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
    if (d / "run_window.json").exists():
        state["ended"] = json.loads((d / "run_window.json").read_text())["ended"]
    report = json.loads((d / "run_report.json").read_text()) if (d / "run_report.json").exists() else {}
    return rows, outs, state, report, bad_r + bad_o


def _ts(iso):
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def ledger_since(started_iso, ended_iso=None):
    """b2 ledger records from the run's start to its end (the end bound keeps later runs out of an earlier run's window)."""
    t0 = _ts(started_iso) if started_iso else 0
    t1 = _ts(ended_iso) + 60 if ended_iso else float("inf")  # +60 s: settles written while the last calls closed
    out = []
    for line in LEDGER.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if t0 <= r.get("ts", 0) <= t1 and r.get("tag") == "b2":
            out.append(r)
    return out


def ledger_summary(started_iso, ended_iso=None):
    """Releases (error replies) by model and HTTP code; per model, reserves minus settled or released (kept
    reservations: network failures, malformed replies, replies without usage, or calls still in flight)."""
    recs = ledger_since(started_iso, ended_iso)
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
    ledg = ledger_summary(state.get("started"), state.get("ended"))
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
    print(json.dumps(ledger_summary(state.get("started"), state.get("ended")), indent=1))
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
           "errors_429": sum("429" in (r.get("error") or "") for r in rows), "ledger": ledger_summary(state.get("started"), state.get("ended")),
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


V2 = ROOT / "tournament" / "v2"


def _pct(x):
    return f"{x * 100:.1f} %"


def _pci(c):
    return f"{_pct(c['share'])} [{_pct(c['lo'])}, {_pct(c['hi'])}]" if c and c.get("share") is not None else "-"


def _pushed(path, first=False):
    """When the commit that added (first=True) or last changed a file reached origin/lane-b2, by the reflog (UTC)."""
    pushes = {}
    for line in _git("reflog", "show", "--date=iso-strict", "refs/remotes/origin/lane-b2").splitlines():
        pushes.setdefault(line.split()[0], line.split("@{", 1)[1].split("}", 1)[0])
    commits = _git("log", "--format=%h", *(["--diff-filter=A"] if first else []), "--", path).split("\n")
    order = _git("log", "--format=%h").split("\n")  # newest first: the first push at or after the commit
    for h in commits[:1]:
        later = order[:order.index(h) + 1] if h in order else []
        hit = [pushes[x] for x in reversed(later) if x in pushes]
        if hit:
            return datetime.fromisoformat(hit[0]).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "not found in the reflog"


def v2_status():
    """One sentence on the second test for README, or None."""
    if not (V2 / "smoke" / "gates.json").exists():
        return None
    g = json.loads((V2 / "smoke" / "gates.json").read_text())
    if g["pass"]:
        return None
    return (f"A second pre-registered test, with customers wording the same facts freely, stopped before its main run: "
            f"the buyer writer did not pass the vocabulary gate (L_own {_pct(g['leakage_v2']['L_own']['share'])} against "
            f"at most {_pct(json.loads((V2 / 'MANIFEST.json').read_text())['gates_v2']['L_own_max'])}).")


