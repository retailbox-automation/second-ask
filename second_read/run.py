"""Second Read (v4) harness:  python -m second_read.run freeze | smoke [--n 30] [--split S] | full [--split S] | analyze

freeze   writes data/second_read/FREEZE.json: sha256 of every file under second_read/ (the rule reader included; freeze
         refuses without it), of the Nemotron system prompt, of the dev split file, plus the test seed, strata weights,
         decision rule text, arms with their models and call parameters, and the spend ceiling. Prints the canonical
         digest. Commit the file before any live command.
smoke    the first N lines of a split through every arm (mechanics and cost only: prints no comparison).
full     every line of a split through every arm. The test split must be committed in a commit that descends from the
         FREEZE commit (it is generated only after both readers are frozen).
         smoke and full refuse unless FREEZE.json exists, is committed in git and every recorded value equals the live
         one, and when any SA_* / TF_BASE_URL / TF_CAPS / TF_LEDGER variable is set.
analyze  the pre-registered analysis -> data/second_read/<split>/V4-RESULTS.md and results.json.

Rows go to data/second_read/<split>/rows.jsonl (smoke: smoke-<split>/), one line per (line id, arm), appended as each
read finishes; a rerun skips pairs that already have a row. Spend: one ceiling (prereg.V4_CEILING_USD) for the whole
v4, measured on the shared ledger's b2 tag from the first start of any v4 run (data/second_read/V4_SPEND_START.json);
no new read starts once the spend so far plus that read's worst case would pass it.
"""
import argparse, json, os, subprocess, sys, threading, time
from contextlib import nullcontext
from pathlib import Path

from second_ask import models as M
from second_ask.tf_client import PRICES, BudgetExceeded, Client, Ledger
from second_ask.tournament.manifest import canonical, digest, sha256_file
from second_ask.tournament.runner import env_overrides

from . import analysis as A, cases as C, offer as O, prereg as P
from .readers import nemotron as N

ROOT = Path(__file__).resolve().parents[1]
DATA_REL = "data/second_read"
FREEZE_REL = f"{DATA_REL}/FREEZE.json"
RULES_REL = "second_read/readers/rules.py"
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
NEMOTRON_ARMS = {"super": (M.SUPER, "tool"), "lightning": (M.LIGHTNING, "json_object"), "ultra": (M.ULTRA, "think_tools"),
                 "super_vl": (M.SUPER_VL, "tool")}
JOB_INPUT_CHARS = 6000  # generous size of one read request, for the worst-case cost of a read before it starts


class Refused(RuntimeError):
    pass


def split_path(root, split):
    return Path(root) / DATA_REL / f"{split}.jsonl"


def package_hashes(root):
    base = Path(root) / "second_read"
    files = sorted(p for p in base.rglob("*") if p.is_file() and not (SKIP_PARTS & set(p.parts))
                   and p.suffix not in SKIP_SUFFIXES and p.name != ".DS_Store")
    return {str(p.relative_to(root)): sha256_file(p) for p in files}


def arm_specs():
    out = {"rules": {"reader": "second_read.readers.rules.RuleReader", "model": None}}
    for arm, (model, method) in NEMOTRON_ARMS.items():
        _, params = N.request(method, "")
        out[arm] = {"reader": "second_read.readers.nemotron.NemotronReader", "model": model, "method": method,
                    "params": {k: v for k, v in params.items() if k != "tools"},
                    "tool_schema_sha256": digest(N.TOOL) if "tools" in params else None}
    return out


