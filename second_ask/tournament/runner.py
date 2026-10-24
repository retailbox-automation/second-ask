"""Phase B runner: one line per finished conversation, resumable, paired, with a cost checkpoint.

- Jobs run buyer by buyer (every policy and repeat of one buyer before the next buyer), at most CONCURRENCY at a time
  and at most ULTRA_CONCURRENCY Ultra conversations at a time, so a run that stops early leaves whole buyers plus a few
  partial ones; results keep only buyers finished in every policy.
- Each finished conversation is appended to outcomes.jsonl (with every step) and rows.jsonl (scored) and fsynced before
  the next result is handled. A restart skips every (buyer, policy, repeat) that already has a clean row.
- A conversation that raises becomes an "errored" row. A conversation is clean when it did not raise, had no platform
  failure and made no off-arm call. One that is not clean is re-run up to RERUNS more times; if no attempt is clean, the
  buyer is excluded from every policy (paired) and its remaining conversations are skipped.
- After CHECKPOINT_BUYERS buyers are finished, the cost of the whole run is projected from what it has spent; above the
  limit the runner starts nothing new. A BudgetExceeded from the ledger stops the run the same way: started
  conversations finish, the covered cells are reported, nothing is lost.
"""
import json, os, statistics, threading, time
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict
from pathlib import Path

from ..tf_client import BudgetExceeded, TFError, _terminate_last_line
from . import scoring as S

CONCURRENCY = 8
ULTRA_CONCURRENCY = 3            # Ultra: 200K tokens per minute and up to 16K output tokens per call
ULTRA_POLICIES = ("ultra_only",)
RERUNS = 2
CHECKPOINT_BUYERS = 20           # about 10 % of 200 buyers
CHECKPOINT_SAFETY = 1.2          # on the projected rest of the run
RUN_CEILING_USD = 12.0           # full run (decision 2026-10-24); also capped by what is left of the b2 cap
SMOKE_CEILING_USD = 1.0
ENV_REFUSED = ("TF_BASE_URL", "TF_CAPS", "TF_LEDGER")  # plus every SA_* variable


class PlatformFailure(RuntimeError):
    """Token Factory failed after the client's retries. Raised past the agent's own error handling in the tournament."""


class StrictClient:
    """The tournament's view of the client: a TFError is not turned into a code-default move or a template buyer
    message (as the product does); it aborts the conversation, which becomes an errored row and is re-run."""

    def __init__(self, client):
        self.client = client

    def chat(self, model, messages, **params):
        try:
            return self.client.chat(model, messages, **params)
        except TFError as e:
            raise PlatformFailure(str(e)) from e


def env_overrides(environ=None):
    """Environment variables that would change models, endpoint, caps or ledger behind the manifest's back."""
    environ = os.environ if environ is None else environ
    return sorted(k for k in environ if k.startswith("SA_") or k in ENV_REFUSED)


def plan(card_ids, policies, repeated, k, subsample):
    """Ordered jobs (buyer, policy, repeat): buyer by buyer; repeats 1..k-1 only for `repeated` policies on `subsample`."""
    sub = set(card_ids) if subsample == "all" else set(subsample)
    return [(b, p, rep) for b in card_ids for rep in range(k) for p in policies
            if rep == 0 or (p in repeated and b in sub)]


def smoke_plan(cards, repeat_policies, per_type_policies, per_type=2):
    """Pre-registered smoke (decision 2026-10-24): the 10 phase-A conversations again (the first clear price buyer and
    the first family buyer, `repeat_policies`), plus the first `per_type` other buyers of every type on
    `per_type_policies`."""
    old = [next(c for c in cards if c.type == "price" and c.clarity == "clear"), next(c for c in cards if c.type == "family")]
    jobs = [(c.id, p, 0) for c in old for p in repeat_policies]
    seen = {c.id for c in old}
    for typ in dict.fromkeys(c.type for c in cards):
        for c in [c for c in cards if c.type == typ and c.id not in seen][:per_type]:
            jobs += [(c.id, p, 0) for p in per_type_policies]
    return jobs


def key(row):
    return row["buyer"], row["policy"], row["repeat"]


def is_clean(row):
    return row.get("status") == "ok" and not row.get("platform_failures") and not row.get("off_arm_calls")


