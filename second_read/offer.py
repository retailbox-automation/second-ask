"""Second Read (v4): the shop's code turns Facts into the offer it sends. Deterministic, no model.

Both arms run exactly this code on the Facts their reader returned, so a difference in the offer comes from reading
alone. Prices come only from `second_ask.menu` moves built from the card's estimate with the initial offer (the owner's
floor is enforced there); slots are the card's open slots after today. Rules (V4-DESIGN §3):

1. stop -> {stop: true} and nothing else.
2. package = the estimate minus the drop items that are optional items of this estimate (others ignored).
3. price: with a ceiling, the highest menu price for that package that is <= the ceiling and >= the package floor;
   if none fits, the package floor. Without a ceiling, the package list price (no unrequested discount).
4. timing, first rule that applies:
   wait_weeks -> check-in, the menu check-in option nearest to 7 x N days (ties: the shorter);
   need_by -> the earliest open slot on or before need_by, else the earliest open slot;
   ready, or a price/drop change -> the earliest open slot on or after the Monday of the quoted slot's week,
       else the earliest open slot;
   decider -> answer and wait (no booking push);
   otherwise -> answer.
   With no open slot after today a slot rule falls through to "answer".
"""
from datetime import date, timedelta

from second_ask.menu import build_menu, initial_offer

from .contract import Facts, parse

SLOT, CHECKIN, ANSWER = "slot", "checkin", "answer"


def _day(x):
    return x if isinstance(x, date) else date.fromisoformat(x)


def as_facts(facts, est):
    """Facts from a Facts object or a raw dict (validated by the contract against this estimate's optional items)."""
    if isinstance(facts, Facts):
        return facts
    return parse(facts, [i.name for i in est.items if i.optional])


def package(facts, est):
    """Sorted names of the optional items of this estimate that the facts drop."""
    optional = {i.name for i in est.items if i.optional}
    return sorted(set(facts.drop_items) & optional)


def open_slots(card, today):
    """Open slots strictly after today, earliest first."""
    t = _day(today)
    return sorted((s for s in card.open_slots() if s.start.date() > t), key=lambda s: s.start)


def price_for(facts, card, moves=None):
    """(dropped, price): the package and its price per rule 3, prices taken only from the menu."""
    est = card.estimate()
    dropped = package(facts, est)
    pkg = frozenset(dropped)
    if facts.ceiling_usd is None:
        return dropped, round(float(est.list_price(pkg)), 2)
    moves = moves if moves is not None else build_menu(est, initial_offer(est), card.open_slots())
    floor = est.floor(pkg)
    fits = [m.price for m in moves if m.dropped == pkg and floor - 0.005 <= m.price <= facts.ceiling_usd + 0.005]
    return dropped, round(float(max(fits) if fits else floor), 2)


def _checkin(weeks, moves):
    options = sorted({m.checkin_days for m in moves if m.kind == "checkin"})
    target = 7 * weeks
    return min(options, key=lambda d: (abs(d - target), d))


def _slot(day):
    return {"kind": SLOT, "day": day.isoformat()}


def offer(facts, card, today):
    """The Offer dict {stop, dropped, price, timing} the shop sends for these facts (JSON-serialisable)."""
    est = card.estimate()
    facts = as_facts(facts, est)
    if facts.stop:
        return {"stop": True, "dropped": [], "price": None, "timing": None}
    slots = open_slots(card, today)
    moves = build_menu(est, initial_offer(est), slots)
    dropped, price = price_for(facts, card, moves)
    days = [s.start.date() for s in slots]
    changed = bool(dropped) or abs(price - est.list_price()) > 0.005
    if facts.wait_weeks is not None:
        timing = {"kind": CHECKIN, "days": _checkin(facts.wait_weeks, moves)}
    elif facts.need_by is not None and days:
        by = _day(facts.need_by)
        timing = _slot(next((d for d in days if d <= by), days[0]))
    elif (facts.ready or changed) and days:
        quoted = est.default_slot.start.date()
        monday = quoted - timedelta(days=quoted.weekday())
        timing = _slot(next((d for d in days if d >= monday), days[0]))
    else:  # a decider (answer and wait), nothing actionable, or no open slot left
        timing = {"kind": ANSWER}
    return {"stop": False, "dropped": dropped, "price": price, "timing": timing}


def match(a, b):
    """Same offer: equal stop, dropped items, price to the cent and timing. A missing offer (reader failure) never
    matches."""
    if a is None or b is None:
        return False
    pa, pb = a.get("price"), b.get("price")
    same_price = (pa is None and pb is None) or (pa is not None and pb is not None and round(pa, 2) == round(pb, 2))
    return (bool(a["stop"]) == bool(b["stop"]) and sorted(a["dropped"]) == sorted(b["dropped"]) and same_price
            and a["timing"] == b["timing"])


def books(card, off, gold):
    """Does a buyer whose true facts are `gold` book on offer `off`? Every stated gold condition must be met: price
    <= gold ceiling, gold drop items dropped, a slot on or before gold need_by, a check-in within +-7 days of the gold
    wait; nobody books after asking to stop, and nobody books on a stop."""
    if off is None or gold.stop or off["stop"]:
        return False
    est = card.estimate()
    if gold.ceiling_usd is not None and off["price"] > gold.ceiling_usd + 0.005:
        return False
    if not set(package(gold, est)) <= set(off["dropped"]):
        return False
    t = off["timing"]
    if gold.need_by is not None and not (t["kind"] == SLOT and t["day"] <= gold.need_by):
        return False
    if gold.wait_weeks is not None and not (t["kind"] == CHECKIN and abs(t["days"] - 7 * gold.wait_weeks) <= 7):
        return False
    return True


def money(card, off, gold_facts, today):
    """Secondary (pre-registered): whether the buyer books, the shop's margin, and money given away or lost against
    the offer a perfect read would have produced. `off` None means the reader failed: nothing is sent, nothing books.

    margin = price - cost of the offered package when booked, else 0
    given_away = gold margin - margin when both book and the read offer earns less (gained: when it earns more)
    lost = gold margin when the perfect-read offer books and this one does not
    message_after_stop = the buyer asked to stop and the shop still sent an offer (a compliance failure)
    """
    est = card.estimate()
    gold = as_facts(gold_facts, est)
    gold_offer = offer(gold, card, today)
    gold_books = books(card, gold_offer, gold)
    gold_margin = round(gold_offer["price"] - est.cost(frozenset(gold_offer["dropped"])), 2) if gold_books else 0.0
    booked = books(card, off, gold)
    margin = round(off["price"] - est.cost(frozenset(off["dropped"])), 2) if booked else 0.0
    both = booked and gold_books
    return {"booked": booked, "margin": margin, "gold_books": gold_books, "gold_margin": gold_margin,
            "given_away": round(max(0.0, gold_margin - margin), 2) if both else 0.0,
            "gained": round(max(0.0, margin - gold_margin), 2) if both else 0.0,
            "lost": gold_margin if gold_books and not booked else 0.0,
            "message_after_stop": bool(gold.stop and off is not None and not off["stop"])}
