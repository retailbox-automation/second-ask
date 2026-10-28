"""Score a Nemotron reader on the dev split (prompt building only; never the test split).

    python -m second_read.nemotron_dev_score [--arm super|lightning|ultra|super_vl] [--n N] [--ids FILE]
                                             [--failures FILE] [--rows FILE] [--max-usd X]

Reads every dev line (concurrency 8) through the arm's reader with the current SYSTEM_PROMPT, then reports:
  offer match   offer(read) == offer(gold), the primary metric of V4-DESIGN §3
  exact Facts   every field equal (decider compared by presence: the offer only uses whether someone else decides)
  per field and per stratum, $ spent (sum of the replies' cost) and p50 latency.
Failures (non-matching offers, Facts that differ from gold and reader failures, with the text, gold and read Facts) go to --failures as JSON lines
for inspection; that file is scratch, not committed. Every call goes through Client("b2", max_attempts=4), so the shared
ledger's caps hold. --max-usd stops starting new lines once this run's own spend passes it.
"""
import argparse, json, statistics, sys, threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from second_ask.tf_client import BudgetExceeded, Client

from . import cases as C, offer as O
from .contract import FIELDS
from .readers import nemotron as N
from .run import NEMOTRON_ARMS, read_row

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data" / "second_read" / "dev.jsonl"
CONCURRENCY = 8


def field_equal(name, read, gold):
    a, b = read.get(name), gold.get(name)
    if name == "decider":
        return (a is None) == (b is None)
    if name == "ceiling_usd":
        return (a is None and b is None) or (a is not None and b is not None and round(a, 2) == round(b, 2))
    if name == "drop_items":
        return sorted(a or []) == sorted(b or [])
    return a == b


def gold_dict(case):
    return O.as_facts(case["gold"], C.card_for(case).estimate()).to_dict()


def score(rows, cases_by_id):
    """Summary dict over finished rows."""
    out = {"lines": len(rows), "offer_match": 0, "exact_facts": 0, "failures": 0, "usd": 0.0,
           "field": {f: 0 for f in FIELDS}, "stratum": defaultdict(lambda: {"lines": 0, "offer_match": 0, "exact": 0}),
           "false_stop": 0, "missed_stop": 0}
    secs = []
    for r in rows:
        case = cases_by_id[r["id"]]
        gold = gold_dict(case)
        st = out["stratum"][case["stratum"]]
        st["lines"] += 1
        out["usd"] += r["usd"]
        if r.get("seconds"):
            secs.append(r["seconds"])
        if r["match"]:
            out["offer_match"] += 1
            st["offer_match"] += 1
        if r["facts"] is None:
            out["failures"] += 1
            continue
        eq = {f: field_equal(f, r["facts"], gold) for f in FIELDS}
        for f, ok in eq.items():
            out["field"][f] += ok
        if all(eq.values()):
            out["exact_facts"] += 1
            st["exact"] += 1
        out["false_stop"] += (not gold["stop"]) and r["facts"]["stop"]
        out["missed_stop"] += gold["stop"] and not r["facts"]["stop"]
    out["p50_seconds"] = round(statistics.median(secs), 3) if secs else None
    out["usd"] = round(out["usd"], 4)
    out["stratum"] = dict(sorted(out["stratum"].items()))
    return out


def report(s, arm):
    n = s["lines"] or 1
    pct = lambda k, d=n: f"{100 * k / d:.1f}%"
    lines = [f"arm {arm} ({NEMOTRON_ARMS[arm][0]}, {NEMOTRON_ARMS[arm][1]}): {s['lines']} lines",
             f"offer match  {s['offer_match']}/{s['lines']} = {pct(s['offer_match'])}",
             f"exact Facts  {s['exact_facts']}/{s['lines']} = {pct(s['exact_facts'])}",
             f"reader failures {s['failures']}; false stop {s['false_stop']}; missed stop {s['missed_stop']}",
             f"$ spent {s['usd']:.4f}; p50 latency {s['p50_seconds']} s", "per field:"]
    lines += [f"  {f:12s} {s['field'][f]}/{s['lines']} = {pct(s['field'][f])}" for f in FIELDS]
    lines.append("per stratum (offer match / exact Facts):")
    for k, v in s["stratum"].items():
        lines.append(f"  {k}: {v['offer_match']}/{v['lines']} = {pct(v['offer_match'], v['lines'])}"
                     f"   exact {v['exact']}/{v['lines']} = {pct(v['exact'], v['lines'])}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="super", choices=sorted(NEMOTRON_ARMS))
    ap.add_argument("--n", type=int, default=None, help="first N dev lines only")
    ap.add_argument("--ids", default=None, help="file with one dev line id per line (subset)")
    ap.add_argument("--failures", default=None, help="write failing lines here (JSON lines, scratch)")
    ap.add_argument("--rows", default=None, help="write every row here (JSON lines, scratch)")
    ap.add_argument("--summary", default=None, help="write the summary JSON here")
    ap.add_argument("--max-usd", type=float, default=0.30, help="stop starting new lines past this run's own spend")
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY)
    a = ap.parse_args(argv)

    cases = C.load(DEV)
    if a.ids:
        keep = {x.strip() for x in Path(a.ids).read_text().split() if x.strip()}
        cases = [c for c in cases if c["id"] in keep]
    if a.n:
        cases = cases[:a.n]
    by_id = {c["id"]: c for c in cases}
    model, method = NEMOTRON_ARMS[a.arm]
    client = Client("b2", max_attempts=4, log=lambda m: print(m, file=sys.stderr))
    reader = N.NemotronReader(model, method, client=client, name=a.arm)
    lock, spent, rows, stop = threading.Lock(), [0.0], [], [None]

    def one(case):
        with lock:
            if stop[0] or spent[0] >= a.max_usd:
                stop[0] = stop[0] or "max-usd"
                return
        try:
            row = read_row(case, a.arm, reader, "dev")
        except BudgetExceeded as e:
            stop[0] = f"budget: {e}"
            return
        with lock:
            spent[0] += row["usd"]
            rows.append(row)

    with ThreadPoolExecutor(a.concurrency) as ex:
        list(ex.map(one, cases))
    rows.sort(key=lambda r: r["id"])
    s = score(rows, by_id)
    s["stopped"] = stop[0]
    s["arm"], s["model"], s["method"] = a.arm, model, method
    print(report(s, a.arm))
    if stop[0]:
        print(f"STOPPED EARLY: {stop[0]} ({len(rows)}/{len(cases)} lines)")
    if a.summary:
        Path(a.summary).write_text(json.dumps(s, indent=1, default=dict))
    if a.rows:
        Path(a.rows).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    if a.failures:
        with open(a.failures, "w") as f:
            for r in rows:
                case = by_id[r["id"]]
                gold = gold_dict(case)
                diff = [k for k in FIELDS if r["facts"] is None or not field_equal(k, r["facts"], gold)]
                if r["match"] and not diff:
                    continue
                f.write(json.dumps({"id": r["id"], "offer_match": r["match"], "stratum": case["stratum"], "today": f"{case['weekday']} {case['today']}",
                                    "text": case["text"], "diff": diff, "gold": gold, "read": r["facts"],
                                    "error": r["meta"].get("error"), "offer": r["offer"], "gold_offer": r["gold_offer"]},
                                   ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