def freeze_record(root=ROOT):
    root = Path(root)
    if not (root / RULES_REL).exists():
        raise Refused(f"{RULES_REL} does not exist: both readers must be final before the freeze")
    dev = split_path(root, "dev")
    if not dev.exists():
        raise Refused(f"{dev.relative_to(root)} does not exist: the dev split is part of the freeze")
    return {
        "name": "Second Read (v4) freeze",
        "files": package_hashes(root),
        "system_prompt": {"sha256": digest(N.SYSTEM_PROMPT), "chars": len(N.SYSTEM_PROMPT)},
        "dev_data": {"file": str(dev.relative_to(root)), "sha256": sha256_file(dev), "lines": len(C.load(dev))},
        "cards": {"file": str(C.CARDS_FILE.relative_to(C.ROOT)), "sha256": sha256_file(root / "tournament/buyers.json")},
        "test_split": {"seed": P.TEST_SEED, "n": P.TEST_N, "strata_weights": P.STRATA_WEIGHTS, "stop_share": P.STOP_SHARE},
        "decision_rule": {"text": P.DECISION_RULE, "sha256": digest(P.DECISION_RULE), "min_gain_pp": P.MIN_GAIN_PP,
                          "bootstrap": P.BOOTSTRAP, "primary": list(P.PRIMARY)},
        "arms": arm_specs(),
        "spend": {"ledger_tag": P.LEDGER_TAG, "v4_ceiling_usd": P.V4_CEILING_USD, "concurrency": P.CONCURRENCY,
                  "ultra_concurrency": P.ULTRA_CONCURRENCY, "platform_reruns": P.PLATFORM_RERUNS},
    }


def _git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def committed(root, rel):
    """True when `rel` is in a commit and the working copy equals HEAD for it."""
    return bool(_git(root, "log", "--format=%H", "-1", "--", rel)) and not _git(root, "status", "--porcelain", "--", rel)


def first_commit(root, rel):
    out = _git(root, "log", "--diff-filter=A", "--format=%H", "--", rel).splitlines()
    return out[-1] if out else None


def _diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        return out
    return [] if a == b else [path]


def verify_freeze(root=ROOT):
    """(recorded freeze, problems). Problems: missing, uncommitted, or any recorded value != the live one."""
    root = Path(root)
    path = root / FREEZE_REL
    if not path.exists():
        return None, [f"{FREEZE_REL} does not exist: run `python -m second_read.run freeze` and commit it"]
    recorded = json.loads(path.read_text())
    problems = []
    try:
        if not committed(root, FREEZE_REL):
            problems.append(f"{FREEZE_REL} is not committed (or differs from HEAD)")
    except Refused as e:
        problems.append(str(e))
    try:
        live = freeze_record(root)
    except Refused as e:
        return recorded, problems + [str(e)]
    problems += [f"changed since the freeze: {k}" for k in _diff(recorded, live)]
    return recorded, problems


def guard(root=ROOT, environ=None, split=None):
    """The verified freeze, or Refused with every reason."""
    bad = env_overrides(environ)
    if bad:
        raise Refused(f"{', '.join(bad)} set: they would change models, endpoint, caps or ledger behind the freeze")
    recorded, problems = verify_freeze(root)
    if problems:
        raise Refused("; ".join(problems))
    if split == "test":
        rel = f"{DATA_REL}/test.jsonl"
        if not split_path(root, "test").exists():
            raise Refused(f"{rel} does not exist")
        if not committed(root, rel):
            raise Refused(f"{rel} is not committed: the test split is frozen in git before it is read")
        # the commit holding the current freeze must come strictly before the test split first appears
        fz, ts = _git(root, "log", "-1", "--format=%H", "--", FREEZE_REL), first_commit(root, rel)
        if fz == ts or subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", fz, ts]).returncode != 0:
            raise Refused(f"{rel} was not added after the freeze (its first commit {ts[:8]} does not descend from "
                          f"the commit of the current FREEZE {fz[:8]})")
    return recorded


# ---------------------------------------------------------------- running

def make_reader(arm, client):
    if arm == "rules":
        from .readers.rules import RuleReader  # owned by the rule-reader lane; imported only when used
        return RuleReader()
    model, method = NEMOTRON_ARMS[arm]
    return N.NemotronReader(model, method, client=client, name=arm)


def worst_case_usd(arm):
    """Upper bound of one read (both attempts) before it starts, as the client reserves it."""
    if arm not in NEMOTRON_ARMS:
        return 0.0
    model, method = NEMOTRON_ARMS[arm]
    p_in, p_out = PRICES[model]
    _, params = N.request(method, "")
    return 2 * (JOB_INPUT_CHARS / 3 * p_in + params["max_tokens"] * p_out) / 1e6


