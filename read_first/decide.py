"""Code decides: Facts -> offer() -> one move from the shop's menu, and the checks on the text written around it.

`second_read.offer.offer()` is the only decider of stop, dropped items, price and timing. This module only finds the
move on the menu `second_ask.menu.build_menu` builds for the standing offer (the same menu, with the same 90-day SMS
limit on check-ins, as `second_ask.agent.Agent` and the rule policies), so the floor check, the compliance gate, the
templates and the owner tap downstream are the existing code.

Mapping (offer -> move):
- package and price equal the standing offer: the timing picks the move. A slot -> the offer_slot / earlier_slot move
  for that day, else offer_slot carrying the open slot of that day; a check-in of N days -> checkin_N; answer -> answer.
- package or price differ: the drop or concede move with exactly that package and that price (the menu's price, never
  a new one). A slot or check-in from offer() is carried on that move.
- no such move (for example a partial package whose floor is not on the menu, a check-in the 90-day SMS rule removed,
  or a list price above a concession the shop already sent): the code default for what was read
  (`second_ask.chooser.default_move`), flagged.
"""
import re
from dataclasses import dataclass, replace

from second_ask.chooser import default_move
from second_ask.tournament.policies import SmartRules, _menu
from second_read.offer import ANSWER, CHECKIN, SLOT

SILENCE_BRANCHES = ("silence_resend", "silence_checkin", "silence")


def menu_for(conv, now):
    """The agent's menu for the standing offer (Agent.decide and SmartRules build exactly this one)."""
    return _menu(conv, now)


def silence_move(conv, menu):
    """(move, branch) for a silence, from SmartRules' silence branches. Code only: no model is called."""
    move, branch = SmartRules.choose_with_branch(conv, menu)
    if branch not in SILENCE_BRANCHES:
        raise ValueError(f"silence_move called with a customer reply pending (branch {branch!r})")
    return move, branch


def label_for(facts):
    """The chooser label that fits what was read, for the code default when no menu move matches the offer."""
    if facts is None:
        return None
    if facts.ceiling_usd is not None or facts.drop_items:
        return "price"
    if facts.need_by is not None or facts.wait_weeks is not None:
        return "timing"
    if facts.decider is not None:
        return "family"
    if facts.ready:
        return "ready_to_book"
    return None


@dataclass(frozen=True)
class Mapping:
    move: object
    exact: bool          # False: no menu move carries this offer; the code default was used
    note: str


def _money(x):
    return f"${x:,.0f}"


def slot_on(conv, day, now):
    """The earliest open slot of this conversation on `day` (YYYY-MM-DD, UTC date) after now."""
    return min((s for s in conv.open_slots if s.start > now and s.start.date().isoformat() == day),
               key=lambda s: s.start, default=None)


def map_offer(off, conv, menu, now, facts=None):
    """The menu move that carries `off` (an offer() dict that is not a stop)."""
    if off["stop"]:
        raise ValueError("a stop has no move")
    by = {m.id: m for m in menu}
    std = conv.offer
    dropped, price, timing = frozenset(off["dropped"]), off["price"], off["timing"]
    slot = slot_on(conv, timing["day"], now) if timing["kind"] == SLOT else None
    if dropped == std.dropped and abs(price - std.price) < 0.005:
        if timing["kind"] == SLOT and slot is not None:
            m = next((m for m in menu if m.kind in ("offer_slot", "earlier_slot") and m.slot is not None
                      and m.slot.start.date().isoformat() == timing["day"]), None)
            if m is not None:
                return Mapping(m, True, f"{m.id}: the slot offer() chose")
            base = by["offer_slot"]
            return Mapping(replace(base, slot=slot, description=f"Offer to book {slot.start:%a %b %d %I:%M %p} at "
                                                                f"{_money(base.price)}."),
                           True, "offer_slot carrying the slot offer() chose")
        if timing["kind"] == CHECKIN and f"checkin_{timing['days']}" in by:
            return Mapping(by[f"checkin_{timing['days']}"], True, f"check back in {timing['days']} days")
        if timing["kind"] == ANSWER:
            return Mapping(by["answer"], True, "answer; nothing to change")
    else:
        m = next((m for m in menu if m.kind in ("drop", "concede") and m.dropped == dropped
                  and abs(m.price - price) < 0.005), None)
        if m is not None:
            if slot is not None:
                m = replace(m, slot=slot)
            elif timing["kind"] == CHECKIN:
                m = replace(m, checkin_days=timing["days"])
            return Mapping(m, True, f"{m.id}: {sorted(dropped) or 'full package'} at {_money(price)}")
    m = default_move(label_for(facts), menu)
    return Mapping(m, False, f"no menu move carries {sorted(dropped) or 'the full package'} at {_money(price)} with "
                             f"timing {timing}; code default {m.id} used")


