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
    ended = [(r["buyer"], r["policy"], r["end"]) for r in rows if r["type"] != "opt_out" and "opted out" in (r.get("end") or "")]
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
    for r in rows:
        if r["type"] != "opt_out" and "opted out" in (r.get("end") or ""):
            ended[r["policy"]] += 1
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
    return f"{_usd(c['point'])} [{_usd(c['lo'])}, {_usd(c['hi'])}]"


def _verdict(c):
    if c["lo"] > 0:
        return "ahead (interval above 0)"
    if c["hi"] < 0:
        return "behind (interval below 0)"
    return "no clear difference (interval includes 0)"


def cmd_results_md(d, out_path=None):
    """RESULTS.md: tables from RESULTS-tables.md verbatim, every other number from the run files. Prose is fixed here."""
    d = Path(d).resolve()
    res = json.loads((d / "results.json").read_text())
    tables = (d / "RESULTS-tables.md").read_text().split("\n", 2)[2]  # drop the generated title line
    rows, outs, state, report, _ = load(d)
    m = json.loads((ROOT / "tournament" / "MANIFEST.json").read_text())
    sha = (ROOT / "tournament" / "MANIFEST.sha256").read_text().split()[0]
    after_ended = defaultdict(int)
    for r in rows:
        if r["type"] != "opt_out" and "opted out" in (r.get("end") or ""):
            after_ended[r["policy"]] += 1
    fo = false_opt_outs(outs)
    dups = outcome_duplicates(outs)
    ledg = ledger_summary(state.get("started"))
    types = {r["buyer"]: r["type"] for r in rows}
    excl_types = dict(Counter(types.get(b) for b in report.get("excluded_buyers", [])))
    agg, comps = res.get("aggregate", {}), res.get("comparisons", {})
    smoke_rows, smoke_outs, smoke_state, smoke_report, _ = load(ROOT / "tournament" / "smoke-b")
    old = {(o["buyer"], o["policy"]): o for o in R.read_lines(ROOT / "tournament" / "smoke" / "outcomes.jsonl")[0]}
    L = [f"# Second Ask: tournament results", "",
         f"Pre-registered manifest `tournament/MANIFEST.json`, sha256 `{sha}` (changes from the first manifest: "
         f"`tournament/CHANGES.md`). Run directory `tournament/full/`: started {state.get('started')}, finished "
         f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime((d / 'run_report.json').stat().st_mtime))}, "
         f"stop: **{report.get('stop')}**; {report.get('rows')} conversation rows ({report.get('clean_rows')} clean, "
         f"{report.get('errored_rows')} errored); spend in the run window {_usd(ledg['usd'], 4)} by the shared ledger.",
         "", "Every number on this page is computed from `tournament/full/rows.jsonl` and `outcomes.jsonl` by "
         "`python3 -m second_ask.tournament.run results` (tables) and `python3 tools/run_checks.py results-md` (this "
         "page). Nothing is typed by hand.", ""]
    if agg:
        llm = [p for p in ("routed", "super_only", "super_vl", "ultra_only") if p in agg]
        vs_shop = "; ".join(f"`{p}` {_ci(comps[p + ' - shop_rule'])}" for p in llm if comps.get(p + " - shop_rule"))
        vs_smart = "; ".join(f"`{p}` {_ci(comps[p + ' - smart_rules'])}: {_verdict(comps[p + ' - smart_rules'])}"
                             for p in llm if comps.get(p + " - smart_rules"))
        best = max(agg, key=lambda p: agg[p]["net"])
        L += ["## Headline", "",
              f"- Against the shop's old rule (`shop_rule`: discount to the floor at the first objection, then weekly "
              f"reminders), difference in net summed over {agg['shop_rule']['n']} buyers [95 % interval]: {vs_shop}.",
              f"- Against `smart_rules`, the strongest rule baseline we could write (pre-registered main comparison), "
              f"difference in net [95 % interval]: {vs_smart}.",
              f"- Highest net: `{best}` ({_usd(agg[best]['net'])}); `smart_rules` {_usd(agg['smart_rules']['net'])}; "
              f"`shop_rule` {_usd(agg['shop_rule']['net'])}.",
              "- Model $ per booking: " + "; ".join(f"`{p}` {_usd(agg[p]['usd_per_booking'], 4)}" for p in llm
                                                    if agg[p]["usd_per_booking"] is not None) + ".",
              f"- Default label model by the pre-registered rule: **{res['label_default']['default_label_model']}**."
              if res.get("label_default") else "", ""]
    L += ["## What was run", "",
          f"- {m['buyers']['n']} simulated homeowners (cards fixed by seed {m['buyers']['generator_seed']}, "
          f"`tournament/buyers.json` sha256 `{m['buyers']['sha256'][:12]}…`), each with a hidden walkaway price and a "
          f"reason for going quiet. Every buyer decision is code; a non-NVIDIA model "
          f"(`{m['buyer_simulator']['model']}`) only writes the buyer's words and never sees the walkaway.",
          f"- {len(m['policies'])} policies. `shop_rule` and `concession_heuristic` never read the buyer's words and "
          f"run once per buyer; `smart_rules` and the four Nemotron policies read them and run "
          f"{m['repeats']['k']} times on a fixed subsample of {len(m['repeats']['subsample'])} buyers.",
          "- Totals are per buyer first: each buyer's mean over its repeats, then the sum over buyers. Only buyers "
          "finished in every policy count.",
          "- **net** = margin earned on booked jobs minus the shop's model spend. **given away** = margin left on the "
          "table on booked jobs; **lost** = margin of buyers who could have booked and did not.",
          "- Main comparison (pre-registered): `routed` and `super_only` against `smart_rules`, the strongest rule "
          "baseline we could write without a model. Intervals: paired bootstrap over buyers, 2,000 draws, 95 %.", ""]
    if agg:
        L += ["## Main comparisons", "", "| comparison (net, summed over buyers) | difference [95 % interval] | buyers | reading |",
              "|---|---|---|---|"]
        for k in ["routed - smart_rules", "super_only - smart_rules", "ultra_only - smart_rules", "super_vl - smart_rules",
                  "shop_rule - smart_rules", "concession_heuristic - smart_rules"]:
            c = comps.get(k)
            if c:
                L.append(f"| {k} | {_ci(c)} | {c['buyers']} | {_verdict(c)} |")
        L += ["", "Against the shop's old rule (`shop_rule`), secondary:", "", "| comparison | difference [95 % interval] | buyers |",
              "|---|---|---|"]
        L += [f"| {k} | {_ci(c)} | {c['buyers']} |" for k, c in comps.items() if k.endswith("- shop_rule") and c]
        ld = res.get("label_default")
        if ld:
            c = ld["ci_routed_minus_super_only"]
            L += ["", "## Default label model (pre-registered rule)", "",
                  f"routed (Lightning labels) minus super_only (Super labels), net: {_ci(c)} over {c['buyers']} buyers. The "
                  f"rule keeps Lightning only if the lower bound is at or above -{_usd(ld['x_usd'])} and routed is not "
                  f"worse on violations, unsure and off-arm calls: the lower bound {'meets' if c['lo'] >= -ld['x_usd'] else 'misses'} "
                  f"the margin; " + "; ".join(f"{k} {agg['routed'][k]:.1f} vs {agg['super_only'][k]:.1f} "
                                             f"({'not worse' if ok else 'worse'})" for k, ok in ld["not_worse"].items())
                  + f". **Default label model: {ld['default_label_model']}.** "
                  + ("Non-inferiority was not shown, so the default stays Super, as the S2 rule requires. "
                     if ld["default_label_model"] == "Super" else "")
                  + "\"Not worse\" is a strict comparison of the summed counts; violations and off-arm calls are 0 by "
                  "construction in the rows that count (opt-outs are enforced in code; a conversation with an off-arm "
                  "call is not clean). routed and super_only differ in the label model, the label output format (JSON "
                  "object vs named tool) and the label fallback, not in the model alone.", ""]
    L += [tables, ""]
    cells = res.get("losing_cells") or []
    if cells:
        real = [c for c in cells if c["gap"] >= 1]
        final = [r for r in rows if R.is_clean(r)]
        same = 0
        for c in cells:
            if c["gap"] < 1:
                buyers = {r["buyer"] for r in final if (r["type"], r["trade"]) == (c["type"], c["trade"])}
                mg = lambda p: {b: round(v, 2) for b, v in S.per_buyer(final, p, "margin").items() if b in buyers}  # noqa: E731
                same += mg("routed") == mg(c["best"])
        L += [f"Of the {len(cells)} cells listed above, {len(cells) - len(real)} differ by less than $1 per buyer; in "
              f"{same} of them routed and the best policy earned the same margin from every buyer, so the gap is model "
              f"spend only. "
              f"Cells with a gap of $1 or more: {len(real)}; clear losses (n >= 10 and the whole interval below 0): "
              f"{sum(c['clear_loss'] for c in cells)}. The intervals are in dollars per buyer.", ""]
    if agg:
        L += ["## Compliance", "",
              "Violations = messages sent after the buyer asked to stop. Opt-out buyers' words come from code "
              "(published keyword and phrase lists), and the shared code layer catches every one of them, so 0 there "
              "is by construction for every policy, not a model result:", ""]
        for p, v in sorted(res.get("opt_out_violations", {}).items()):
            L.append(f"- {p}: " + "; ".join(f"{style} {x['violations']:.0f} violations over n = {x['n']}" for style, x in sorted(v.items())))
        L += ["", "Conversations of buyers who are not opt-out buyers that still ended on an opt-out (a buyer-model "
              "phrase read as a stop by the shared layer):", ""]
        L += [f"- {p}: {after_ended.get(p, 0)}" for p in sorted(agg)]
        L += ["", f"Buyer-model lines that the opt-out layer reads as a stop (type is not opt_out): {len(fo['opt_out'])} of "
              f"{fo['customer_lines_checked']}; read as ambiguous: {fo['ambiguous']}."]
        if any(after_ended.get(p, 0) for p in ("shop_rule", "concession_heuristic")):
            L += ["", "Caveat on running the text-blind rules once: their outcome is fixed by the cards except where the "
                  "shared opt-out layer reads a buyer-model sentence as a stop (counted above)."]
        L += ["", "## Unsure flags (messages sent despite a flag)", "", "| policy | flags by kind |", "|---|---|"]
        L += [f"| {p} | {', '.join(f'{k} {n}' for k, n in sorted(v.items())) or '-'} |" for p, v in sorted(res.get("unsure_flags", {}).items())]
    L += ["", "## Integrity", "",
          f"- Errored rows {report.get('errored_rows')}, excluded buyers {report.get('excluded_buyers')} (by type "
          f"{excl_types or '-'}), re-runs {res['kept']['reruns']}, buyers kept {res['kept']['buyers_kept']} of "
          f"{res['kept']['buyers_planned']}.",
          f"- Token Factory error replies in the run window, by model and code: {ledg['releases'] or 'none'}; "
          f"reservations kept (network or malformed replies, no usage, or in flight at the end): "
          f"{ledg['kept_reservations'] or 'none'}. Calls by model: {ledg['calls_by_model']}.",
          f"- Duplicate outcome lines (interrupted between the two writes): {dups or 'none'}.",
          f"- Usage-based cost of the kept conversations: shop {_usd(sum(o['seller_cost'] for o in outs), 4)}, buyer "
          f"writer {_usd(sum(o['buyer_cost'] for o in outs), 4)}; the ledger window ({_usd(ledg['usd'], 4)}) also holds "
          f"the errored attempts' calls and the reservations kept on network-level retries.",
          "", "| policy | attempts | errored | platform failures | off-arm calls | fallback labels | chronology errors | "
          "buyer text from the template |", "|---|---|---|---|---|---|---|---|"]
    for p, g in sorted(res.get("integrity_all_attempts", {}).items()):
        L.append(f"| {p} | {g['attempts']:.0f} | {g['errored']:.0f} | {g['platform_failures']:.0f} | {g['off_arm_calls']:.0f} "
                 f"| {g['label_fallback_calls']:.0f} | {g['chronology_errors']:.0f} | {g['writer_fallbacks']:.0f} |")
    gates = res.get("sanity_gates")
    if gates:
        L += ["", "## Simulator gates (text-blind rule policies)", "",
              f"- Booking rate in 30–80 %: {gates['booking_rate_by_policy']} -> {gates['booking_rate_in_30_80']}.",
              f"- Price buyers' booking rate by walkaway tercile: {[round(x, 3) for x in gates['price_buyers_booking_rate_by_walkaway_tercile']]} "
              f"(rises: {gates['rises_with_walkaway']}); spread by type >= 0.20: {gates['differs_by_type']}.",
              f"- Bookings above the walkaway {gates['bookings_above_walkaway']}, below the floor "
              f"{gates['bookings_below_floor']}: both hold by construction (integrity checks, not evidence of realism)."]
    L += ["", "## Smoke before the run (mechanics and cost only)", "",
          f"`tournament/smoke-b/`: {smoke_report.get('rows')} conversations, {smoke_report.get('clean_rows')} clean, "
          f"{smoke_report.get('errored_rows')} errored, spend {_usd(smoke_report.get('spent_usd', 0), 4)}. Not results: "
          "its buyers are two per type, not the population mix, and `smart_rules` and `concession_heuristic` were not in "
          "it (their only cost is the buyer writer; their mechanics are covered by the dry run and the tests). The 10 "
          "phase-A conversations, re-run on the frozen code (phase A ran before three simulator fixes):", ""]
    for r in smoke_rows:
        o = old.get((r["buyer"], r["policy"]))
        if o:
            L.append(f"- {r['buyer']} {r['policy']}: phase A booked={o['booked']} price={o['price']} end={o['end']!r}; "
                     f"now booked={r['booked']} price={r['price']} end={r['end']!r}")
    prov = m["provenance"]
    L += ["", "## What is real, what is seeded, what is simulated", "",
          "- Real: " + "; ".join(prov["real"]) + ".", "- Seeded: " + "; ".join(prov["seeded"]) + ".",
          "- Simulated: " + "; ".join(prov["simulated"]) + ".", "",
          "## Limitations", "",
          "- The buyers are simulated: their decisions are code drawn from a seeded card, so the numbers say how the "
          "policies do against this simulator, not against real homeowners.",
          "- `smart_rules` was written by us against the simulator's own instruction vocabulary (\"need it done by\", "
          "\"in about N weeks\", named amounts). That favours the baseline and makes the comparison conservative for the "
          "Nemotron policies.",
          "- Only channel buyers have an unopened estimate and silence; there are no opened-and-silent buyers.",
          "- Ultra is not routed in (spike S7: Super with reasoning chose at least as well, at about a quarter of the price); "
          "`ultra_only` is a separate arm, not part of `routed`."
          + (f" In this tournament `ultra_only`'s total net is {_usd(agg['ultra_only']['net'] - agg['super_only']['net'])} "
             f"above `super_only`'s at {_usd(agg['ultra_only']['model_usd'] - agg['super_only']['model_usd'], 2)} more "
             f"model spend; that comparison was not pre-registered and is descriptive only."
             if agg and agg['ultra_only']['net'] > agg['super_only']['net'] else ""),
          "- given away splits into money given to buyers who would have paid the full list "
          "(`given_to_full_payers`) and the gap the menu's discrete prices cannot reach (`menu_gap`).",
          "- In the losing-cells table the best policy of each cell is picked in-sample, which biases toward a loss; a "
          "clear loss needs n >= 10 and a Bonferroni interval below 0.",
          "- The compliance rules are applied conservatively in code and are not legal advice; whether Fla. Stat. "
          "§501.616(6) covers SMS follow-ups to an estimate is not confirmed.",
          "- Shop and trade names are made up; any real business with the same name is unrelated.", "",
          "## Reproduce", "",
          "```", "python3 -m pytest -q                                        # offline tests",
          "python3 -m second_ask.tournament.run dry-run                 # fake models, mechanics only",
          f"python3 -m second_ask.tournament.run full --approve {sha}   # live, needs NEBIUS_API_KEY",
          "python3 -m second_ask.tournament.run results                 # tables from tournament/full/rows.jsonl",
          "python3 tools/run_checks.py results-md tournament/full       # this page", "```", ""]
    (Path(out_path) if out_path else ROOT / "RESULTS.md").write_text("\n".join(L))
    print(f"wrote {out_path or ROOT / 'RESULTS.md'}: {len(L)} lines")
    return 0


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    sys.exit({"smoke": cmd_smoke, "ledger": cmd_ledger, "after": cmd_after, "watch": cmd_watch,
              "results-md": cmd_results_md}[cmd](*args))