def v2_section():
    """The second test (v2), from its manifests, CHANGES and the two smokes' gates.json. No policy outcome of v2."""
    if not (V2 / "MANIFEST.json").exists():
        return []
    m2 = json.loads((V2 / "MANIFEST.json").read_text())
    sha2 = (V2 / "MANIFEST.sha256").read_text().split()[0]
    sha1 = (V2 / "smoke.83010fc4" / "MANIFEST.sha256").read_text().split()[0]
    g1 = json.loads((V2 / "smoke.83010fc4" / "gates.json").read_text())
    g2 = json.loads((V2 / "smoke" / "gates.json").read_text()) if (V2 / "smoke" / "gates.json").exists() else None
    r1 = json.loads((V2 / "smoke.83010fc4" / "run_report.json").read_text())
    r2 = json.loads((V2 / "smoke" / "run_report.json").read_text()) if (V2 / "smoke" / "run_report.json").exists() else {}
    gt, v1b = m2["gates_v2"], g1["leakage_v1"]
    L = ["## Second test (v2): wording outside the simulator's vocabulary", ""]
    if g2 and not g2["pass"]:
        L += ["**Stopped before the main run: the buyer writer did not pass the pre-registered vocabulary gate, so v2 has "
              "no policy results and the headline above stands.**", ""]
    L += [f"Why: on v1's {v1b['L_own']['n']:,} buyer lines, {_pct(v1b['L_any']['share'])} trip at least one of "
          f"`smart_rules`' triggers and {_pct(v1b['L_own']['share'])} the rule meant for the customer's type (pre-registered "
          "definition). v2 asked whether the rulebook's edge survives when a model that never saw the rules writes the same "
          f"facts in its own words (`{m2['buyer_simulator']['model']}`; the facts come from `env.intent`, which a test checks "
          "against v1's instructions on every card and state). Everything else is the v1 code, checked by sha256; only "
          "`env.py`, `run.py` and `manifest.py` changed. Ultra was not in v2.", "",
          f"- Pre-registered: manifest `{sha1}`, pushed {_pushed('tournament/v2/PREREG.md', first=True)} "
          f"(`tournament/v2/PREREG.md`): hypothesis, decision rule (lower bounds of D2 and DiD above 0), budget, and gates "
          f"L_own at most {_pct(gt['L_own_max'])}, fidelity at least {_pct(gt['fidelity_min'])}, template at most "
          f"{_pct(gt['template_max'])}.",
          f"- First smoke ({r1.get('rows')} conversations, `tournament/v2/smoke.83010fc4/`): L_own {_pci(g1['leakage_v2']['L_own'])} "
          f"(fail), fidelity {_pci(g1['fidelity_v2'])} (fail; the v1-writer reference "
          f"{_pct(g1['reference_v1_writer']['fidelity']['share'])}), template {_pct(g1['template']['share'])}, integrity "
          f"{'pass' if g1['integrity']['ok'] else 'fail'}.",
          f"- **The gates were amended after the failed smoke**: manifest `{sha2}`, pushed "
          f"{_pushed('tournament/v2/MANIFEST.sha256')} before the second smoke. The judge mapping (household-must-review "
          "accepts \"undecided\" when someone else with a say is mentioned; a landlord counts), L_own without the $-amount "
          "pattern (a faithful price line has to state the amount), and the one allowed revision of the writer prompt. "
          "Thresholds, hypothesis, decision rules, policies and budget unchanged. Before, after and why, with example "
          "lines: `tournament/v2/CHANGES.md`.",
          f"- Disclosure: {m2['amendment']['disclosure']}"]
    if g2:
        d = g2["descriptive"]
        L += [f"- Second smoke ({r2.get('rows')} conversations on customers the first smoke did not use, "
              f"`tournament/v2/smoke/`): fidelity {_pci(g2['fidelity_v2'])} (pass; {_pct(g2['fidelity_v2']['share_old_mapping'])} "
              f"under the first mapping; the v1-writer reference {_pct(g2['reference_v1_writer']['fidelity']['share'])}, "
              f"{_pct(g2['reference_v1_writer']['fidelity']['share_old_mapping'])} under the first mapping), template "
              f"{_pct(g2['template']['share'])} (pass), integrity {'pass' if g2['integrity']['ok'] else 'fail'}, "
              f"**L_own {_pci(g2['leakage_v2']['L_own'])} ({'pass' if g2['gates']['L_own'] else 'fail'})**; by type "
              + ", ".join(f"{t} {_pct(v['share'])} (n = {v['n']})" for t, v in g2["leakage_v2"]["L_own_by_type"].items())
              + f". For comparison: v1 under the same definition {_pct(d['L_own_v1_without_amount']['share'])}; with the "
              f"$-amount pattern {_pct(d['L_own_with_amount']['share'])} (v1 {_pct(d['L_own_v1_with_amount']['share'])}). "
              f"\"v1 minus 20 points\" ({_pct(d['L_own_v1_without_amount']['share'] - 0.20)}) would "
              f"{'' if d['would_pass_v1_minus_20pp'] else 'not '}have been met either.",
              "- What this shows, and what it does not: a writer that never saw the rules, writing the same facts in its "
              "own words, still mostly used phrasing the rulebook catches, because the facts themselves (how many weeks, "
              "the deadline day, who else decides, the amount) are what the rulebook keys on. So this design could not "
              "separate wording from facts. It says nothing either way about whether the rulebook or Nemotron holds up "
              "on real customers' wording.",
              f"- v2 spend (two smokes, the judge and the references): "
              f"{_usd(r1.get('spent_usd', 0) + r2.get('spent_usd', 0) + g1.get('judge_spent_usd', 0) + g2.get('judge_spent_usd', 0), 2)} "
              "of the $6 budget."]
    return L + [""]


