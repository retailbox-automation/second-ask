"""Checks on a tournament run directory, outside the hashed package (second_ask/** is frozen by the manifest).

    python3 tools/run_checks.py smoke tournament/smoke-b      every smoke gate (b2-recheck watch_in_smoke); exit 1 on a fail
    python3 tools/run_checks.py ledger tournament/full        429/5xx and kept reservations in the spend ledger since the run began
    python3 tools/run_checks.py after tournament/full         post-run items: false opt-outs, exclusions by type, outcome duplicates
    python3 tools/run_checks.py results-md tournament/full    RESULTS.md from results.json, rows, outcomes, smoke-b, manifest
    python3 tools/run_checks.py watch tournament/full PID     stop rule of the full run (DECISIONS 2026-10-24 10:55): SIGINT to PID
                                                              when > 5 % of buyers are excluded or a policy has > 10 % errored rows,
                                                              or at once when a conversation failed on HTTP 429 after its retries
Reads rows.jsonl, outcomes.jsonl, run_state.json and run_report.json only, and the shared ledger read-only.
"""
import json, os, signal, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from second_ask.compliance import opt_out_status  # noqa: E402
from second_ask.tournament import env as E, runner as R, scoring as S  # noqa: E402

LEDGER = Path("~/.local/state/nebius-hackathon/tf-ledger.jsonl").expanduser()
CODE_TEXTS = {"We talked it over. Let's book it."}  # customer lines written by code, not the buyer model
# How a conversation can end on an opt-out (second_ask/agent.py _stop reasons, compliance gate). Matched by reason, not by
# one substring: a check for "opted out" alone missed the label-model path (B139, results audit 24.10).
OPT_OUT_STOPS = (("label model read an opt-out", "label model"), ("opt-out keyword", "code layer (agent)"),
                 ("recipient opted out", "code layer"))


def opt_out_stop(end):
    """Which opt-out path ended the conversation, or None."""
    return next((how for key, how in OPT_OUT_STOPS if key in (end or "")), None)


def assert_opt_out_paths_known():
    """Every opt-out stop reason in agent.py must be recognised here (fails loudly when a new path appears)."""
    import re
    src = (ROOT / "second_ask" / "agent.py").read_text()
    reasons = [r for r in re.findall(r'_stop\("([^"]+)"', src) if "opt" in r]
    unknown = [r for r in reasons if opt_out_stop("policy stopped: " + r) is None]
    assert reasons and not unknown, f"opt-out stop reasons not recognised: {unknown}"
    return reasons


def false_opt_out_rows(rows):
    """Clean rows of buyers who are not opt-out buyers that still ended on an opt-out, with the path."""
    assert_opt_out_paths_known()
    return [dict(r, how=opt_out_stop(r.get("end"))) for r in rows
            if r.get("status") == "ok" and r["type"] != "opt_out" and opt_out_stop(r.get("end"))]


def load(d):
    d = Path(d)
    rows, bad_r = R.read_lines(d / "rows.jsonl")
    outs, bad_o = R.read_lines(d / "outcomes.jsonl")
    state = json.loads((d / "run_state.json").read_text()) if (d / "run_state.json").exists() else {}
    report = json.loads((d / "run_report.json").read_text()) if (d / "run_report.json").exists() else {}
    return rows, outs, state, report, bad_r + bad_o


