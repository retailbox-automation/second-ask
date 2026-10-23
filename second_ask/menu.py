"""Estimates, the owner's floor and the menu of next moves. All of it is code; the model only picks a move id.

A package is the estimate minus a set of dropped optional line items. Each package has a list price and an owner's
floor (list price minus the owner's maximum discount, rounded up to $5, never below cost). No move below the floor of
its package is ever built, and `validate` re-checks every move before anything is sent.
"""
import math, re
from dataclasses import dataclass
from datetime import datetime

CHECKIN_DAYS = (3, 7, 14, 30)


class FloorViolation(ValueError):
    pass


@dataclass(frozen=True)
class LineItem:
    name: str
    price: float
    cost: float
    optional: bool = False


@dataclass(frozen=True)
class Slot:
    id: str
    start: datetime
    hours: float = 2.0


@dataclass(frozen=True)
class Estimate:
    id: str
    shop: str
    trade: str
    title: str
    items: tuple
    max_discount: float        # owner's largest allowed discount on any package, e.g. 0.07
    sent_at: datetime
    channel: str               # "sms" or "email": where the estimate went
    default_slot: Slot         # the slot quoted with the estimate

    def list_price(self, dropped=frozenset()):
        return sum(i.price for i in self.items if i.name not in dropped)

    def cost(self, dropped=frozenset()):
        return sum(i.cost for i in self.items if i.name not in dropped)

    def floor(self, dropped=frozenset()):
        lst = self.list_price(dropped)
        raw = max(lst * (1 - self.max_discount), self.cost(dropped))
        return min(lst, math.ceil(raw / 5) * 5)  # rounding up to $5 must not lift the floor above the list price

    def optional_items(self, dropped=frozenset()):
        return [i for i in self.items if i.optional and i.name not in dropped]


@dataclass(frozen=True)
class Offer:
    dropped: frozenset
    price: float
    slot: Slot
    channel: str


@dataclass(frozen=True)
class Move:
    id: str
    kind: str          # answer ask_why reminder checkin offer_slot earlier_slot resend drop concede close_out
    description: str
    dropped: frozenset
    price: float
    slot: Slot | None
    channel: str
    checkin_days: int | None = None
    needs_owner: bool = False
    proposes_booking: bool = False

    def offer(self, prev):
        return Offer(self.dropped, self.price, self.slot or prev.slot, self.channel)


def initial_offer(est):
    return Offer(frozenset(), est.list_price(), est.default_slot, est.channel)


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _round5(x):
    return round(x / 5) * 5


def _money(x):
    return f"${x:,.0f}"


def build_menu(est, offer, open_slots, channels=("sms", "email"), max_checkin_days=None):
    """Every move the shop may make next, given the standing offer. Prices never go below the package floor."""
    d, p, s, ch = offer.dropped, offer.price, offer.slot, offer.channel
    pkg = lambda dropped: ", ".join(i.name for i in est.items if i.name not in dropped)  # noqa: E731
    moves = [
        Move("answer", "answer", f"Written answer: explain what the {_money(p)} covers and why; no price change.", d, p, None, ch),
        Move("ask_why", "ask_why", "Ask one short, friendly question about what is holding them back; no offer.", d, p, None, ch),
        Move("reminder", "reminder", "Generic reminder that the estimate is still open; nothing new.", d, p, None, ch),
    ]
    for n in CHECKIN_DAYS:
        if max_checkin_days is None or n <= max_checkin_days:
            moves.append(Move(f"checkin_{n}", "checkin", f"No pressure, no price change; say you will check back in {n} days.",
                              d, p, None, ch, checkin_days=n))
    moves.append(Move("offer_slot", "offer_slot", f"Offer to book {s.start:%a %b %d %I:%M %p} at {_money(p)}.",
                      d, p, s, ch, proposes_booking=True))
    for k, slot in enumerate(sorted((x for x in open_slots if x.start < s.start), key=lambda x: x.start)[:2], 1):
        moves.append(Move(f"earlier_slot_{k}", "earlier_slot",
                          f"Offer an earlier slot {slot.start:%a %b %d %I:%M %p} (quoted slot is {s.start:%a %b %d}) at {_money(p)}.",
                          d, p, slot, ch, proposes_booking=True))
    for other in channels:
        if other != ch:
            moves.append(Move(f"resend_{other}", "resend", f"Resend the estimate summary by {other} instead of {ch}, "
                                                            f"with a link to book; no price change.", d, p, None, other,
                              proposes_booking=True))
    for item in est.optional_items(d):
        nd = d | {item.name}
        price = min(est.list_price(nd), max(est.floor(nd), _round5(p - item.price)))
        moves.append(Move(f"drop_{_slug(item.name)}", "drop",
                          f"Same job without the optional {item.name} ({_money(item.price)}): {pkg(nd)} for {_money(price)}.",
                          nd, price, None, ch, needs_owner=price < est.list_price(nd), proposes_booking=True))
    if len(est.optional_items(d)) >= 2:
        nd = d | {i.name for i in est.optional_items(d)}
        price = min(est.list_price(nd), max(est.floor(nd), _round5(p - sum(i.price for i in est.optional_items(d)))))
        moves.append(Move("drop_all_optional", "drop", f"Same job without any optional items: {pkg(nd)} for {_money(price)}.",
                          nd, price, None, ch, needs_owner=price < est.list_price(nd), proposes_booking=True))
    targets = [(d, "pkg", "this package", p)]  # concessions on the standing package must beat the standing price
    core = frozenset(i.name for i in est.items if i.optional)
    if core != d:
        targets.append((core, "core", "the job without optional items", est.list_price(core)))
    for dropped, key, name, beat in targets:
        lst, flo, seen = est.list_price(dropped), est.floor(dropped), set()
        for pct in (3, 5, None):
            price = flo if pct is None else max(flo, _round5(lst * (1 - pct / 100)))
            if price >= beat or price in seen:
                continue
            seen.add(price)
            moves.append(Move(f"concede_{key}_{'floor' if price == flo else pct}", "concede",
                              f"Lower {name} ({pkg(dropped)}) to {_money(price)} ({(1 - price / lst) * 100:.0f}% below "
                              f"list); owner approves with one tap.", dropped, price, None, ch, needs_owner=True,
                              proposes_booking=True))
    moves.append(Move("close_out", "close_out", "Thank them, leave the door open and stop following up.", d, p, None, ch))
    for m in moves:
        validate(m, est)
    return moves


