"""Pre-registered scoring: dollars both ways, model cost, $ per booking, intervals, and where routing loses.

For each buyer the oracle margin is the best margin any honest policy could earn: over every package (the job minus
any optional items), price = min(list price, walkaway), kept only if it is at or above the owner's floor; for a buyer
who needs an early slot the shop must have one. A buyer with no such package is infeasible.
Per conversation:  margin = price - cost of the booked package (0 if not booked)
                   given_away = oracle - margin            (booked: money left on the table)
                   lost = oracle                            (feasible but not booked)
so oracle - margin = given_away + lost for every buyer. Model $ counts the shop's calls only (not the simulated buyer).
"""
import itertools, random, statistics
from collections import defaultdict



def packages(card):
    """(dropped items, list price, cost, floor) for every package: the job minus any set of optional items."""
    est = card.estimate()
    opt = [i.name for i in est.items if i.optional]
    for k in range(len(opt) + 1):
        for drop in itertools.combinations(opt, k):
            d = frozenset(drop)
            yield d, est.list_price(d), est.cost(d), est.floor(d)


def oracle(card):
    """(oracle margin, feasible)."""
    if card.type == "opt_out":
        return 0.0, False
    if card.type == "timing_slot" and not any(card.first_reply_hours / 24 < d <= card.need_by_days for d in card.open_slot_days):
        return 0.0, False
    best = None
    for _, lst, cost, flo in packages(card):
        price = min(lst, card.walkaway)
        if price >= flo:
            best = max(best or 0.0, price - cost)
    return (best, True) if best is not None else (0.0, False)


def score(card, out):
    """Per-conversation dollars, plus flags for a booking above the walkaway or below the floor (gates: must be 0)."""
    orc, feasible = oracle(card)
    row = {"buyer": out.buyer, "type": out.type, "trade": out.trade, "policy": out.policy, "repeat": out.repeat,
           "booked": out.booked, "price": out.price, "feasible": feasible, "oracle": round(orc, 2),
           "model_usd": out.seller_cost, "violations": out.violations, "unsure": out.unsure, "end": out.end,
           "above_walkaway": False, "below_floor": False}
    if out.booked:
        est, d = card.estimate(), frozenset(out.dropped)
        margin = out.price - est.cost(d)
        row.update(margin=round(margin, 2), given_away=round(orc - margin, 2), lost=0.0,
                   above_walkaway=out.price > card.walkaway + 1e-6, below_floor=out.price < est.floor(d) - 1e-6)
    else:
        row.update(margin=0.0, given_away=0.0, lost=round(orc, 2) if feasible else 0.0)
    row["net"] = round(row["margin"] - row["model_usd"], 4)
    return row


def aggregate(rows):
    """Per policy and repeat: totals; then per policy: mean and [min, max] over repeats."""
    by = defaultdict(list)
    for r in rows:
        by[(r["policy"], r["repeat"])].append(r)
    per_rep = {}
    for (pol, rep), rs in by.items():
        bookings = sum(r["booked"] for r in rs)
        usd = sum(r["model_usd"] for r in rs)
        per_rep[(pol, rep)] = {
            "n": len(rs), "bookings": bookings, "booking_rate": bookings / len(rs),
            "margin": sum(r["margin"] for r in rs), "given_away": sum(r["given_away"] for r in rs),
            "lost": sum(r["lost"] for r in rs), "lost_price": sum(r["lost"] for r in rs if r["type"] == "price"),
            "model_usd": usd, "usd_per_booking": usd / bookings if bookings else None,
            "net": sum(r["net"] for r in rs), "violations": sum(r["violations"] for r in rs),
            "unsure": sum(r["unsure"] for r in rs)}
    summary = {}
    for pol in sorted({p for p, _ in per_rep}):
        reps = [v for (p, _), v in sorted(per_rep.items()) if p == pol]
        summary[pol] = {k: _band([r[k] for r in reps]) for k in reps[0] if k != "n"} | {"n": reps[0]["n"], "K": len(reps)}
    return summary