def ledger_since(started_iso):
    t0 = (datetime.strptime(started_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
          if started_iso else 0)
    out = []
    for line in LEDGER.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ts", 0) >= t0 and r.get("tag") == "b2":
            out.append(r)
    return out


def ledger_summary(started_iso):
    """Releases (error replies) by model and HTTP code; per model, reserves minus settled or released (kept
    reservations: network failures, malformed replies, replies without usage, or calls still in flight)."""
    recs = ledger_since(started_iso)
    releases, res, done = Counter(), Counter(), Counter()
    for r in recs:
        note = r.get("note", "")
        kind, _, rest = note.partition(" ")
        model = rest.split(" ")[0]
        if kind == "release":
            releases[(model, rest.split("HTTP ")[-1])] += 1
            done[model] += 1
        elif kind == "reserve":
            res[model] += 1
        elif kind == "settle" or note.startswith("no usage"):
            done[note.split(" ")[3] if note.startswith("no usage") else model] += 1
    kept = {m: res[m] - done[m] for m in res if res[m] != done[m]}
    return {"records": len(recs), "usd": round(sum(r["usd"] for r in recs), 6),
            "releases": {f"{m} HTTP {c}": n for (m, c), n in sorted(releases.items())}, "kept_reservations": kept,
            "calls_by_model": dict(res)}


def customer_lines(o):
    """(text, written_by_model) for every customer line of a conversation."""
    for i, e in enumerate(o["transcript"]):
        if e["who"] != "customer":
            continue
        by_code = e["text"] in CODE_TEXTS or (o["type"] == "opt_out" and i == 0)
        yield e["text"], not by_code


def false_opt_outs(outs):
    hits, ambiguous, n = [], 0, 0
    for o in outs:
        if o["type"] == "opt_out":
            continue
        for text, _ in customer_lines(o):
            n += 1
            s = opt_out_status(text)
            if s == "opt_out":
                hits.append({"buyer": o["buyer"], "policy": o["policy"], "repeat": o["repeat"], "text": text})
            ambiguous += s == "ambiguous"
    return {"customer_lines_checked": n, "opt_out": hits, "ambiguous": ambiguous}


def outcome_duplicates(outs):
    c = Counter((o["buyer"], o["policy"], o["repeat"], o.get("attempt")) for o in outs)
    return [list(k) for k, n in c.items() if n > 1]


def gate(name, ok, detail):
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}")
    return ok


def cmd_smoke(d, expected=38):
    rows, outs, state, report, bad = load(d)
    ok = True
    ok &= gate("run_report", report.get("stop") == "complete" and report.get("rows") == expected
               and report.get("clean_rows") == expected and report.get("errored_rows") == 0
               and report.get("excluded_buyers") == [], json.dumps({k: report.get(k) for k in
                                                                    ("stop", "rows", "clean_rows", "errored_rows",
                                                                     "excluded_buyers", "spent_usd")}))
    ok &= gate("one attempt each", all(r.get("attempt") == 1 for r in rows) and len({R.key(r) for r in rows}) == len(rows),
               f"attempts {Counter(r.get('attempt') for r in rows)}; unreadable lines {bad}")
    ledg = ledger_summary(state.get("started"))
    errs = {k: v for k, v in ledg["releases"].items() if "HTTP 429" in k or "HTTP 5" in k}
    failed_429 = [r for r in rows if r.get("status") == "errored" and "429" in r.get("error", "")]
    ok &= gate("ledger window sees this run", ledg["records"] > 0, f"{ledg['records']} b2 ledger records since start, "
               f"${ledg['usd']} net (run_report says ${report.get('spent_usd')})")
    ok &= gate("no conversation failed on 429", not failed_429, f"releases since start {ledg['releases'] or 0}; "
               f"429/5xx {errs or 0}; kept reservations {ledg['kept_reservations'] or 0}")
    fo = false_opt_outs(outs)
    ended = [(r["buyer"], r["policy"], r["end"]) for r in false_opt_out_rows(rows)]
    ok &= gate("no false opt-out", not fo["opt_out"] and not ended,
               f"{fo['customer_lines_checked']} customer lines checked, opt_out {fo['opt_out']}, ambiguous "
               f"{fo['ambiguous']}; rows ending 'opted out' with type != opt_out: {ended}")
    watch = [o for o in outs if o["buyer"] in ("B010", "B013", "B011", "B016")]
    print("      risky types:", [(o["buyer"], o["policy"], o["end"]) for o in watch])
    oo = [o for o in outs if o["type"] == "opt_out"]
    verbatim = all(o["transcript"][0]["text"] in E.OPT_OUT_KEYWORDS + E.OPT_OUT_PHRASES for o in oo)
    ok &= gate("code opt-outs", bool(oo) and verbatim and all(o["violations"] == 0 and o["moves"] == 0 for o in oo),
               [(o["buyer"], o["policy"], o["transcript"][0]["text"], o["violations"], o["end"]) for o in oo])
    bad_rows = [R.key(r) for r in rows if r.get("off_arm_calls") or r.get("platform_failures") or r.get("chronology_errors")
                or r.get("above_walkaway") or r.get("below_floor")]
    served = [(o["buyer"], o["policy"], s["step"], s["model"], s.get("served_model")) for o in outs for s in o["steps"]
              if s.get("served_model") and s["served_model"] != s["model"]]
    ok &= gate("integrity", not bad_rows and not served, f"rows with a counter or gate lit {bad_rows}; served != asked {served}")
    model_lines = sum(m for o in outs for _, m in customer_lines(o))
    fb = sum(o["writer_fallbacks"] for o in outs)
    share = fb / model_lines if model_lines else 0.0
    lfb = {p: sum(o["label_fallback_calls"] for o in outs if o["policy"] == p) for p in sorted({o["policy"] for o in outs})}
    ok &= gate("writer template share <= 10 %", share <= 0.10, f"writer_fallbacks {fb} of {model_lines} model-written "
               f"customer lines = {share:.1%}; label_fallback_calls by policy {lfb}")
    print("SMOKE GATES:", "ALL PASS" if ok else "SOME FAIL")
    return 0 if ok else 1