def concession(est, offer, price):
    """A concession on the standing package at a price chosen by code (the rule baselines); floor-checked."""
    price = float(price)
    lst = est.list_price(offer.dropped)
    move = Move(f"concede_to_{price:.0f}", "concede", f"Lower this package to {_money(price)} "
                f"({(1 - price / lst) * 100:.0f}% below list); owner approves with one tap.", offer.dropped, price, None,
                offer.channel, needs_owner=price < lst, proposes_booking=True)
    return validate(move, est)


def validate(move, est):
    """Raise FloorViolation for any move priced outside [floor, list] of its package."""
    flo, lst = est.floor(move.dropped), est.list_price(move.dropped)
    if not (flo - 0.005 <= move.price <= lst + 0.005):
        raise FloorViolation(f"{move.id}: {move.price} outside [{flo}, {lst}] for {sorted(move.dropped)}")
    return move


_AMOUNT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)|\b(\d[\d,]*(?:\.\d{1,2})?)\s?(?:dollars|bucks|usd)\b", re.I)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent)", re.I)


def amounts_in(text):
    return [float((m.group(1) or m.group(2)).replace(",", "")) for m in _AMOUNT.finditer(text or "")]


def check_message(text, move, est, offer, customer_amounts=()):
    """Problems with a model-written message: any $ amount or % that is not part of this move, or excess length.

    Amounts the customer wrote may be quoted back only when they are at or above the move's price ("under your $3,600
    budget"), so a message can never seem to offer less than the move does.
    """
    allowed = {round(x, 2) for x in (move.price, est.list_price(move.dropped), est.list_price(), offer.price,
                                     *(i.price for i in est.items), *(a for a in customer_amounts if a >= move.price))}
    lst = est.list_price(move.dropped)
    pct_ok = {round((1 - move.price / lst) * 100)} if move.price < lst else set()
    problems = []
    for val in amounts_in(text):
        if round(val, 2) not in allowed:
            problems.append(f"amount ${val:,.2f} is not part of move {move.id}")
    for m in _PERCENT.finditer(text or ""):
        if round(float(m.group(1))) not in pct_ok:
            problems.append(f"percent {m.group(1)}% is not part of move {move.id}")
    if not (text or "").strip():
        problems.append("empty message")
    if len(text or "") > 480:
        problems.append("message longer than 480 characters")
    return problems


TEMPLATES = {
    "answer": "Here is what the {price} covers: {items}. Happy to walk through any line.",
    "ask_why": "Quick question so we get this right: is it the price, the timing, or something else?",
    "reminder": "Just checking in on your {trade} estimate. It is still open whenever you are ready.",
    "checkin": "No rush at all. I will check back in {days} days; the estimate stays at {price}.",
    "offer_slot": "We can book you for {slot} at {price}. Reply YES to confirm.",
    "earlier_slot": "Good news: we can come sooner, {slot}, at {price}. Reply YES to confirm.",
    "resend": "Resending your {trade} estimate here in case the first one was missed: {items}, {price}. Reply YES to book.",
    "drop": "We can do it without the optional item: {items} for {price}. Reply YES to book.",
    "concede": "We can do {items} for {price}. Reply YES to book.",
    "close_out": "Thanks for considering us. If anything changes, just reply here.",
}


def template_message(move, est, offer):
    items = ", ".join(i.name for i in est.items if i.name not in move.dropped)
    slot = move.slot or offer.slot
    return TEMPLATES[move.kind].format(price=_money(move.price), items=items, trade=est.trade,
                                       slot=f"{slot.start:%a %b %d %I:%M %p}", days=move.checkin_days)
