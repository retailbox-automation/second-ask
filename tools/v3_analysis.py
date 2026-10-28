"""v3 analysis (pre-registered in tournament/v3/PREREG.md; its sha256 is in tournament/v3/MANIFEST.json).

    python3 tools/v3_analysis.py [RUN_DIR]     default tournament/v3/full -> v3-results.json and V3-RESULTS.md there

Reads only the run's rows.jsonl and outcomes.jsonl, the v3 manifest and, for one descriptive comparison, v1's rows.
Prints nothing about outcomes before the full run is finished: it refuses a run whose report does not say complete.
"""
import json, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from second_ask.tournament import manifest as MF, runner as R, scoring as S  # noqa: E402
from second_ask.tournament.policies import HYBRID, SMART  # noqa: E402

OPT_OUT_STOP = "policy stopped: label model read an opt-out"


def false_opt_out(row):
    return row["type"] != "opt_out" and (row.get("end") or "").startswith(OPT_OUT_STOP)


def per_buyer_sum(rows, policy, fn):
    """Each customer's mean over its repeats of fn(row), summed over customers."""
    by = defaultdict(list)
    for r in rows:
        if r["policy"] == policy:
            by[r["buyer"]].append(fn(r))
    return sum(sum(v) / len(v) for v in by.values())


def verdict(d3, worse):
    if d3["lo"] > 0 and not worse:
        return "beats"
    if d3["hi"] < 0:
        return "behind"
    return "no clear difference"


def handoffs(outcomes, kept):
    """Every hand-off of the hybrid in the kept conversations: branch, the rule's move, the model's move or stop."""
    out = []
    for o in outcomes:
        if o["policy"] != HYBRID or (o["buyer"], o["policy"], o["repeat"], o.get("attempt")) not in kept:
            continue
        for s in o["steps"]:
            if s.get("step") == "handoff":
                out.append(dict(json.loads(s["note"]), buyer=o["buyer"], type=o["type"], repeat=o["repeat"]))
    return out


def analyse(run_dir, manifest, jobs, v1_rows=None):
    """Every number V3-RESULTS.md shows, from the run's rows and outcomes, the planned jobs and the manifest."""
    rows, _ = R.read_lines(run_dir / "rows.jsonl")
    outcomes, _ = R.read_lines(run_dir / "outcomes.jsonl")
    pols = manifest["run_policies"]
    final, info = R.final_rows(rows, jobs)
    kept = {(r["buyer"], r["policy"], r["repeat"], r["attempt"]) for r in final}
    res = {"manifest_sha256": MF.digest(manifest), "kept": info}
    d3 = S.paired_bootstrap(final, HYBRID, SMART)
    table = {}
    for p in pols:
        table[p] = {
            "buyers": len({r["buyer"] for r in final if r["policy"] == p}),
            "net": per_buyer_sum(final, p, lambda r: r["net"]),
            "bookings": per_buyer_sum(final, p, lambda r: float(r["booked"])),
            "model_usd": per_buyer_sum(final, p, lambda r: r.get("model_usd") or 0.0),
            "violations": per_buyer_sum(final, p, lambda r: r.get("violations") or 0),
            "false_opt_outs": per_buyer_sum(final, p, lambda r: float(false_opt_out(r))),
            "false_opt_out_conversations": sum(false_opt_out(r) for r in final if r["policy"] == p),
            "off_arm_calls": per_buyer_sum(final, p, lambda r: r.get("off_arm_calls") or 0),
            "unsure": per_buyer_sum(final, p, lambda r: r.get("unsure") or 0)}
    worse = any(table[HYBRID][k] > table[SMART][k] for k in ("violations", "false_opt_outs", "off_arm_calls"))
    res.update(d3=d3, d3_per_customer={k: d3[k] / d3["buyers"] for k in ("point", "lo", "hi")} if d3 else None,
               not_worse_failed_on=[k for k in ("violations", "false_opt_outs", "off_arm_calls")
                                    if table[HYBRID][k] > table[SMART][k]],
               verdict=verdict(d3, worse) if d3 else "no data", table=table)
    hs = handoffs(outcomes, kept)
    res["handoffs"] = {"total": len(hs), "by_branch": dict(Counter(h["branch"] for h in hs)),
                       "by_type": dict(Counter(h["type"] for h in hs)),
                       "model_move_differs": sum(h["model_move"] != h["rule_move"] for h in hs),
                       "model_stopped": dict(Counter(h["stop"] for h in hs if h["model_move"] is None)),
                       "pairs": dict(Counter(f"{h['rule_move']} -> {h['model_move'] or 'stop'}" for h in hs)),
                       "conversations_with_handoff": len({(h["buyer"], h["repeat"]) for h in hs})}
    res["false_opt_outs"] = [{"buyer": r["buyer"], "type": r["type"], "repeat": r["repeat"], "end": r["end"]}
                             for r in final if r["policy"] == HYBRID and false_opt_out(r)]
    res["by_type"] = {t: S.paired_bootstrap([r for r in final if r["type"] == t], HYBRID, SMART)
                      for t in sorted({r["type"] for r in final})}
    if v1_rows is not None:
        v1 = [dict(r, policy="smart_rules_v1") for r in v1_rows if r["policy"] == SMART and R.is_clean(r)]
        res["vs_v1_smart_rules"] = S.paired_bootstrap([r for r in final if r["policy"] == HYBRID] + v1, HYBRID,
                                                      "smart_rules_v1")
    return res


