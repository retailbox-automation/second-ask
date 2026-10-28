"""Second Read (v4) pre-registered analysis, computed from the run's rows and the split file only.

Primary: paired difference in match rate, Nemotron Super reader minus the hardened rule reader, over every line of the
split (a reader failure is a non-match), with a paired bootstrap over lines (`second_ask.tournament.scoring.
paired_bootstrap`, 2,000 draws, seed 7) and the decision rule in prereg.DECISION_RULE. A missed stop is a line whose
gold Facts say stop where the arm's read did not (a reader failure on such a line counts as missed: the stop was not
read). Everything else is descriptive.
"""
import math, statistics

from second_ask.tournament.scoring import paired_bootstrap

from . import offer as O, prereg as P
from .cases import card_for
from .contract import FactsError


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(100 * (mid - half), 2), round(100 * (mid + half), 2)]


def verdict(diff_pp, lo_pp, hi_pp, missed_super, missed_rules):
    """prereg.DECISION_RULE as code."""
    if diff_pp >= P.MIN_GAIN_PP and lo_pp > 0 and missed_super <= missed_rules:
        return P.BEATS
    if hi_pp < 0:
        return P.BEHIND
    return P.UNCLEAR


def paired(by_arm, a, b, ids):
    """Paired difference in match rate a - b (percentage points) over `ids`, with the pre-registered bootstrap."""
    rows = [{"policy": arm, "buyer": i, "match": float(by_arm[arm][i]["match"])} for arm in (a, b) for i in ids]
    if not ids:
        return None
    bs = paired_bootstrap(rows, a, baseline=b, metric="match", draws=P.BOOTSTRAP["draws"], seed=P.BOOTSTRAP["seed"])
    n = bs["buyers"]
    return {"n": n, "diff_pp": round(100 * bs["point"] / n, 2), "lo_pp": round(100 * bs["lo"] / n, 2),
            "hi_pp": round(100 * bs["hi"] / n, 2)}


def missed_stops(rows):
    return sum(1 for r in rows if r["gold_stop"] and not r["read_stop"])


def false_stops(rows):
    return sum(1 for r in rows if not r["gold_stop"] and r["read_stop"])


def _arm_block(rows, n_lines):
    n = len(rows)
    k = sum(1 for r in rows if r["match"])
    usd = sum(r.get("usd") or 0.0 for r in rows)
    secs = [r["seconds"] for r in rows if r.get("seconds")]
    m = [r["money"] for r in rows]
    per100 = (lambda key: round(100 * sum(x[key] for x in m) / n, 2)) if n else (lambda key: None)
    return {"n": n, "missing_lines": n_lines - n, "match": k, "match_rate_pct": round(100 * k / n, 2) if n else None,
            "match_ci95_pct": wilson(k, n), "reader_failures": sum(1 for r in rows if r["failure"]),
            "failures_by_kind": {kind: sum(1 for r in rows if r["failure"] == kind) for kind in ("invalid", "platform")},
            "missed_stops": missed_stops(rows), "missed_stops_from_failures": sum(1 for r in rows if r["gold_stop"]
                                                                                   and r["failure"]),
            "false_stops": false_stops(rows),
            "money_per_100": {"booked": per100("booked"), "margin_usd": per100("margin"),
                              "given_away_usd": per100("given_away"), "gained_usd": per100("gained"),
                              "lost_usd": per100("lost"), "messages_after_stop": sum(x["message_after_stop"] for x in m)},
            "usd_total": round(usd, 4), "usd_per_1000": round(1000 * usd / n, 4) if n else None,
            "p50_seconds": round(statistics.median(secs), 3) if secs else None}


def fidelity_agree(case):
    """Did the blind fidelity judge's read give the gold offer? None when the line carries no judge read."""
    fid = case.get("fidelity") or {}
    if isinstance(fid.get("agree"), bool):
        return fid["agree"]
    if isinstance(fid.get("facts"), dict):
        card = card_for(case)
        try:
            return O.match(O.offer(fid["facts"], card, case["today"]), O.offer(case["gold"], card, case["today"]))
        except FactsError:
            return False
    return None