def _band(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"mean": statistics.mean(vals), "min": min(vals), "max": max(vals)}


def paired_bootstrap(rows, policy, baseline="shop_rule", metric="net", draws=2000, seed=7):
    """95% interval of the per-buyer difference policy - baseline (each buyer's mean over repeats), summed over buyers."""
    def per_buyer(pol):
        acc = defaultdict(list)
        for r in rows:
            if r["policy"] == pol:
                acc[r["buyer"]].append(r[metric])
        return {b: statistics.mean(v) for b, v in acc.items()}
    a, b = per_buyer(policy), per_buyer(baseline)
    common = sorted(set(a) & set(b))
    if not common:
        return None
    diffs = [a[x] - b[x] for x in common]
    rng = random.Random(seed)
    sums = sorted(sum(rng.choice(diffs) for _ in diffs) for _ in range(draws))
    return {"point": sum(diffs), "lo": sums[int(0.025 * draws)], "hi": sums[int(0.975 * draws) - 1], "buyers": len(common)}


def losing_cells(rows, policy="routed"):
    """Cells (objection type x trade) where `policy` earns less net per buyer than some other policy."""
    acc = defaultdict(list)
    for r in rows:
        acc[(r["type"], r["trade"], r["policy"])].append(r["net"])
    cells = sorted({(t, tr) for t, tr, _ in acc})
    out = []
    for t, tr in cells:
        means = {p: statistics.mean(v) for (tt, ttr, p), v in acc.items() if (tt, ttr) == (t, tr)}
        if policy not in means:
            continue
        best = max(means, key=means.get)
        if means[best] > means[policy] + 1e-9:
            out.append({"type": t, "trade": tr, "policy_net": round(means[policy], 2), "best": best,
                        "best_net": round(means[best], 2), "gap": round(means[best] - means[policy], 2)})
    return sorted(out, key=lambda c: -c["gap"])


def sanity_gates(cards, rows, policies=("shop_rule", "concession_heuristic")):
    """The buyer-simulator gates fixed in evidence/spikes/PLAN.md (S8b)."""
    above = [r for r in rows if r["above_walkaway"]]
    rates = {p: statistics.mean(r["booked"] for r in rows if r["policy"] == p) for p in {r["policy"] for r in rows}}
    price = [r for r in rows if r["type"] == "price" and r["policy"] in policies]
    ratio = {c.id: c.walkaway / sum(p for _, p, _ in c.items) for c in cards if c.type == "price"}
    cuts = sorted(ratio.values())
    t1, t2 = cuts[len(cuts) // 3], cuts[2 * len(cuts) // 3]
    terc = [statistics.mean([r["booked"] for r in price if (ratio[r["buyer"]] < t1, t1 <= ratio[r["buyer"]] < t2,
                                                            ratio[r["buyer"]] >= t2)[i]] or [0]) for i in range(3)]
    by_type = {}
    for p in policies:
        by_type[p] = {t: statistics.mean([r["booked"] for r in rows if r["policy"] == p and r["type"] == t] or [0])
                      for t in sorted({r["type"] for r in rows})}
    spread = {p: max(v.values()) - min(v.values()) for p, v in by_type.items()}
    return {
        "bookings_above_walkaway": len(above),
        "bookings_below_floor": sum(r["below_floor"] for r in rows),
        "booking_rate_by_policy": rates,
        "booking_rate_in_30_80": {p: 0.30 <= v <= 0.80 for p, v in rates.items()},
        "price_buyers_booking_rate_by_walkaway_tercile": terc,
        "rises_with_walkaway": terc[0] <= terc[1] <= terc[2] and terc[0] < terc[2],
        "booking_rate_by_type": by_type,
        "type_spread": spread,
        "differs_by_type": all(v >= 0.2 for v in spread.values()),
    }