def append_line(path, obj):
    """Append one JSON line and fsync it (a line cut off by a killed run is closed first, not glued to this one)."""
    with open(path, "a+") as f:
        _terminate_last_line(f)
        f.write(json.dumps(obj) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_lines(path):
    """(records, unreadable line count); a missing file is empty."""
    out, bad = [], 0
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    bad += 1
    return out, bad


def state(rows, jobs, reruns=RERUNS):
    """(clean keys, attempts per key, excluded buyers, pending jobs in plan order)."""
    attempts = Counter(key(r) for r in rows)
    clean = {key(r) for r in rows if is_clean(r)}
    excluded = {k[0] for k, n in attempts.items() if n >= 1 + reruns and k not in clean}
    pending = [j for j in jobs if j not in clean and j[0] not in excluded]
    return clean, attempts, excluded, pending


def finished_buyers(jobs, clean, excluded):
    by = {}
    for j in jobs:
        by.setdefault(j[0], []).append(j)
    return [b for b, js in by.items() if b in excluded or all(j in clean for j in js)]


def errored_row(card, job, attempt, error):
    b, p, rep = job
    return {"buyer": b, "type": card.type, "trade": card.trade, "policy": p, "repeat": rep, "attempt": attempt,
            "status": "errored", "error": f"{type(error).__name__}: {error}"[:300],
            "platform_failures": int(isinstance(error, PlatformFailure)), "off_arm_calls": 0}


def project(rows, pending, spent_usd, safety):
    """Projected cost of the whole run: spent so far + safety x (each pending job x its policy's mean $ per clean
    conversation, shop and buyer calls together). A policy with no clean conversation yet takes the dearest known."""
    per = {}
    for r in rows:
        if is_clean(r):
            per.setdefault(r["policy"], []).append((r.get("model_usd") or 0.0) + (r.get("buyer_usd") or 0.0))
    mean = {p: statistics.mean(v) for p, v in per.items()}
    dearest = max(mean.values(), default=0.0)
    rest = sum(mean.get(p, dearest) for _, p, _ in pending)
    return {"spent_usd": round(spent_usd, 4), "rest_usd": round(rest, 4), "safety": safety,
            "projected_total_usd": round(spent_usd + safety * rest, 4), "usd_per_conversation": mean,
            "pending_jobs": len(pending)}


def run(jobs, cards, converse, out_dir, *, concurrency=CONCURRENCY, ultra_concurrency=ULTRA_CONCURRENCY,
        reruns=RERUNS, checkpoint=None, ceiling_usd=None, spent=lambda: 0.0, log=print):
    """Run `jobs` ((buyer, policy, repeat), in order) with converse(card, policy, repeat) -> env.Outcome.

    checkpoint: None, or {"after_buyers", "safety", "limit_usd", "accept_failed": bool}. ceiling_usd: start nothing new
    once spent() is above it. spent(): dollars this run has spent so far (the caller reads the ledger).
    Returns the run report, also written to run_report.json."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows_p, outs_p, cp_p = out / "rows.jsonl", out / "outcomes.jsonl", out / "checkpoint.json"
    rows, bad = read_lines(rows_p)
    clean, attempts, excluded, pending = state(rows, jobs, reruns)
    stop = None
    cp_done = checkpoint is None
    if checkpoint is not None and cp_p.exists():
        cp = json.loads(cp_p.read_text())
        cp_done = cp.get("passed") or checkpoint.get("accept_failed", False)
        if not cp_done:
            stop = f"checkpoint failed earlier (projected ${cp['projected_total_usd']} > limit ${cp['limit_usd']})"
    queue = deque(pending)
    ultra = threading.BoundedSemaphore(ultra_concurrency)
    in_flight = {}

    def work(job):
        b, p, rep = job
        if p in ULTRA_POLICIES:
            with ultra:
                return converse(cards[b], p, rep)
        return converse(cards[b], p, rep)

    def record(job, fut):
        nonlocal stop
        b, p, rep = job
        attempt = attempts[job] + 1
        try:
            outcome = fut.result()
        except BudgetExceeded as e:  # no row: the conversation is unfinished and runs again on a resume
            stop = stop or f"budget cap: {e}"
            log(f"[run] {b} {p} r{rep}: budget cap reached, stopping")
            return
        except Exception as e:  # noqa: BLE001 - one conversation's failure must not take the run down
            row = errored_row(cards[b], job, attempt, e)
        else:
            row = S.score(cards[b], outcome) | {"attempt": attempt, "status": "ok"}
            append_line(outs_p, asdict(outcome) | {"attempt": attempt})
        append_line(rows_p, row)
        rows.append(row)
        attempts[job] = attempt
        if is_clean(row):
            clean.add(job)
        elif attempt < 1 + reruns:
            queue.appendleft(job)  # re-run next, keeping buyer order
        else:
            excluded.add(b)
            log(f"[run] {b} excluded from every policy: {p} r{rep} not clean after {attempt} attempts")
        log(f"[run] {b} {p} r{rep} a{attempt}: {row['status']}"
            + (f" booked={row.get('booked')} end={row.get('end')} ${row.get('model_usd', 0):.5f}" if row["status"] == "ok"
               else f" {row['error'][:120]}"))

    def check():
        nonlocal stop, cp_done
        done = finished_buyers(jobs, clean, excluded)
        if cp_done or stop or len(done) < checkpoint["after_buyers"]:
            return
        rest = [j for j in jobs if j not in clean and j[0] not in excluded]
        cp = project(rows, rest, spent(), checkpoint["safety"]) | {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "finished_buyers": len(done),
            "limit_usd": checkpoint["limit_usd"]}
        cp["passed"] = cp["projected_total_usd"] <= checkpoint["limit_usd"]
        cp_p.write_text(json.dumps(cp, indent=1) + "\n")
        cp_done = True
        log(f"[run] checkpoint after {len(done)} buyers: projected ${cp['projected_total_usd']:.2f} "
            f"vs limit ${checkpoint['limit_usd']:.2f} -> {'continue' if cp['passed'] else 'STOP'}")
        if not cp["passed"]:
            stop = f"checkpoint: projected ${cp['projected_total_usd']:.2f} > limit ${checkpoint['limit_usd']:.2f}"

    with ThreadPoolExecutor(concurrency) as ex:
        while True:
            try:
                while stop is None and queue and len(in_flight) < concurrency:
                    if ceiling_usd is not None and spent() > ceiling_usd:
                        stop = f"run ceiling ${ceiling_usd:.2f} reached (spent ${spent():.4f})"
                        break
                    job = queue.popleft()
                    if job[0] in excluded or job in clean:
                        continue
                    in_flight[ex.submit(work, job)] = job
                if not in_flight:
                    break
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for fut in done:
                    record(in_flight.pop(fut), fut)
                if checkpoint is not None:
                    check()
            except KeyboardInterrupt:
                stop = stop or "interrupted"
                log("[run] interrupted: finishing the conversations already started")
    _, _, excluded, pending = state(rows, jobs, reruns)
    done = finished_buyers(jobs, {key(r) for r in rows if is_clean(r)}, excluded)
    report = {"stop": stop or "complete", "jobs": len(jobs), "clean_rows": sum(map(is_clean, rows)),
              "errored_rows": sum(r.get("status") == "errored" for r in rows), "rows": len(rows),
              "finished_buyers": len(done), "excluded_buyers": sorted(excluded), "pending_jobs": len(pending),
              "clean_by_policy": dict(Counter(r["policy"] for r in rows if is_clean(r))),
              "unreadable_row_lines": bad, "spent_usd": round(spent(), 4)}
    (out / "run_report.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def final_rows(rows, jobs, reruns=RERUNS):
    """The rows results are computed from: the first clean row of every planned conversation, for buyers finished in
    every policy and not excluded. Plus what was dropped and why."""
    clean, attempts, excluded, _ = state(rows, jobs, reruns)
    planned = {}
    for j in jobs:
        planned.setdefault(j[0], []).append(j)
    complete = {b for b, js in planned.items() if b not in excluded and all(j in clean for j in js)}
    first = {}
    for r in rows:
        if is_clean(r) and key(r) not in first:
            first[key(r)] = r
    keep = [first[j] for b in planned if b in complete for j in planned[b]]
    return keep, {"buyers_planned": len(planned), "buyers_kept": len(complete), "excluded": sorted(excluded),
                  "incomplete": sorted(b for b in planned if b not in complete and b not in excluded),
                  "errored_rows": sum(r.get("status") == "errored" for r in rows),
                  "reruns": sum(n - 1 for n in attempts.values() if n > 1)}
