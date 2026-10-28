"""Score a reader on the dev split: exact Facts match, per-field accuracy, per stratum, and the offer match rate.

    python -m second_read.rules_dev_score                    # the hardened rule reader and the SmartRules start point
    python -m second_read.rules_dev_score --reader rules     # one reader
    python -m second_read.rules_dev_score --failures 30      # also print up to 30 mismatching lines of the first reader
    python -m second_read.rules_dev_score --json out.json    # also write the scores as JSON

Only for code readers (no calls are made). Fields are compared after the contract's validation: ceilings as numbers,
drop items as sorted lists, dates as strings; the decider is compared case-insensitively ("HOA" == "hoa"), because
the offer only uses whether there is one, and its name is reported as a field of its own. "offer" is the share of lines
whose offer (second_read/offer.py, the shop's code shared by both arms) equals the offer of the gold Facts.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from . import cases as C
from .contract import FIELDS, parse

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data" / "second_read" / "dev.jsonl"
STRATA = ("S1", "S2", "S3", "S4")


def _norm(field, v):
    if field == "decider":
        return v.lower() if isinstance(v, str) else v
    if field == "ceiling_usd" and v is not None:
        return round(float(v), 2)
    if field == "drop_items":
        return sorted(v or [])
    return v


def field_hits(read, gold):
    """{field: bool} for one line; every field is a miss when the reader failed (read is None)."""
    if read is None:
        return {f: False for f in FIELDS}
    return {f: _norm(f, read[f]) == _norm(f, gold[f]) for f in FIELDS}


def readers(names):
    from .readers.rules import RuleReader, SmartRulesPatternReader
    known = {"rules": RuleReader, "smart_rules_patterns": SmartRulesPatternReader}
    return [known[n]() for n in names]


def _offer_module():
    try:
        from . import offer as O
    except ImportError:
        return None
    return O


def score(reader, lines):
    O = _offer_module()
    rows = []
    for case in lines:
        card = C.card_for(case)
        est = card.estimate()
        gold = parse(case["gold"], [i.name for i in est.items if i.optional])
        facts, meta = reader.read(case)
        read = facts.to_dict() if facts is not None else None
        hits = field_hits(read, gold.to_dict())
        row = {"id": case["id"], "stratum": case["stratum"], "text": case["text"], "read": read,
               "gold": gold.to_dict(), "fields": hits, "exact": all(hits.values()), "error": meta.get("error")}
        if O is not None:
            g_off = O.offer(gold, card, case["today"])
            r_off = O.offer(facts, card, case["today"]) if facts is not None else None
            row["offer"] = O.match(r_off, g_off)
        rows.append(row)
    return rows


def summary(rows):
    def rate(rs, key):
        return round(sum(1 for r in rs if key(r)) / len(rs), 4) if rs else None

    out = {"lines": len(rows), "exact": rate(rows, lambda r: r["exact"]),
           "fields": {f: rate(rows, lambda r, f=f: r["fields"][f]) for f in FIELDS},
           "offer": rate(rows, lambda r: r["offer"]) if rows and "offer" in rows[0] else None, "strata": {},
           "failures": sum(1 for r in rows if r["read"] is None)}
    by = defaultdict(list)
    for r in rows:
        by[r["stratum"]].append(r)
    for s in STRATA:
        rs = by.get(s, [])
        out["strata"][s] = {"lines": len(rs), "exact": rate(rs, lambda r: r["exact"]),
                            "offer": rate(rs, lambda r: r["offer"]) if rs and "offer" in rs[0] else None}
    gold_stop = [r for r in rows if r["gold"]["stop"]]
    out["stop"] = {"gold_stop_lines": len(gold_stop),
                   "missed_stop": sum(1 for r in gold_stop if not (r["read"] and r["read"]["stop"])),
                   "false_stop": sum(1 for r in rows if not r["gold"]["stop"] and r["read"] and r["read"]["stop"])}
    return out


def _pct(x):
    return "   -  " if x is None else f"{100 * x:5.1f}%"


def table(results):
    names = list(results)
    w = max(12, *(len(n) for n in names))
    head = f"{'':24}" + "".join(f"{n:>{w + 2}}" for n in names)
    lines = [head, "-" * len(head)]

    def row(label, get):
        lines.append(f"{label:24}" + "".join(f"{_pct(get(results[n])):>{w + 2}}" for n in names))

    row("exact Facts", lambda s: s["exact"])
    row("offer match", lambda s: s["offer"])
    for f in FIELDS:
        row(f"  field {f}", lambda s, f=f: s["fields"][f])
    for st in STRATA:
        row(f"  {st} exact (n={results[names[0]]['strata'][st]['lines']})", lambda s, st=st: s["strata"][st]["exact"])
        row(f"  {st} offer", lambda s, st=st: s["strata"][st]["offer"])
    lines.append(f"{'missed stop / false stop':24}" + "".join(
        f"{str(results[n]['stop']['missed_stop']) + ' / ' + str(results[n]['stop']['false_stop']):>{w + 2}}"
        for n in names))
    lines.append(f"{'reader failures':24}" + "".join(f"{results[n]['failures']:>{w + 2}}" for n in names))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--reader", action="append", choices=("rules", "smart_rules_patterns"))
    ap.add_argument("--dev", default=str(DEV))
    ap.add_argument("--failures", type=int, default=0)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    names = a.reader or ["rules", "smart_rules_patterns"]
    lines = C.load(a.dev)
    all_rows = {r.name: score(r, lines) for r in readers(names)}
    results = {n: summary(rows) for n, rows in all_rows.items()}
    print(f"dev lines: {len(lines)}  ({a.dev})")
    if results[names[0]]["offer"] is None:
        print("offer match: second_read/offer.py is not on this branch; Facts only")
    print(table(results))
    if a.failures:
        bad = [r for r in all_rows[names[0]] if not r["exact"]][: a.failures]
        for r in bad:
            diff = {f: (r["read"] or {}).get(f) for f in FIELDS if not r["fields"][f]}
            want = {f: r["gold"][f] for f in FIELDS if not r["fields"][f]}
            print(f"\n{r['id']} {r['stratum']} offer={'ok' if r.get('offer') else 'MISS'}: {r['text']}\n"
                  f"   read {diff}\n   gold {want}")
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1))
    return results


if __name__ == "__main__":
    main()