def read_row(case, arm, reader, split):
    """One (line, arm) row: the read (re-run on a platform error up to PLATFORM_RERUNS times), the offer it gives,
    whether that equals the offer of a perfect read, and the money secondary."""
    card = C.card_for(case)
    runs = []
    for _ in range(1 + P.PLATFORM_RERUNS):
        facts, meta = reader.read(case)
        runs.append(meta)
        if facts is not None or meta.get("error_kind") != "platform":
            break
    failure = None if facts is not None else (meta.get("error_kind") or "invalid")
    gold = O.as_facts(case["gold"], card.estimate())
    gold_offer = O.offer(gold, card, case["today"])
    off = O.offer(facts, card, case["today"]) if facts is not None else None
    return {"id": case["id"], "split": split, "stratum": case["stratum"], "card_id": case["card_id"], "arm": arm,
            "facts": facts.to_dict() if facts is not None else None, "failure": failure, "runs": len(runs),
            "meta": meta, "usd": round(sum(m.get("usd") or 0.0 for m in runs), 6), "seconds": meta.get("seconds"),
            "offer": off, "gold_offer": gold_offer, "match": O.match(off, gold_offer),
            "read_stop": bool(facts.stop) if facts is not None else False, "gold_stop": gold.stop,
            "money": O.money(card, off, gold, case["today"])}