# --- dates in a written message --------------------------------------------------------------------------------------
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|" \
         r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_MONTH_DAY = re.compile(_MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH + r"(?![a-z])", re.I)
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_NUMERIC = re.compile(r"(?<![\d$.,])(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b")
_ORDINAL = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b", re.I)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY = re.compile(r"\b(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|"
                      r"sat(?:urday)?|sun(?:day)?)\b", re.I)
_RELATIVE = re.compile(r"\b(today|tonight|tomorrow|yesterday|this weekend|next week|next month)\b", re.I)
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10, "a couple of": 2, "a couple": 2, "a few": 3, "few": 3}
_SPAN = re.compile(r"\b(\d+|a couple of|a couple|a few|few|an|a|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
                   r"(days?|weeks?|months?)\b", re.I)
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}


def _month(s):
    return _MONTHS.index(s[:3].lower()) + 1


def date_problems(text, move):
    """Every date, weekday, relative day or span of days in `text` that is not the move's own: the slot's day (when the
    move has a slot) and the check-in span (when it has one). Conservative on purpose: a mention the code cannot match
    to the move is a problem, and the message falls back to the template."""
    text = text or ""
    slot = move.slot.start.date() if move.slot is not None else None
    days = move.checkin_days
    problems = []

    def bad(what):
        problems.append(f"{what} is not part of move {move.id}")

    spans = []
    for m in _MONTH_DAY.finditer(text):
        spans.append(m.span())
        if slot is None or (_month(m.group(1)), int(m.group(2))) != (slot.month, slot.day):
            bad(f"date {m.group(0)!r}")
    for m in _DAY_MONTH.finditer(text):
        if any(a <= m.start() < b or m.start() <= a < m.end() for a, b in spans):
            continue
        spans.append(m.span())
        if slot is None or (_month(m.group(2)), int(m.group(1))) != (slot.month, slot.day):
            bad(f"date {m.group(0)!r}")
    for m in _ISO.finditer(text):
        spans.append(m.span())
        if slot is None or (int(m.group(1)), int(m.group(2)), int(m.group(3))) != (slot.year, slot.month, slot.day):
            bad(f"date {m.group(0)!r}")
    for m in _NUMERIC.finditer(text):
        spans.append(m.span())
        if slot is None or (int(m.group(1)), int(m.group(2))) != (slot.month, slot.day):
            bad(f"date {m.group(0)!r}")
    for m in _ORDINAL.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        if slot is None or int(m.group(1)) != slot.day:
            bad(f"day {m.group(0)!r}")
    for m in _WEEKDAY.finditer(text):
        w = next(i for i, d in enumerate(_WEEKDAYS) if d.startswith(m.group(1).lower()[:3]))
        if slot is None or w != slot.weekday():
            bad(f"weekday {m.group(0)!r}")
    for m in _RELATIVE.finditer(text):
        bad(f"relative day {m.group(0)!r}")
    for m in _SPAN.finditer(text):
        n = m.group(1).lower()
        count = int(n) if n.isdigit() else _NUMBER_WORDS[n]
        unit = m.group(2).lower().rstrip("s")
        if days is None or count * _UNIT_DAYS[unit] != days:
            bad(f"span {m.group(0)!r}")
    return problems