def cmd_ledger(d):
    _, _, state, _, _ = load(d)
    print(json.dumps(ledger_summary(state.get("started")), indent=1))
    return 0


def cmd_after(d):
    rows, outs, state, report, bad = load(d)
    ended = defaultdict(int)
    for r in false_opt_out_rows(rows):
        ended[f"{r['policy']} ({r['how']})"] += 1
    excluded = sorted(set(report.get("excluded_buyers", [])))
    types = {r["buyer"]: r["type"] for r in rows}
    out = {"false_opt_out_lines": false_opt_outs(outs), "rows_type_not_opt_out_ending_opted_out": dict(ended),
           "excluded_by_type": dict(Counter(types.get(b) for b in excluded)), "outcome_duplicates": outcome_duplicates(outs),
           "errored_by_policy": dict(Counter(r["policy"] for r in rows if r.get("status") == "errored")),
           "errors_429": sum("429" in (r.get("error") or "") for r in rows), "ledger": ledger_summary(state.get("started")),
           "unreadable_lines": bad}
    print(json.dumps(out, indent=1))
    return 0


def stop_reason(rows, n_buyers):
    """The full-run stop rule (DECISIONS 2026-10-24 10:55), or None. Fractions need a base: errored share per policy once
    the policy has >= 10 rows; excluded share of buyers started once >= 20 buyers started (or > 5 % of all buyers)."""
    for r in rows:
        if r.get("status") == "errored" and "429" in (r.get("error") or "") and r.get("attempt", 1) >= 1:
            return f"conversation failed on HTTP 429 after its retries: {R.key(r)} attempt {r.get('attempt')}"
    attempts = Counter(R.key(r) for r in rows)
    clean = {R.key(r) for r in rows if R.is_clean(r)}
    excluded = {k[0] for k, n in attempts.items() if n >= 1 + R.RERUNS and k not in clean}
    started = {r["buyer"] for r in rows}
    if len(excluded) > 0.05 * n_buyers or (len(started) >= 20 and len(excluded) > 0.05 * len(started)):
        return f"{len(excluded)} buyers excluded of {len(started)} started"
    by = defaultdict(list)
    for r in rows:
        by[r["policy"]].append(r.get("status") == "errored")
    for p, v in by.items():
        if len(v) >= 10 and sum(v) > 0.10 * len(v):
            return f"{p}: {sum(v)} errored of {len(v)} rows"
    return None


def cmd_watch(d, pid, n_buyers=200, every=30):
    """Pulse line every tick; SIGINT to the runner on the stop rule; exits when the runner is gone."""
    d, pid, fails = Path(d), int(pid), 0
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print(f"{time.strftime('%H:%M:%SZ', time.gmtime())} runner {pid} has exited", flush=True)
            return 0
        try:
            rows, _ = R.read_lines(d / "rows.jsonl")
            why = stop_reason(rows, n_buyers)
            fails = 0
        except Exception as e:  # noqa: BLE001 - a broken tick is reported, three in a row end the watch
            fails += 1
            print(f"{time.strftime('%H:%M:%SZ', time.gmtime())} tick error {fails}: {type(e).__name__}: {e}", flush=True)
            if fails >= 3:
                return 2
            time.sleep(every)
            continue
        print(f"{time.strftime('%H:%M:%SZ', time.gmtime())} rows={len(rows)} errored="
              f"{sum(r.get('status') == 'errored' for r in rows)} stop_rule={'none' if why is None else why}", flush=True)
        if why:
            os.kill(pid, signal.SIGINT)
            print(f"SIGINT sent to {pid}: {why}", flush=True)
            return 3
        time.sleep(every)