def read_rows(path):
    if not Path(path).exists():
        return []
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def run_lines(jobs, read_one, out, concurrency=P.CONCURRENCY, ultra_concurrency=P.ULTRA_CONCURRENCY,
              can_start=lambda arm: True, log=print):
    """Run (case, arm) jobs not yet in out/rows.jsonl. read_one(case, arm) -> row. Stops starting new jobs when
    can_start(arm) is False (spend ceiling) or on BudgetExceeded; any other exception stops the run and is re-raised."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows_path = out / "rows.jsonl"
    done = {(r["id"], r["arm"]) for r in read_rows(rows_path)}
    todo = [(c, a) for c, a in jobs if (c["id"], a) not in done]
    it, it_lock, write_lock = iter(todo), threading.Lock(), threading.Lock()
    ultra = threading.Semaphore(ultra_concurrency)
    state = {"stop": None, "error": None, "written": 0, "failures": 0}

    def worker():
        while True:
            with it_lock:
                if state["stop"]:
                    return
                job = next(it, None)
                if job is None:
                    return
                if not can_start(job[1]):
                    state["stop"] = "ceiling"
                    return
            case, arm = job
            try:
                with ultra if arm == "ultra" else nullcontext():
                    row = read_one(case, arm)
            except BudgetExceeded as e:
                state["stop"] = f"budget: {e}"
                return
            except Exception as e:  # a bug or bad data: stop, keep what is written, re-raise after the join
                state["stop"], state["error"] = f"error: {type(e).__name__}: {e}", e
                return
            with write_lock:
                with open(rows_path, "a") as f:
                    f.write(json.dumps(row, sort_keys=True) + "\n")
                state["written"] += 1
                state["failures"] += bool(row["failure"])
                if row["failure"]:
                    log(f"[{arm}] {case['id']}: reader failure ({row['failure']}): {row['meta'].get('error')}")

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, concurrency))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if state["error"] is not None:
        raise state["error"]
    total = len(read_rows(rows_path))
    return {"jobs": len(jobs), "already_done": len(jobs) - len(todo), "written": state["written"],
            "reader_failures_written": state["failures"], "rows_total": total,
            "stop": state["stop"] or ("complete" if total >= len({(c["id"], a) for c, a in jobs}) else "incomplete")}


def spend_tracker(root, ledger):
    """Spend on the ledger's b2 tag since the first start of any v4 run (the mark is written once and kept)."""
    mark = Path(root) / DATA_REL / "V4_SPEND_START.json"
    if mark.exists():
        start = json.loads(mark.read_text())["usd_at_start"]
    else:
        start = ledger.spent()[1].get(P.LEDGER_TAG, 0.0)
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(json.dumps({"ledger_tag": P.LEDGER_TAG, "usd_at_start": start, "ledger": str(ledger.path),
                                    "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=1) + "\n")
    return lambda: ledger.spent()[1].get(P.LEDGER_TAG, 0.0) - start


def run_state(out, freeze_digest, split_file):
    """Bind a run directory to one freeze and one split file; resuming under anything else is refused."""
    st = Path(out) / "run_state.json"
    cur = {"freeze": freeze_digest, "split_sha256": sha256_file(split_file)}
    if st.exists():
        old = json.loads(st.read_text())
        if {k: old.get(k) for k in cur} != cur:
            raise Refused(f"{out} was started under {({k: old.get(k) for k in cur})}, not {cur}; use a new directory")
    else:
        st.parent.mkdir(parents=True, exist_ok=True)
        st.write_text(json.dumps(dict(cur, started=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())), indent=1) + "\n")


def live(root, split, out_name, n=None, environ=None, log=print):
    recorded = guard(root, environ, split)
    fdigest = digest(recorded)
    path = split_path(root, split)
    cases = C.load(path)[:n] if n else C.load(path)
    if not os.environ.get("NEBIUS_API_KEY"):
        raise Refused("NEBIUS_API_KEY is not set")
    out = Path(root) / DATA_REL / out_name
    run_state(out, fdigest, path)
    ledger = Ledger()
    client = Client(P.LEDGER_TAG, ledger=ledger, fallbacks={})
    readers = {arm: make_reader(arm, client) for arm in P.ARMS}
    spent = spend_tracker(root, ledger)
    can_start = lambda arm: spent() + worst_case_usd(arm) <= P.V4_CEILING_USD  # noqa: E731
    jobs = [(c, a) for c in cases for a in P.ARMS]
    log(f"freeze {fdigest}; {len(cases)} lines x {len(P.ARMS)} arms; v4 spend so far ${spent():.4f} of "
        f"${P.V4_CEILING_USD:.2f}")
    report = run_lines(jobs, lambda c, a: read_row(c, a, readers[a], split), out, can_start=can_start, log=log)
    report["v4_spend_usd"] = round(spent(), 4)
    return report, out


def mechanics(rows):
    """Smoke summary: rows, failures and cost per arm. No match rates: smoke is mechanics and cost only."""
    out = {}
    for r in rows:
        s = out.setdefault(r["arm"], {"rows": 0, "reader_failures": 0, "usd": 0.0})
        s["rows"] += 1
        s["reader_failures"] += bool(r["failure"])
        s["usd"] = round(s["usd"] + (r.get("usd") or 0.0), 6)
    return out


def cmd_freeze(root=ROOT, log=print):
    rec = freeze_record(root)
    path = Path(root) / FREEZE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    d = digest(rec)
    log(f"{FREEZE_REL}: {len(rec['files'])} files, canonical digest {d}")
    log("commit it before any live command")
    return d


def cmd_analyze(root, split, log=print):
    path = split_path(root, split)
    out = Path(root) / DATA_REL / split
    rows = read_rows(out / "rows.jsonl")
    if not rows:
        raise Refused(f"no rows in {out / 'rows.jsonl'}")
    res = A.analyze(C.load(path), rows, split)
    fz = Path(root) / FREEZE_REL
    res["freeze_digest"] = digest(json.loads(fz.read_text())) if fz.exists() else None
    res["split_sha256"] = sha256_file(path)
    (out / "results.json").write_text(json.dumps(res, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    (out / "V4-RESULTS.md").write_text(A.markdown(res) + "\n")
    log(f"{out / 'V4-RESULTS.md'}: verdict {res['verdict'] or 'withheld'}")
    return res


def main(argv=None, root=ROOT, environ=None):
    ap = argparse.ArgumentParser(prog="python -m second_read.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("freeze")
    s = sub.add_parser("smoke")
    s.add_argument("--n", type=int, default=P.SMOKE_N)
    s.add_argument("--split", default="test", choices=("dev", "test"))
    f = sub.add_parser("full")
    f.add_argument("--split", default="test", choices=("dev", "test"))
    a = sub.add_parser("analyze")
    a.add_argument("--split", default="test", choices=("dev", "test"))
    args = ap.parse_args(argv)
    try:
        if args.cmd == "freeze":
            cmd_freeze(root)
            return 0
        if args.cmd == "analyze":
            cmd_analyze(root, args.split)
            return 0
        if args.cmd == "smoke":
            report, out = live(root, args.split, f"smoke-{args.split}", n=args.n, environ=environ)
            print("SMOKE (mechanics and cost only, no comparison):")
            print(json.dumps(mechanics(read_rows(out / "rows.jsonl")), indent=1))
        else:
            report, out = live(root, args.split, args.split, environ=environ)
        print(json.dumps(report, indent=1))
        return 0 if report["stop"] == "complete" else 3
    except Refused as e:
        print(f"refusing: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