def analyze(cases, rows, split):
    ids = [c["id"] for c in cases]
    by_id = {c["id"]: c for c in cases}
    by_arm = {}
    for r in rows:
        if r["id"] in by_id:
            by_arm.setdefault(r["arm"], {})[r["id"]] = r
    arms = [a for a in P.ARMS if a in by_arm] + sorted(set(by_arm) - set(P.ARMS))
    res = {"split": split, "lines": len(ids), "decision_rule": P.DECISION_RULE, "arms": {}, "primary": None,
           "verdict": None, "per_stratum": {}, "fidelity": None, "sensitivity_judge_agreed": None}
    for a in arms:
        res["arms"][a] = _arm_block(list(by_arm[a].values()), len(ids))
    gold_money = [O.money(card_for(by_id[i]), O.offer(by_id[i]["gold"], card_for(by_id[i]), by_id[i]["today"]),
                          by_id[i]["gold"], by_id[i]["today"]) for i in ids]
    res["gold_reference_per_100"] = {"booked": round(100 * sum(m["booked"] for m in gold_money) / len(ids), 2) if ids else None,
                                     "margin_usd": round(100 * sum(m["margin"] for m in gold_money) / len(ids), 2) if ids else None}
    a, b = P.PRIMARY
    if a in by_arm and b in by_arm:
        complete = all(i in by_arm[a] and i in by_arm[b] for i in ids)
        common = [i for i in ids if i in by_arm[a] and i in by_arm[b]]
        prim = paired(by_arm, a, b, common)
        res["primary"] = dict(prim or {}, complete=complete, arms=[a, b])
        ms, mr = res["arms"][a]["missed_stops"], res["arms"][b]["missed_stops"]
        if prim and complete:
            res["verdict"] = verdict(prim["diff_pp"], prim["lo_pp"], prim["hi_pp"], ms, mr)
        else:
            res["verdict_withheld"] = (f"incomplete: {len(ids) - len(common)} of {len(ids)} lines lack a row for "
                                       f"{a} or {b}; the pre-registered comparison uses every line")
        for s in sorted({c["stratum"] for c in cases}):
            sids = [i for i in common if by_id[i]["stratum"] == s]
            res["per_stratum"][s] = {"n": len(sids), "match_rate_pct": {
                arm: round(100 * sum(by_arm[arm][i]["match"] for i in sids if i in by_arm[arm])
                           / max(1, sum(1 for i in sids if i in by_arm[arm])), 2) for arm in arms},
                f"{a}_minus_{b}": paired(by_arm, a, b, sids)}
        agree = {i: fidelity_agree(by_id[i]) for i in ids}
        known = [i for i in ids if agree[i] is not None]
        if known:
            per_s = {}
            for s in sorted({by_id[i]["stratum"] for i in known}):
                sk = [i for i in known if by_id[i]["stratum"] == s]
                per_s[s] = round(100 * sum(agree[i] for i in sk) / len(sk), 2)
            res["fidelity"] = {"lines_with_judge": len(known),
                               "agreement_pct": round(100 * sum(agree[i] for i in known) / len(known), 2),
                               "per_stratum_pct": per_s}
            agreed = [i for i in common if agree.get(i)]
            res["sensitivity_judge_agreed"] = paired(by_arm, a, b, agreed)
    return res


def _pct(x):
    return "n/a" if x is None else f"{x:.1f} %"


def markdown(res):
    L = [f"# Second Read (v4) results: {res['split']} split", "",
         f"{res['lines']} customer replies. Same decision code in every arm; only the reader differs.", "",
         "## Decision rule (pre-registered, verbatim)", "", "> " + res["decision_rule"], ""]
    p = res["primary"]
    if p:
        L += ["## Primary: Super reader minus hardened rules (paired, every line)", "",
              f"- Difference in match rate: **{p.get('diff_pp', 'n/a')} pp**, 95 % paired bootstrap "
              f"[{p.get('lo_pp', 'n/a')}, {p.get('hi_pp', 'n/a')}] over {p.get('n', 0)} lines",
              f"- Missed stops: super {res['arms']['super']['missed_stops']}, rules {res['arms']['rules']['missed_stops']}",
              f"- **Verdict: {res['verdict'] or 'withheld: ' + res.get('verdict_withheld', '')}**", ""]
    L += ["## Every arm", "", "| arm | n | match | 95 % CI | failures | missed stops | false stops | "
          "margin / 100 | given away / 100 | lost / 100 | $ / 1,000 | p50 s |", "|" + "---|" * 12]
    for a, s in res["arms"].items():
        ci = s["match_ci95_pct"]
        mp = s["money_per_100"]
        cis = "[{}, {}]".format(*ci) if ci else "n/a"
        L.append(f"| {a} | {s['n']} | {_pct(s['match_rate_pct'])} | {cis} | "
                 f"{s['reader_failures']} | {s['missed_stops']} | {s['false_stops']} | {mp['margin_usd']} | "
                 f"{mp['given_away_usd']} | {mp['lost_usd']} | {s['usd_per_1000']} | {s['p50_seconds']} |")
    g = res.get("gold_reference_per_100") or {}
    L += ["", f"Perfect read (gold Facts) for reference: booked {g.get('booked')} and margin ${g.get('margin_usd')} "
              "per 100 replies.", ""]
    if res["per_stratum"]:
        L += ["## Per stratum", "", "| stratum | n | " + " | ".join(res["arms"]) + " | super - rules (pp) |",
              "|" + "---|" * (len(res["arms"]) + 3)]
        for s, v in res["per_stratum"].items():
            d = v.get("super_minus_rules")
            diff = "n/a" if not d else "{} [{}, {}]".format(d["diff_pp"], d["lo_pp"], d["hi_pp"])
            L.append(f"| {s} | {v['n']} | " + " | ".join(_pct(v["match_rate_pct"].get(a)) for a in res["arms"])
                     + f" | {diff} |")
        L.append("")
    if res["fidelity"]:
        f = res["fidelity"]
        L += ["## Fidelity (descriptive, not a filter)", "",
              f"A blind non-NVIDIA judge's read gives the gold offer on {f['agreement_pct']} % of {f['lines_with_judge']} "
              f"lines; per stratum: " + ", ".join(f"{k} {v} %" for k, v in f["per_stratum_pct"].items()) + "."]
        s = res["sensitivity_judge_agreed"]
        if s:
            L.append(f"Sensitivity (judge-agreed lines only, {s['n']}): super - rules {s['diff_pp']} pp "
                     f"[{s['lo_pp']}, {s['hi_pp']}].")
        L.append("")
    return "\n".join(L)
