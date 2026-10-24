"""Pre-registered scoring: dollars both ways, model cost, $ per booking, intervals, and where routing loses.

For each buyer the oracle margin is the best margin any honest policy could earn: over every package (the job minus
any optional items), price = min(list price, walkaway), kept only if it is at or above the owner's floor; for a buyer
who needs an early slot the shop must have one. A buyer with no such package is infeasible.
Per conversation:  margin = price - cost of the booked package (0 if not booked)
                   given_away = oracle - margin            (booked: money left on the table)
                   lost = oracle                            (feasible but not booked)
so oracle - margin = given_away + lost for every buyer. Model $ counts the shop's calls only (not the simulated buyer).
given_away has two named parts: given_to_full_payers (all of it, for buyers whose walkaway is at or above the full list
price: they would have paid it) and menu_gap = oracle - menu_oracle (unreachable with the menu's discrete prices;
menu_oracle uses real menu prices at or below the walkaway). given_away = menu_gap + (menu_oracle - margin).

Totals are per buyer first: each buyer's mean over its repeats, then the sum over buyers (repeats on a subsample must
not shrink a policy's totals). [min, max] bands over repeats use only the buyers every policy ran in every repeat.
"""
import itertools, random, statistics
from collections import defaultdict

from ..menu import Offer, build_menu

METRICS = ("booked", "margin", "given_away", "given_to_full_payers", "menu_gap", "lost", "lost_price", "model_usd",
           "buyer_usd", "net", "violations", "unsure", "off_arm_calls", "label_fallback_calls", "platform_failures",
           "chronology_errors")
INVARIANT_GATES = ("bookings_above_walkaway", "bookings_below_floor")  # hold by construction (env.react, menu.validate)


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


def menu_oracle(card):
    """Best margin reachable at a real menu price (fresh offer on each package) at or above the floor and at or below
    the walkaway; 0 for an infeasible buyer."""
    orc, feasible = oracle(card)
    if not feasible:
        return 0.0
    est, best = card.estimate(), 0.0
    for d, lst, cost, flo in packages(card):
        prices = {m.price for m in build_menu(est, Offer(d, lst, est.default_slot, est.channel), []) if m.dropped == d}
        ok = [p for p in prices if flo - 1e-6 <= p <= card.walkaway + 1e-6]
        if ok:
            best = max(best, max(ok) - cost)
    return min(best, orc)


def score(card, out):
    """Per-conversation dollars, integrity counters, and flags for a booking above the walkaway or below the floor
    (invariants by construction: they can only light on a bug)."""
    orc, feasible = oracle(card)
    est = card.estimate()
    row = {"buyer": out.buyer, "type": out.type, "trade": out.trade, "policy": out.policy, "repeat": out.repeat,
           "booked": out.booked, "price": out.price, "feasible": feasible, "oracle": round(orc, 2),
           "model_usd": out.seller_cost, "buyer_usd": out.buyer_cost, "violations": out.violations,
           "unsure": out.unsure, "end": out.end, "moves": out.moves, "writer_fallbacks": out.writer_fallbacks,
           "off_arm_calls": out.off_arm_calls, "label_fallback_calls": out.label_fallback_calls,
           "platform_failures": out.platform_failures, "chronology_errors": out.chronology_errors,
           "above_walkaway": False, "below_floor": False}
    if out.booked:
        d = frozenset(out.dropped)
        margin = out.price - est.cost(d)
        given = round(orc - margin, 2)
        row.update(margin=round(margin, 2), given_away=given, lost=0.0,
                   given_to_full_payers=given if card.walkaway >= est.list_price() else 0.0,
                   menu_gap=round(orc - menu_oracle(card), 2),
                   above_walkaway=out.price > card.walkaway + 1e-6, below_floor=out.price < est.floor(d) - 1e-6)
    else:
        row.update(margin=0.0, given_away=0.0, given_to_full_payers=0.0, menu_gap=0.0,
                   lost=round(orc, 2) if feasible else 0.0)
    row["net"] = round(row["margin"] - row["model_usd"], 4)
    return row


def _value(r, k):
    if k == "lost_price":
        return r["lost"] if r["type"] == "price" else 0.0
    if k == "booked":
        return float(r["booked"])
    return r.get(k) or 0.0


def per_buyer(rows, policy, metric=None):
    """{buyer: mean over that buyer's repeats} of one metric, or of every metric in METRICS (dict) when metric is None."""
    acc = defaultdict(list)
    for r in rows:
        if r["policy"] == policy:
            acc[r["buyer"]].append(r)
    if metric is not None:
        return {b: statistics.mean(_value(r, metric) for r in rs) for b, rs in acc.items()}
    return {b: {k: statistics.mean(_value(r, k) for r in rs) for k in METRICS} | {"K": len(rs)} for b, rs in acc.items()}


def band_buyers(rows):
    """Buyers present in every (policy, repeat) group: the common subsample the bands are computed on."""
    groups = defaultdict(set)
    for r in rows:
        groups[(r["policy"], r["repeat"])].add(r["buyer"])
    return set.intersection(*groups.values()) if groups else set()