def _usd(x, d=0):
    return f"${x:,.{d}f}" if x >= 0 else f"-${-x:,.{d}f}"


def _ci(c):
    return f"{_ci_s(c)}"


def _verdict(c):
    if c["lo"] > 0:
        return "ahead (interval above 0)"
    if c["hi"] < 0:
        return "behind (interval below 0)"
    return "no clear difference (interval includes 0)"


def _git(*args):
    import subprocess
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _sd(x, d=0):
    """Signed $ for a difference: +$1,234 / -$1,234; cents when it is under a dollar but not zero."""
    if 0 < abs(x) < 1:
        d = 2
    if round(x, d) == 0:
        return "$0"
    return ("+" if x > 0 else "") + _usd(x, d)


def _ci_s(c, n=1):
    return f"{_sd(c['point'] / n)} [{_sd(c['lo'] / n)}, {_sd(c['hi'] / n)}]"


def _per(c, n=None):
    n = n or c["buyers"]
    return f"{_ci_s(c, n)} per customer"


def _reading(c, n=None):
    n = n or c["buyers"]
    if c["hi"] < 0:
        return "behind: the whole interval is below 0"
    if c["lo"] > 0:
        return "ahead: the whole interval is above 0"
    return (f"no clear difference, which is not equivalence: from {_usd(-c['lo'] / n)} per customer behind to "
            f"{_usd(c['hi'] / n)} ahead is inside the interval")