def usd(x, d=0):
    return f"{'-' if x < 0 else '+'}${abs(x):,.{d}f}"


def ci(c, n=None):
    if not c:
        return "-"
    s = f"{usd(c['point'])} [{usd(c['lo'])}, {usd(c['hi'])}]"
    return s + (f" ({usd(c['point'] / n)} [{usd(c['lo'] / n)}, {usd(c['hi'] / n)}] per customer)" if n else "")


def markdown(res):
    t, d3 = res["table"], res["d3"]
    h = res["handoffs"]
    L = ["# v3 results: rules first, Nemotron only where the rules do not know", "",
         f"Computed by `tools/v3_analysis.py` from the run's rows only (manifest sha256 `{res['manifest_sha256']}`).", "",
         f"**Verdict (pre-registered rule): {res['verdict']}.** D3 = {HYBRID} - {SMART}, net summed over "
         f"{d3['buyers']} customers [95 % paired bootstrap]: {ci(d3, d3['buyers'])}."
         + (f" Not-worse check failed on: {', '.join(res['not_worse_failed_on'])}." if res["not_worse_failed_on"] else ""),
         "", "| policy | customers | bookings | net $ | model $ | violations | false opt-outs (conversations) | off-arm | unsure |",
         "|---|---|---|---|---|---|---|---|---|"]
    for p, a in t.items():
        L.append(f"| {p} | {a['buyers']} | {a['bookings']:.1f} | {a['net']:,.0f} | {a['model_usd']:.4f} | "
                 f"{a['violations']:.1f} | {a['false_opt_outs']:.2f} ({a['false_opt_out_conversations']}) | "
                 f"{a['off_arm_calls']:.1f} | {a['unsure']:.1f} |")
    L += ["", "Each customer's mean over its repeats, summed over customers. All $ are simulated margin on illustrative "
          "prices (not market data).", "", "## Hand-offs to Nemotron (descriptive)", "",
          f"- {h['total']} hand-offs in {h['conversations_with_handoff']} conversations; by rule: {h['by_branch']}; by type: "
          f"{h['by_type']}.",
          f"- Nemotron's move differed from the rule's move in {h['model_move_differs']} of {h['total']}; stops: "
          f"{h['model_stopped'] or 'none'}.",
          "- rule move -> model move: " + "; ".join(f"{k} x{v}" for k, v in sorted(h["pairs"].items(), key=lambda x: -x[1])),
          "", "## By objection type (descriptive, not adjusted)", "", "| type | D3 [95 %] | customers |", "|---|---|---|"]
    L += [f"| {k} | {ci(v)} | {v['buyers']} |" for k, v in res["by_type"].items() if v]
    if res.get("vs_v1_smart_rules"):
        L += ["", f"Against v1's smart_rules rows (independent buyer words, descriptive): {ci(res['vs_v1_smart_rules'])}."]
    if res["false_opt_outs"]:
        L += ["", "## False opt-outs by Nemotron", ""] + [f"- {f['buyer']} ({f['type']}), repeat {f['repeat']}: {f['end']}"
                                                         for f in res["false_opt_outs"]]
    return "\n".join(L) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    run_dir = Path(argv[0]).resolve() if argv else ROOT / "tournament" / "v3" / "full"
    report = json.loads((run_dir / "run_report.json").read_text())
    if report.get("stop") != "complete":
        print(f"refusing: the run in {run_dir} is not complete ({report.get('stop')})")
        return 2
    manifest = json.loads((ROOT / MF.V3_PREREG).parent.joinpath("MANIFEST.json").read_text())
    ids = [c["id"] for c in json.loads((ROOT / manifest["buyers"]["file"]).read_text())]
    jobs = R.plan(ids, manifest["run_policies"], manifest["repeats"]["repeated"], manifest["repeats"]["k"],
                  manifest["repeats"]["subsample"])
    v1_rows, _ = R.read_lines(ROOT / MF.V1_ROWS)
    res = analyse(run_dir, manifest, jobs, v1_rows)
    (run_dir / "v3-results.json").write_text(json.dumps(res, indent=1) + "\n")
    (run_dir / "V3-RESULTS.md").write_text(markdown(res))
    print(markdown(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