def aggregate(rows, common=None):
    """Per policy: totals over buyers of each buyer's mean over its repeats (the headline numbers), plus `band`:
    mean and [min, max] over repeats of the totals on the common buyers only (rows of every policy, same buyers)."""
    common = band_buyers(rows) if common is None else set(common)
    summary = {}
    for pol in sorted({r["policy"] for r in rows}):
        pb = per_buyer(rows, pol)
        tot = {k: sum(v[k] for v in pb.values()) for k in METRICS}
        n, bookings = len(pb), tot["booked"]
        out = {"n": n, "K": max(v["K"] for v in pb.values()), "bookings": bookings, "booking_rate": bookings / n,
               **{k: tot[k] for k in METRICS if k != "booked"},
               "usd_per_booking": tot["model_usd"] / bookings if bookings else None}
        reps = defaultdict(dict)
        for r in rows:
            if r["policy"] == pol and r["buyer"] in common:
                reps[r["repeat"]][r["buyer"]] = r
        full = [rs for _, rs in sorted(reps.items()) if common and set(rs) == common]
        band = {"n": len(common), "K": len(full)}
        if full:
            for k in ("booked", "margin", "given_away", "lost", "model_usd", "net", "violations", "unsure"):
                band[k] = _band([sum(_value(r, k) for r in rs.values()) for rs in full])
        out["band"] = band
        summary[pol] = out
    return summary


def _band(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"mean": statistics.mean(vals), "min": min(vals), "max": max(vals)}


def paired_bootstrap(rows, policy, baseline="smart_rules", metric="net", draws=2000, seed=7):
    """95% interval of the per-buyer difference policy - baseline (each buyer's mean over repeats), summed over buyers."""
    a, b = per_buyer(rows, policy, metric), per_buyer(rows, baseline, metric)
    common = sorted(set(a) & set(b))
    if not common:
        return None
    diffs = [a[x] - b[x] for x in common]
    rng = random.Random(seed)
    sums = sorted(sum(rng.choice(diffs) for _ in diffs) for _ in range(draws))
    return {"point": sum(diffs), "lo": sums[int(0.025 * draws)], "hi": sums[int(0.975 * draws) - 1], "buyers": len(common)}


MIN_CELL_N = 10


def losing_cells(rows, policy="routed", min_n=MIN_CELL_N, alpha=0.05, draws=4000, seed=7):
    """Cells (objection type x trade) where `policy` earns less net per buyer than the best policy in the cell.

    Per-buyer means first (a buyer counts once whatever its repeats). For each cell: n (buyers both policies ran),
    the mean gap, and a bootstrap interval of the mean per-buyer difference policy - best at level 1 - alpha/m, where
    m is the number of cells with n >= min_n (Bonferroni). The best policy is picked in-sample, which biases the gap
    toward a loss: a cell is a clear loss only when n >= min_n and the whole interval is below 0."""
    pols = sorted({r["policy"] for r in rows})
    pb = {p: per_buyer(rows, p, "net") for p in pols}
    cell_of = {r["buyer"]: (r["type"], r["trade"]) for r in rows}
    if policy not in pb:
        return []
    cand = []
    for cell in sorted(set(cell_of.values())):
        buyers = [b for b, c in cell_of.items() if c == cell]
        means = {p: statistics.mean(pb[p][b] for b in buyers if b in pb[p]) for p in pols if any(b in pb[p] for b in buyers)}
        if policy not in means:
            continue
        best = max(means, key=means.get)
        common = [b for b in buyers if b in pb[policy] and b in pb[best]]
        cand.append((cell, means, best, common))
    m = max(1, sum(len(c[3]) >= min_n for c in cand))
    lo_q, hi_q = alpha / m / 2, 1 - alpha / m / 2
    out = []
    for (t, tr), means, best, common in cand:
        if means[best] <= means[policy] + 1e-9:
            continue
        diffs = [pb[policy][b] - pb[best][b] for b in common]
        rng = random.Random(seed)
        boots = sorted(statistics.mean(rng.choice(diffs) for _ in diffs) for _ in range(draws)) if diffs else []
        lo = boots[int(lo_q * draws)] if boots else None
        hi = boots[min(draws - 1, int(hi_q * draws))] if boots else None
        out.append({"type": t, "trade": tr, "n": len(common), "policy_net": round(means[policy], 2), "best": best,
                    "best_net": round(means[best], 2), "gap": round(means[best] - means[policy], 2),
                    "ci": [round(lo, 2), round(hi, 2)] if boots else None, "level": round(1 - alpha / m, 4),
                    "small": len(common) < min_n, "clear_loss": bool(boots) and len(common) >= min_n and hi < 0})
    return sorted(out, key=lambda c: -c["gap"])


LABEL_RULE_X_USD = 2000.0  # pre-registered non-inferiority margin for Lightning as the default label model (MF8a)


def label_default(rows, x_usd=LABEL_RULE_X_USD):
    """Manifest rule: Lightning labels by default only if the 95% paired-bootstrap lower bound of
    sum(net routed - net super_only) over buyers is >= -x_usd and routed is not worse on violations, unsure and off-arm
    calls (per-buyer totals); otherwise Super (the S2 rule)."""
    ci = paired_bootstrap(rows, "routed", "super_only", "net")
    agg = aggregate(rows)
    checks = {k: agg["routed"][k] <= agg["super_only"][k] + 1e-9 for k in ("violations", "unsure", "off_arm_calls")}
    ok = ci is not None and ci["lo"] >= -x_usd and all(checks.values())
    return {"default_label_model": "Lightning" if ok else "Super", "x_usd": x_usd, "ci_routed_minus_super_only": ci,
            "not_worse": checks}


def sanity_gates(cards, rows, policies=("shop_rule", "concession_heuristic")):
    """The buyer-simulator gates fixed in evidence/spikes/PLAN.md (S8b). The 30-80 % booking-rate gate is computed on
    `policies` only (the text-blind rule policies, as in the manifest); the first two gates are invariants by
    construction and can only light on a bug."""
    above = [r for r in rows if r["above_walkaway"]]
    rates = {p: statistics.mean(r["booked"] for r in rows if r["policy"] == p) for p in policies
             if any(r["policy"] == p for r in rows)}
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
        "invariants": list(INVARIANT_GATES),
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