V3 = ROOT / "tournament" / "v3"
V3_READING = {"beats": "ahead: the whole interval is above 0, and not worse on opt-outs or off-arm calls",
              "behind": "behind: the whole interval is below 0", "no clear difference": "no clear difference (not a tie)"}


def v3_results():
    f = V3 / "full" / "v3-results.json"
    return json.loads(f.read_text()) if f.exists() else None


def v3_status():
    """One sentence on the third test for README, or None (written as pre-registered in tournament/v3/PREREG.md)."""
    r = v3_results()
    if not r:
        return None
    n = r["d3"]["buyers"]
    if r["verdict"] == "beats":
        return (f"Rules + Nemotron beat rules alone: {_per(r['d3'], n)} over {n} simulated customers (third pre-registered "
                f"test: the rules decide, Nemotron Super only where the rules do not know).")
    return (f"We also tested rules first, with Nemotron only where the rules don't know: {_per(r['d3'], n)}, "
            f"{'behind' if r['verdict'] == 'behind' else 'no clear gain'} (third pre-registered test).")


def v3_section():
    """The third test (v3), from its manifest, run files and tools/v3_analysis.py output only."""
    r = v3_results()
    if not r:
        return []
    m = json.loads((V3 / "MANIFEST.json").read_text())
    sha = (V3 / "MANIFEST.sha256").read_text().split()[0]
    st = json.loads((V3 / "full" / "run_state.json").read_text())
    rep = json.loads((V3 / "full" / "run_report.json").read_text())
    freeze = _git("log", "-1", "--format=%h", "--", "tournament/v3/MANIFEST.sha256")
    freeze_t = datetime.fromisoformat(_git("log", "-1", "--format=%cI", freeze)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    smoke = json.loads((V3 / "smoke" / "run_report.json").read_text())
    d3, n, t, h = r["d3"], r["d3"]["buyers"], r["table"], r["handoffs"]
    L = ["## Third test (v3): rules first, Nemotron only where the rules do not know", "",
         f"**Pre-registered verdict: {r['verdict']}.** rules_then_super - smart_rules, net over {n} customers: "
         f"{_ci_s(d3)} ({_per(d3, n)}); {V3_READING[r['verdict']]}.", "",
         "The hybrid `rules_then_super` is `smart_rules` unchanged, except where the rules do not know the answer: rule 8 "
         "(the customer's latest message says only think / get back / not sure / maybe) and rule 9 (nothing the rules "
         "recognise). There the `super_only` agent decides (Nemotron Super labels the reply, Nemotron Super picks a move "
         "from the same code-built menu; the floor check and the compliance gate are the same). Silence, rules 1 to 7 and "
         "customers the shared opt-out layer has stopped stay with the rules. Both policies ran fresh in one run, "
         f"{m['repeats']['k']} times on all {n} customers, with the same buyer writer as v1 and common random numbers: "
         "within a repeat both policies meet the same customer words until the hybrid's first hand-off.", "",
         f"- Pre-registered: manifest `{sha}` (`tournament/v3/PREREG.md`; the pre-registration and "
         f"`tools/v3_analysis.py` are pinned by sha256 in it), frozen in `{freeze}` at {freeze_t}, pushed "
         f"{_pushed('tournament/v3/MANIFEST.sha256')}. Smoke {smoke.get('rows')} conversations (mechanics only, no outcome "
         f"printed); run started {st.get('started')}, {rep.get('rows')} rows ({rep.get('clean_rows')} clean, "
         f"{rep.get('errored_rows')} errored and re-run).",
         f"- Disclosure: {m['disclosure']}", "",
         "| policy | customers | bookings | net $ | model $ | violations | false opt-outs (conversations) | off-arm |",
         "|---|---|---|---|---|---|---|---|"]
    for p_, a in t.items():
        L.append(f"| {p_} | {a['buyers']} | {a['bookings']:.1f} | {a['net']:,.0f} | {a['model_usd']:.4f} | "
                 f"{a['violations']:.1f} | {a['false_opt_outs']:.2f} ({a['false_opt_out_conversations']}) | "
                 f"{a['off_arm_calls']:.1f} |")
    L += ["", "Each customer's mean over its repeats, summed over customers; simulated margin on illustrative prices (not "
          "market data).", "",
          f"- Hand-offs to Nemotron: {h['total']} in {h['conversations_with_handoff']} conversations (rule 8: "
          f"{h['by_branch'].get('think', 0)}, rule 9: {h['by_branch'].get('none', 0)}). Nemotron's move differed from the "
          f"move the rules would have made in {h['model_move_differs']} of {h['total']}"
          + (f"; it stopped the conversation {sum(h['model_stopped'].values())} times" if h["model_stopped"] else "") + ".",
          "- By objection type (descriptive, not adjusted): " + "; ".join(
              f"{k} {_ci_s(v)}" for k, v in r["by_type"].items() if v) + "."]
    if r.get("vs_v1_smart_rules"):
        L.append(f"- Against v1's smart_rules rows (independent customer words there, descriptive): {_ci_s(r['vs_v1_smart_rules'])}.")
    if r["false_opt_outs"]:
        L.append("- False opt-outs by Nemotron: " + "; ".join(f"{f['buyer']} ({f['type']}), repeat {f['repeat']}"
                                                            for f in r["false_opt_outs"]) + ".")
    outs = [json.loads(x) for x in (V3 / "full" / "outcomes.jsonl").read_text().splitlines() if x.strip()]
    cap = Counter(o["policy"] for o in outs if o["type"] == "timing_pause" and o["end"] == "move cap")
    tp = Counter(o["policy"] for o in outs if o["type"] == "timing_pause")
    think = [json.loads(s_["note"]) for o in outs if o["policy"] == "rules_then_super" for s_ in o["steps"]
             if s_.get("step") == "handoff" and json.loads(s_["note"])["branch"] == "think"]
    chk = sum((x["model_move"] or "").startswith("checkin_") for x in think)
    L += [f"- Where it lost (post-hoc, descriptive; written after the run): on rule 8 (\"I'll think about it\"), where the "
          f"rules ask why, Nemotron chose a check-in in {chk} of {len(think)} hand-offs. In this simulator a customer who "
          "waits for a date and has not said when stays silent after a check-in (`second_ask/tournament/env.py`, `react`), "
          "while asking why makes them name the weeks, which the rules then match. Timing-pause conversations that ended at "
          f"the five-message cap: rules_then_super {cap['rules_then_super']} of {tp['rules_then_super']}, smart_rules "
          f"{cap['smart_rules']} of {tp['smart_rules']}.",
          f"- Spend: run {_usd(rep.get('spent_usd', 0), 3)}, smoke {_usd(smoke.get('spent_usd', 0), 4)} of real Token "
          f"Factory spend (ceilings $3.00 and $0.30)."]
    L += ["- What this shows, and what it does not: the same simulator and the same rulebook written for it, so the room "
          "above the rules is small by construction (smart_rules takes most of the oracle margin). It says nothing about "
          "live customers."]
    return L + [""]


V4 = ROOT / "data" / "second_read"
V4_ARMS = (("rules", "hardened rules"), ("super", "Nemotron 3 Super (primary)"), ("lightning", "Nemotron 3.5 Lightning"),
           ("ultra", "Nemotron 3 Ultra"), ("super_vl", "Nemotron 3.5 Super-VL"))
V4_STRATA = {"S1": "one plain fact", "S2": "two or three facts", "S3": "paraphrase", "S4": "Spanish/Spanglish"}


def v4_results():
    f = V4 / "test" / "results.json"
    return json.loads(f.read_text()) if f.exists() else None


def _pp(c):
    return f"{c['diff_pp']:+.2f} pp [{c['lo_pp']:+.2f}, {c['hi_pp']:+.2f}]".replace("+0.00", "0.00")


def v4_status():
    """One sentence on the fourth test for README, or None (written as pre-registered in second_read/PREREG.md)."""
    r = v4_results()
    if not r:
        return None
    a, p = r["arms"], r["primary"]
    if r["verdict"] == "beats":
        return (f"Fourth test: with the rules keeping every money decision and Nemotron Super reading the reply, the shop "
                f"sent the same offer as a perfect read on {a['super']['match_rate_pct']} % of {p['n']} replies against "
                f"{a['rules']['match_rate_pct']} % for the hardened rules ({_pp(p)}, pre-registered).")
    return (f"A fourth test had Nemotron only read the customer while the rules decide: same offer as a perfect read on "
            f"{a['super']['match_rate_pct']} % of {p['n']} synthetic replies with Super, {a['rules']['match_rate_pct']} % "
            f"with the hardened rules, {r['verdict']} (pre-registered).")


def _v4_miss_kinds(rows, tests, arm):
    """Post-hoc: what the read got wrong on each mismatched reply, by field (counts)."""
    kinds = Counter()
    for x in rows:
        if x["arm"] != arm or x["match"]:
            continue
        g, f = tests[x["id"]]["gold"], x["facts"] or {}
        if x["failure"]:
            kinds["no valid read"] += 1
            continue
        if g["stop"] and not f.get("stop"):
            kinds["missed a stop"] += 1
            continue
        if g.get("ceiling_usd") is None and f.get("ceiling_usd") is not None:
            kinds["read a price as a ceiling"] += 1
        if sorted(g.get("drop_items") or []) != sorted(f.get("drop_items") or []):
            kinds["missed or added a dropped item"] += 1
        if g.get("wait_weeks") != f.get("wait_weeks"):
            kinds["wrong wait"] += 1
        if g.get("need_by") != f.get("need_by"):
            kinds["wrong deadline"] += 1
    return kinds


def v4_section():
    """The fourth test (v4), from data/second_read/ (FREEZE, test split, run rows, second_read.analysis output) only."""
    r = v4_results()
    if not r:
        return []
    rows = [json.loads(x) for x in (V4 / "test" / "rows.jsonl").read_text().splitlines() if x.strip()]
    tests = {t["id"]: t for t in (json.loads(x) for x in (V4 / "test.jsonl").read_text().splitlines() if x.strip())}
    smoke = [json.loads(x) for x in (V4 / "smoke-test" / "rows.jsonl").read_text().splitlines() if x.strip()]
    st = json.loads((V4 / "test" / "run_state.json").read_text())
    summ = json.loads((V4 / "test-summary.json").read_text())
    fz = json.loads((V4 / "FREEZE.json").read_text())
    when = lambda c: datetime.fromisoformat(_git("log", "-1", "--format=%cI", c)).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    freeze = _git("log", "-1", "--format=%h", "--", "data/second_read/FREEZE.json")
    split = _git("log", "--diff-filter=A", "--format=%h", "--", "data/second_read/test.jsonl").splitlines()[-1]
    a, p, n = r["arms"], r["primary"], r["lines"]
    stops = [t for t in tests.values() if t["gold"]["stop"]]
    worded = sum(1 for t in stops if "stop" not in t["text"].lower())
    missed = lambda arm: [tests[x["id"]]["text"] for x in rows if x["arm"] == arm and x["gold_stop"] and not x["read_stop"]]
    usd = lambda rs: sum(x.get("usd") or 0.0 for x in rs)
    kinds = lambda arm: "; ".join(f"{k} {v}" for k, v in _v4_miss_kinds(rows, tests, arm).most_common())
    nmiss = lambda arm: sum(1 for x in rows if x["arm"] == arm and not x["match"])
    L = ["## Fourth test (v4): the rules decide, Nemotron only reads", "",
         f"**Pre-registered verdict: {r['verdict']}.** On {n} customer replies frozen before the run, the shop sent the "
         f"same offer as a perfect read on {a['super']['match_rate_pct']} % of replies with Nemotron 3 Super reading and "
         f"on {a['rules']['match_rate_pct']} % with the hardened rulebook reading: Super - rules {_pp(p)} (paired "
         f"bootstrap, 2,000 draws, seed 7). The rule needed at least +10 pp with the whole interval above 0.", "",
         "v1–v3 had the model choose the shop's move inside a simulator whose buyers never read the words. v4 tests "
         "reading alone: one deterministic `offer()` (`second_read/offer.py`; prices only from the shop's menu, never "
         "below the owner's floor) turns what a reader extracted from a reply (stop, a price ceiling, items to drop, a "
         "deadline, a wait, someone else who must agree, ready to book) into an offer. A reply counts as matched when "
         "that offer equals the offer from the true facts.", "",
         f"- Pre-registered: `second_read/PREREG.md`; `data/second_read/FREEZE.json` ({len(fz['files'])} files under "
         f"`second_read/` by sha256, the Nemotron prompt, the dev data, the test seed, the strata weights and the decision "
         f"rule; canonical digest `{r['freeze_digest'][:12]}…`), committed in `{freeze}` at {when(freeze)} and pushed "
         f"before the test split existed. Test split ({n} replies, seed `{fz['test_split']['seed']}`, sha256 "
         f"`{r['split_sha256'][:12]}…`) generated after that and first committed in `{split}` at {when(split)}. Smoke "
         f"{len({x['id'] for x in smoke})} replies × {len({x['arm'] for x in smoke})} readers (mechanics and cost only); "
         f"run started {st['started']}, {len(rows)} rows.",
         "- Both readers were built on a separate 200-reply dev split only: the rulebook by a lane that never called "
         "Nemotron (`second_read/RULES-HARDENING-LOG.md`), the Nemotron prompt in its own log "
         "(`second_read/NEMOTRON-PROMPT-LOG.md`). Both reached about 99–100 % on dev.",
         "- Disclosure: v4 is the fourth test of B2, designed after v1–v3 were known. The replies are synthetic: one "
         "non-NVIDIA model (`deepseek-ai/DeepSeek-V4-Flash-0731`) wrote them from facts our code chose, and the rulebook "
         "was hardened on 200 replies from the same writer.", "",
         f"| reader | replies | same offer as a perfect read | 95 % CI | failed reads | missed stops (of {len(stops)}) "
         "| false stops | $ per 1,000 replies | p50 s |", "|---|---|---|---|---|---|---|---|---|"]
    for k, name in V4_ARMS:
        x = a[k]
        L.append(f"| {name} | {x['n']} | {x['match_rate_pct']} % | [{x['match_ci95_pct'][0]}, {x['match_ci95_pct'][1]}] "
                 f"| {x['reader_failures']} | {x['missed_stops']} | {x['false_stops']} | {round(x['usd_per_1000'] or 0, 2)} "
                 f"| {x['p50_seconds'] or 0} |")
    L += ["", "Lightning, Ultra and Super-VL are descriptive (not part of the decision rule).", "",
          "- Per stratum, Super - rules (unadjusted): " + "; ".join(
              f"{V4_STRATA[s]} {_pp(v['super_minus_rules'])}" for s, v in sorted(r["per_stratum"].items())) + ".",
          f"- Stops (secondary, pre-registered): {len(stops)} of the {n} replies ask the shop to stop, {worded} of them "
          f"without the word \"stop\". The rulebook missed {len(missed('rules'))}"
          + (" (" + "; ".join(f"\"{t}\"" for t in missed("rules")) + ")" if missed("rules") else "")
          + "; " + ", ".join(f"{name.split(' (')[0]} {len(missed(k))}" for k, name in V4_ARMS[1:]) + ". These are "
          "synthetic replies; the counts say nothing about real customers.",
          f"- Where each lost (post-hoc, descriptive, by field): the rulebook's {nmiss('rules')} misses: {kinds('rules')}. "
          f"Super's {nmiss('super')} misses: {kinds('super')}.",
          f"- Fidelity: a blind non-NVIDIA judge (`Qwen/Qwen3-235B-A22B-Instruct-2507`) gives the gold offer on "
          f"{r['fidelity']['agreement_pct']} % of replies. On the {r['sensitivity_judge_agreed']['n']} replies where it "
          f"agrees with gold: Super - rules {_pp(r['sensitivity_judge_agreed'])}.",
          f"- Spend: run {_usd(usd(rows), 2)}, smoke {_usd(usd(smoke), 2)}, test replies "
          f"{_usd(summ['spend_usd']['total'], 2)} of real Token Factory spend (v4 ceiling $8.00 in code).",
          "- What this shows, and what it does not: both readers are near the ceiling on these replies, so this test "
          "cannot separate them. The rulebook's score says how well rules cover one writer's phrasing after being tuned "
          "on it, not how they would cover real customers. What the run does show: the shop can keep every money "
          "decision in code and let Nemotron read, with no measurable loss in this test, at about "
          f"{_usd(a['super']['usd_per_1000'], 2)} per 1,000 replies for Super.", ""]
    return L

def lead_section(main_r, main_s, n):
    """What ships now and the four tests in one place, from the run files (v1 tables, v3 and v4 results) only."""
    L = ["## What ships now, and the four tests", "",
         "Second Ask now keeps every money and timing decision in the shop's code; Nemotron 3 Super reads the customer's "
         "reply into facts and drafts the message, and the code checks the draft before anything is sent (`read_first/`). "
         "Four pre-registered tests led there:", "",
         f"- Test 1 (v1, below): Nemotron choosing the shop's move against a good rulebook: the routed design "
         f"{_per(main_r, n)}, behind; Super-only {_per(main_s, n)}, no clear difference.",
         "- Test 2 (v2): stopped at its vocabulary gate before the main run (no policy results)."]
    r3 = v3_results()
    if r3:
        L.append(f"- Test 3 (v3): rules first, Nemotron only where the rules do not know: {_per(r3['d3'], r3['d3']['buyers'])}, "
                 f"{'behind' if r3['verdict'] == 'behind' else r3['verdict']}.")
    r4 = v4_results()
    if r4:
        a = r4["arms"]
        L.append(f"- Test 4 (v4): Nemotron only reads, the code decides: same offer as a perfect read on "
                 f"{a['super']['match_rate_pct']} % of {r4['lines']} synthetic replies with Nemotron 3 Super against "
                 f"{a['rules']['match_rate_pct']} % for the hardened rulebook: {r4['verdict']}.")
    return L + ["", "The sections below follow the tests in order. All $ are simulated margin on illustrative prices "
                "(not market data).", ""]

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
    window = d / "run_window.json"  # recorded once: a checkout moves file times
    ended = (json.loads(window.read_text())["ended"] if window.exists()
             else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((d / "run_report.json").stat().st_mtime)))
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
    ledg = ledger_summary(state.get("started"), ended)
    note = "simulated margin on illustrative prices (not market data)"
    L = ["# Second Ask: tournament results", ""] + lead_section(main_r, main_s, n) + [
         f"**Test 1 (v1): in our pre-registered test, Nemotron did not beat a good rulebook. Against "
         f"`smart_rules`, a rulebook we wrote with full knowledge of the simulator, the routed design (Lightning labels, "
         f"Super chooses) came out at {_ci_s(main_r)} against it over {n} simulated customers ({_per(main_r, n)}): "
         f"behind. Super-only: {_ci_s(main_s)} ({_per(main_s, n)}): no clear difference. That test made "
         f"{lab['default_label_model']} the default label model of the earlier design. All $ are {note}.**", "",
         f"Pre-registered manifest `tournament/MANIFEST.json`: sha256 `{sha}` of its canonical JSON (sorted keys, no "
         f"spaces; the file's own sha256 is `{file_sha[:16]}…`). Frozen in commit `{freeze}` at {utc(freeze_t)}, pushed "
         f"{utc(push_t) if 'T' in push_t else push_t}; the run started {state.get('started')} and finished {ended}. "
         f"`second_ask/**` and the manifest did not change between the freeze and the end of the run (the second test later "
         f"changed three files; this run reproduces at commit `{freeze}`). Run: {report.get('rows')} conversation rows "
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
          "and `concession_heuristic` were not in it (their only cost is the buyer writer). Not results.", ""]
    L += v2_section()
    L += v3_section()
    L += v4_section()
    L += ["## What is real, what is seeded, what is simulated", "",
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
          "## Reproduce", "", "```", f"git checkout {freeze}                                    # the code of this run",
          "python3 -m pytest -q                                        # offline tests",
          f"python3 -m second_ask.tournament.run full --approve {sha}   # live, needs NEBIUS_API_KEY",
          "python3 -m second_ask.tournament.run results                 # pre-registered tables from rows.jsonl",
          "git checkout lane-b2 && python3 tools/run_checks.py results-md tournament/full   # this page and README's answer",
          "```", ""]
    (Path(out_path) if out_path else ROOT / "RESULTS.md").write_text("\n".join(L))
    readme = ROOT / "README.md"
    txt = readme.read_text()
    a, b = "<!-- results:start -->", "<!-- results:end -->"
    if a in txt and b in txt and not out_path:
        v2 = v2_status()
        answer = (f"{a}\nNot in our pre-registered test: against a rulebook we wrote with full knowledge of the simulator, "
                  f"the routed design came out at {_per(main_r, n)} [{_sd(main_r['point'])} over {n} simulated customers; "
                  f"simulated margin on illustrative prices, not market data], "
                  f"Super-only showed no clear difference ({_per(main_s, n)}), and the test switched the default label "
                  f"model to {lab['default_label_model']}." + (f" {v2}" if v2 else "")
                  + (f" {v3_status()}" if v3_status() else "") + (f" {v4_status()}" if v4_status() else "") + f"\n{b}")
        txt = txt[:txt.index(a)] + answer + txt[txt.index(b) + len(b):]
        ea, eb = "<!-- evidence:start -->", "<!-- evidence:end -->"
        if ea in txt and eb in txt:
            rows = [("Main test (v1), pre-registered", "`tournament/MANIFEST.json`, [`RESULTS.md`](RESULTS.md)",
                     f"manifest `{sha[:12]}…` (canonical JSON); frozen in `{freeze}` at {utc(freeze_t)}, pushed "
                     f"{utc(push_t) if 'T' in push_t else push_t}; run {state.get('started')} to {ended}; "
                     f"{report.get('clean_rows')} conversations; {_usd(ledg['usd'], 2)} of real Token Factory spend"),
                    ("Main result", "[`RESULTS.md`](RESULTS.md)",
                     f"routed {_per(main_r, n)}, Super-only {_per(main_s, n)} against `smart_rules`; default label model "
                     f"after the test: {lab['default_label_model']}"),
                    ("Second test (v2): free wording", "`tournament/v2/` (PREREG, CHANGES)",
                     (v2_status() or "see RESULTS.md").replace("A second pre-registered test, with customers wording the "
                                                               "same facts freely, stopped", "Stopped")),
                    *([("Third test (v3): rules first, Nemotron where they do not know",
                        "`tournament/v3/` (PREREG), [`RESULTS.md`](RESULTS.md)", v3_status())] if v3_status() else []),
                    *([("Fourth test (v4): the rules decide, Nemotron only reads",
                        "`second_read/` (PREREG), `data/second_read/`, [`RESULTS.md`](RESULTS.md)", v4_status())]
                      if v4_status() else []),
                    ("Limitations", "[`RESULTS.md#limitations`](RESULTS.md#limitations)",
                     "simulated customers; a rulebook written for the simulator; one published false opt-out; and more"),
                    ("Platform spikes", "`evidence/spikes/`", "pre-registered plans, raw replies, results"),
                    ("Replay demo", "`demo/data/replay.json`",
                     f"{len(json.loads((ROOT / 'demo' / 'data' / 'replay.json').read_text())['scenarios'])} recorded "
                     "conversations picked by the rule in `demo/replay.py`, served step for step")]
            table = "\n".join(["| Evidence | Where | What it shows |", "|---|---|---|"] + [f"| {a_} | {b_} | {c_} |" for a_, b_, c_ in rows])
            txt = txt[:txt.index(ea)] + ea + "\n" + table + "\n" + txt[txt.index(eb):]
        readme.write_text(txt)
    print(f"wrote {out_path or ROOT / 'RESULTS.md'}: {len(L)} lines")
    return 0

if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    sys.exit({"smoke": cmd_smoke, "ledger": cmd_ledger, "after": cmd_after, "watch": cmd_watch,
              "results-md": cmd_results_md}[cmd](*args))