def cmd_results_md(d, out_path=None):
    """RESULTS.md, every number computed here from the run files (rows, outcomes, results.json, smoke-b, manifest,
    buyers, ledger, git). The wording around the numbers is fixed in this function, written after the run."""
    from second_ask.tournament import run as RUN
    d = Path(d).resolve()
    res = json.loads((d / "results.json").read_text())
    tables = (d / "RESULTS-tables.md").read_text()
    sections = {t.split("\n", 1)[0]: "## " + t for t in tables.split("\n## ")[1:]}
    rows, outs, state, report, _ = load(d)
    m = json.loads((ROOT / "tournament" / "MANIFEST.json").read_text())
    sha = (ROOT / "tournament" / "MANIFEST.sha256").read_text().split()[0]
    file_sha = __import__("hashlib").sha256((ROOT / "tournament" / "MANIFEST.json").read_bytes()).hexdigest()
    jobs = RUN.manifest_jobs(m)
    final, _ = R.final_rows(rows, jobs)
    agg, comps = res["aggregate"], res["comparisons"]
    n = agg["routed"]["n"]
    cards = {c["id"]: c for c in json.loads((ROOT / "tournament" / "buyers.json").read_text())}
    cases = json.loads((ROOT / "seeds" / "home_cases.json").read_text())["cases"]
    oracle = {r["buyer"]: r["oracle"] for r in final}
    feasible = {r["buyer"] for r in final if r["feasible"]}
    orc = sum(oracle.values())
    freeze = _git("log", "-1", "--format=%h", "--", "tournament/MANIFEST.sha256")
    freeze_t = _git("log", "-1", "--format=%cI", freeze)
    pushes = [l for l in _git("reflog", "show", "--date=iso-strict", "refs/remotes/origin/lane-b2").splitlines()
              if l.startswith(freeze + " ")]
    push_t = pushes[-1].split("@{", 1)[1].split("}", 1)[0] if pushes else "not found in the reflog"
    gen = _git("log", "--diff-filter=A", "--format=%h %cI", "--", "tools/run_checks.py").split("\n")[-1]
    ended = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((d / "run_report.json").stat().st_mtime))
    utc = lambda iso: datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    main_r, main_s = comps["routed - smart_rules"], comps["super_only - smart_rules"]
    lab = res["label_default"]
    lab_c = lab["ci_routed_minus_super_only"]
    ultra_super = S.paired_bootstrap(final, "ultra_only", "super_only")
    fo_rows = false_opt_out_rows(rows)
    fo_lines = false_opt_outs(outs)
    by_key = {(o["buyer"], o["policy"], o["repeat"], o.get("attempt")): o for o in outs}
    vague = sum(c["clarity"] == "vague" for c in cards.values())
    seeded = sum(c["archetype"] != "generic" for c in cards.values())
    mapped = sum("mapped" in c["trade"] for c in cases)
    cells = {(r["type"], r["trade"]) for r in final}
    cell_n = Counter((cards[b]["type"], cards[b]["trade"]) for b in cards)
    testable = sum(cell_n[c] >= S.MIN_CELL_N for c in cells)
    ledg = ledger_summary(state.get("started"))
    note = "simulated margin on illustrative prices (not market data)"
    L = ["# Second Ask: tournament results", "",
         f"**Second Ask, measured: in our pre-registered test, Nemotron did not beat a good rulebook. Against "
         f"`smart_rules`, a rulebook we wrote with full knowledge of the simulator, the routed design (Lightning labels, "
         f"Super chooses) came out at {_ci_s(main_r)} against it over {n} simulated customers ({_per(main_r, n)}): "
         f"behind. Super-only: {_ci_s(main_s)} ({_per(main_s, n)}): no clear difference. The test changed what "
         f"we ship: {lab['default_label_model']} labels by default. All $ are {note}.**", "",
         f"Pre-registered manifest `tournament/MANIFEST.json`: sha256 `{sha}` of its canonical JSON (sorted keys, no "
         f"spaces; the file's own sha256 is `{file_sha[:16]}…`). Frozen in commit `{freeze}` at {utc(freeze_t)}, pushed "
         f"{utc(push_t) if 'T' in push_t else push_t}; the run started {state.get('started')} and finished {ended}. "
         f"`second_ask/**` and the manifest did not change after the freeze. Run: {report.get('rows')} conversation rows "
         f"({report.get('clean_rows')} clean, {report.get('errored_rows')} errored and re-run clean), real Token Factory "
         f"spend {_usd(ledg['usd'], 2)}.", "",
         "Numbers on this page are computed from the run files by `python3 -m second_ask.tournament.run results` (the "
         f"pre-registered tables) and `tools/run_checks.py results-md` (this page). That generator is not in the "
         f"manifest and was first committed in `{gen.split()[0]}` at {utc(gen.split()[1])}, after the run: the numbers "
         "are computed, but the presentation and wording were chosen with the results known.", "",
         "## Main comparison (pre-registered): Nemotron against a good rulebook", "",
         f"| comparison: net, {note} | over {n} customers [95 % interval] | per customer | reading |", "|---|---|---|---|"]
    for c, name in ((main_r, "routed - smart_rules"), (main_s, "super_only - smart_rules")):
        L.append(f"| {name} | {_ci_s(c)} | {_per(c, n)} | {_reading(c, n)} |")
    L += ["", f"Read this with how `smart_rules` was made. We wrote it with full knowledge of the simulator's vocabulary and "
          f"decision code: asking why turns the {vague} customers who start vague into clear ones, who then state amounts, "
          f"weeks and deadlines in the simulator's phrasing, and an amount a customer names is 88–97 % of their hidden "
          f"walkaway (`second_ask/tournament/buyers.py`). It takes {agg['smart_rules']['net'] / orc:.1%} of the oracle "
          f"margin ({_usd(orc)} over {len(feasible)} feasible customers), so any policy has little room above it here. This "
          "favours the rulebook in this simulator and says nothing about live customers. Paired bootstrap over "
          "customers, 2,000 draws, seed 7.", "",
          "## What the test changed: the default label model", "",
          f"The product as first designed (routed: Lightning labels, Super chooses) finished behind the rulebook and failed "
          f"the pre-registered label rule against super_only: routed - super_only {_ci_s(lab_c)} ({_per(lab_c, n)}); the rule keeps Lightning only if the lower "
          f"bound is at or above -{_usd(lab['x_usd'])} and routed is not worse on violations, unsure and off-arm calls ("
          + "; ".join(f"{k} {agg['routed'][k]:.1f} vs {agg['super_only'][k]:.1f}" for k in lab["not_worse"])
          + f"). The test changed the default label model to **{lab['default_label_model']}**. Keeping Lightning in "
          "routed was already a disclosed deviation from spike S2, where Lightning scored 5/10 and 4/10 "
          "(`decision_rules.deviation_from_S2`); the tournament's rule reversed it. \"Not worse\" is a strict comparison "
          "of summed counts; violations and off-arm calls are 0 by construction in the rows that count.", "",
          "## Secondary comparisons (unadjusted 95 % intervals)", ""]
    for k in ("ultra_only - smart_rules", "super_vl - smart_rules"):
        c = comps[k]
        L.append(f"- {k}: {_ci_s(c)} ({_per(c, n)}): {_reading(c, n)}.")
    vs = ["smart_rules"] + [p for p in ("routed", "super_only", "super_vl", "ultra_only")]
    L.append("- Against a naive discount-first rule (`shop_rule`: discount to the floor at the first objection, then "
             "weekly reminders; our stand-in, not a real shop's data), in one line: "
             + "; ".join(f"`{p}`{' (no model)' if p == 'smart_rules' else ''} {_ci_s(comps[p + ' - shop_rule'])} "
                         f"({_sd(comps[p + ' - shop_rule']['point'] / n)} per customer)" for p in vs)
             + ". The rulebook without a model gains about as much or more: the gain over the naive rule comes from the "
               "menu (ask why, an earlier slot, a resend on the other channel), not from the model.")
    c = comps["concession_heuristic - smart_rules"]
    L.append(f"- `concession_heuristic` (naive gap-halving, also our stand-in) - smart_rules: {_ci_s(c)}.")
    L.append(f"- Descriptive, not pre-registered: ultra_only - super_only {_ci_s(ultra_super)} at "
             f"{_usd(agg['ultra_only']['model_usd'] - agg['super_only']['model_usd'], 2)} more model spend. Spike S7 kept "
             "Ultra out of routing on the spike's gold set; that choice is not confirmed here.")
    L += ["", "## Per policy", "",
          f"Each customer's mean over its repeats, summed over {n} customers; {note}. Net per customer: "
          + "; ".join(f"`{p}` {_usd(a['net'] / a['n'])}" for p, a in sorted(agg.items()))
          + ". Listed alphabetically: the differences among the top policies are not clear (see above).", ""]
    L += sections["Per policy (each buyer's mean over its repeats, summed over buyers)"].split("\n")[2:]
    L += [sections["Bands over repeats on the common subsample"]]
    L += ["## Where routed loses", "",
          f"Pre-registered test by cell (objection type x trade): only {testable} of {len(cells)} cells have n >= "
          f"{S.MIN_CELL_N}, so \"no clear loss\" in a cell means not testable, not no loss; in total routed is clearly "
          "behind (above).", ""]
    L += sections["Cells where routed earns less than the best policy in the cell"].split("\n")[2:]
    lc = res.get("losing_cells") or []
    L += [f"Of the {len(lc)} cells listed, {sum(x['gap'] < 1 for x in lc)} differ by less than $1 per customer (model "
          f"spend only); clear losses (n >= {S.MIN_CELL_N} and the interval below 0): {sum(x['clear_loss'] for x in lc)}.",
          "", "Descriptive, by objection type (post-hoc, not pre-registered): routed - smart_rules, net summed over the "
          "type's customers [95 % interval].", "", "| type | customers | difference [95 % interval] |", "|---|---|---|"]
    for typ in sorted({r["type"] for r in final}):
        c = S.paired_bootstrap([r for r in final if r["type"] == typ], "routed", "smart_rules")
        if c:
            L.append(f"| {typ} | {c['buyers']} | {_ci_s(c)} |")
    L += ["", "## Compliance", "",
          "Violations (messages after a customer asked to stop) are 0 for every policy by construction: opt-out "
          "customers' words come from published lists, and a shared code layer stops every policy on them. That is not "
          "an achievement of any model:", ""]
    for p, v in sorted(res["opt_out_violations"].items()):
        L.append(f"- {p}: " + "; ".join(f"{st} {x['violations']:.0f} over n = {x['n']}" for st, x in sorted(v.items())))
    L += ["", f"False opt-outs (a customer who did not ask to stop, stopped as an opt-out): {len(fo_rows)}."]
    for r in fo_rows:
        o = by_key.get((r["buyer"], r["policy"], r["repeat"], r.get("attempt")))
        said = [e["text"] for e in (o or {}).get("transcript", []) if e["who"] == "customer"]
        L.append(f"- {r['buyer']} ({r['type']}), {r['policy']}, repeat {r['repeat']}: stopped by the {r['how']}, after "
                 f"the customer wrote {said[-1]!r}; " + ("a feasible customer was lost" if r["feasible"] else "not feasible")
                 + f" (oracle margin {_usd(r['oracle'], 2)}). Published as a failure.")
    L += [f"- Customer lines that the shared opt-out code layer reads as a stop: {len(fo_lines['opt_out'])} of "
          f"{fo_lines['customer_lines_checked']}; read as ambiguous: {fo_lines['ambiguous']}.",
          "", "## Unsure flags (messages sent despite a flag)", "", "| policy | flags by kind |", "|---|---|"]
    L += [f"| {p} | {', '.join(f'{k} {v}' for k, v in sorted(f.items()))} |" for p, f in sorted(res["unsure_flags"].items())]
    integ = res["integrity_all_attempts"]
    L += ["", "## Integrity", "",
          f"- Errored rows {report.get('errored_rows')} (HTTP 503 after the client's retries), each clean on its second "
          f"attempt; customers excluded {len(report.get('excluded_buyers', []))}; customers kept "
          f"{res['kept']['buyers_kept']} of {res['kept']['buyers_planned']}.",
          f"- Token Factory error replies in the run window, all retried: "
          + ("; ".join(f"{k.split('/')[-1]} x{v}" for k, v in sorted(ledg["releases"].items())) or "none")
          + f"; HTTP 429: "
          f"{sum(v for k, v in ledg['releases'].items() if 'HTTP 429' in k)}. Reservations kept on network-level retries "
          f"(held as spend): {sum(ledg['kept_reservations'].values())}.",
          f"- Usage-based cost of the kept conversations: shop {_usd(sum(o['seller_cost'] for o in outs), 4)}, buyer writer "
          f"{_usd(sum(o['buyer_cost'] for o in outs), 4)}; the ledger window ({_usd(ledg['usd'], 4)}) also holds the "
          "errored attempts and the kept reservations.",
          f"- Off-arm calls {sum(g['off_arm_calls'] for g in integ.values()):.0f}, chronology errors "
          f"{sum(g['chronology_errors'] for g in integ.values()):.0f}, buyer text from the template "
          f"{sum(g['writer_fallbacks'] for g in integ.values()):.0f}, fallback labels "
          f"{sum(g['label_fallback_calls'] for g in integ.values()):.0f} (routed), duplicate outcome lines "
          f"{len(outcome_duplicates(outs))}.", ""]
    g = res["sanity_gates"]
    L += ["## Simulator gates (text-blind rule policies)", "",
          f"- Booking rate in 30–80 %: " + ", ".join(f"{p} {v:.3f}" for p, v in g["booking_rate_by_policy"].items())
          + f"; price customers' booking rate by walkaway tercile {[round(x, 3) for x in g['price_buyers_booking_rate_by_walkaway_tercile']]}.",
          f"- Bookings above the walkaway {g['bookings_above_walkaway']}, below the floor {g['bookings_below_floor']}: "
          "invariants by construction, integrity checks, not evidence of realism.", ""]
    sr, _, _, sreport, _ = load(ROOT / "tournament" / "smoke-b")
    L += ["## Smoke before the run (mechanics and cost only)", "",
          f"`tournament/smoke-b/`: {sreport.get('rows')} conversations, {sreport.get('clean_rows')} clean, spend "
          f"{_usd(sreport.get('spent_usd', 0), 4)}. Two customers per type rather than the population mix; `smart_rules` "
          "and `concession_heuristic` were not in it (their only cost is the buyer writer). Not results.", "",
          "## What is real, what is seeded, what is simulated", "",
          f"- Real: Token Factory calls and their usage-based cost; Nemotron labels, move choices and message text, on "
          "simulated conversations. The $ score depends only on the chosen move: the buyer code never reads the shop's "
          "message (`second_ask/tournament/env.py`, `react`), so message wording changes no customer decision and message "
          "quality is not measured. The compliance rules in code.",
          f"- Seeded: ten paraphrased cases from one homeowner's history set the parameter ranges of {seeded} of "
          f"{len(cards)} customers ({mapped} of the 10 cases mapped from other trades); the other {len(cards) - seeded} "
          "customers, the type mix, prices and floors are our assumptions. The agent was not tested on the real cases. "
          "Speaking styles come from CaSiNo strategy-label counts.",
          "- Simulated: " + "; ".join(m["provenance"]["simulated"]) + ".", "",
          "## Limitations", "",
          f"1. The customers are simulated: their decisions are code drawn from a seeded card, and their words come from "
          f"a non-NVIDIA model (`{m['buyer_simulator']['model']}`). The result says how the policies behave in this "
          "simulator, not with live homeowners.",
          f"2. `smart_rules` was written by us knowing the simulator's vocabulary and decision code: asking why makes the "
          f"{vague} vague customers clear, and a named amount is 88–97 % of the hidden walkaway. It takes "
          f"{agg['smart_rules']['net'] / orc:.0%} of the oracle, so any policy has little room to grow here. This works for "
          "the baseline and says nothing about live customers.",
          f"3. All $ are {note}, summed over {n} customers. The shop and its name are made up.",
          "4. The $ score depends only on the chosen move: message wording changes no customer decision. The quality and "
          "persuasiveness of Nemotron's messages were not measured.",
          f"5. Real layer: ten paraphrased cases from one homeowner's history set parameter ranges for only {seeded} of "
          f"{len(cards)} customers, some mapped from other trades. The agent was not tested on the real cases themselves.",
          "6. Silence comes in one kind only: the estimate was never opened (channel customers). There are no customers "
          "who opened the estimate and stayed silent.",
          f"7. The by-cell test has little power: n >= {S.MIN_CELL_N} in only {testable} of {len(cells)} cells. \"No clear "
          "loss\" means not testable, not no loss.",
          f"8. A published failure: {len(fo_rows)} false opt-out"
          + (f" ({', '.join(r['buyer'] + ' ' + r['policy'] for r in fo_rows)}): the {fo_rows[0]['how']} read a customer's "
             f"sentence as a request to stop, and a feasible customer was lost." if fo_rows else "."),
          "9. Opt-out violations are 0 by construction (the shared code layer), not to any model's credit. Whether Fla. "
          "Stat. §501.616(6) covers SMS reminders about an estimate is not confirmed; the compliance code is not legal "
          "advice.",
          f"10. Repeats (K = {m['repeats']['k']}) ran only on a subsample of {len(m['repeats']['subsample'])} customers; "
          f"the other {n - len(m['repeats']['subsample'])} ran once, at temperature 1.0.",
          "11. Before the first manifest, the simulator was calibrated to the 30–80 % booking-rate gate on the text-blind "
          "rule policies with fake models: the first dry run gave 15 % and 13 %, and the buyer model was changed to lose "
          "customers to time. Nemotron took no part in that calibration. The generator of this page was written after "
          "the run and is not in the manifest.",
          "12. Routing deliberately departed from spike S2's rule (Lightning 5/10 and 4/10); the deviation was disclosed. "
          "The tournament's pre-registered rule brought back Super labels.",
          "13. `shop_rule` and `concession_heuristic` are our stand-ins, not observed practice of real shops.",
          f"14. Ultra is not in the routing, per spike S7 on the spike's gold set; in this tournament, descriptively, "
          f"ultra_only - super_only = {_ci_s(ultra_super)}: "
          "S7's choice is not confirmed here.", "",
          "## Reproduce", "", "```", "python3 -m pytest -q                                        # offline tests",
          f"python3 -m second_ask.tournament.run full --approve {sha}   # live, needs NEBIUS_API_KEY",
          "python3 -m second_ask.tournament.run results                 # pre-registered tables from rows.jsonl",
          "python3 tools/run_checks.py results-md tournament/full       # this page and the answer in README.md", "```", ""]
    (Path(out_path) if out_path else ROOT / "RESULTS.md").write_text("\n".join(L))
    readme = ROOT / "README.md"
    txt = readme.read_text()
    a, b = "<!-- results:start -->", "<!-- results:end -->"
    if a in txt and b in txt and not out_path:
        answer = (f"{a}\nNot in our pre-registered test: against a rulebook we wrote with full knowledge of the simulator, "
                  f"the routed design came out at {_per(main_r, n)} [{_sd(main_r['point'])} over {n} simulated customers; "
                  f"simulated margin on illustrative prices, not market data], "
                  f"Super-only showed no clear difference ({_per(main_s, n)}), and the test switched the default label "
                  f"model to {lab['default_label_model']}.\n{b}")
        readme.write_text(txt[:txt.index(a)] + answer + txt[txt.index(b) + len(b):])
    print(f"wrote {out_path or ROOT / 'RESULTS.md'}: {len(L)} lines")
    return 0

if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    sys.exit({"smoke": cmd_smoke, "ledger": cmd_ledger, "after": cmd_after, "watch": cmd_watch,
              "results-md": cmd_results_md}[cmd](*args))
