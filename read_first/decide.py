"""Code decides: Facts -> offer() -> one move from the shop's menu, and the checks on the text written around it.

`second_read.offer.offer()` is the only decider of stop, dropped items, price and timing. This module only finds the
move on the menu `second_ask.menu.build_menu` builds for the standing offer (the same menu, with the same 90-day SMS
limit on check-ins, as `second_ask.agent.Agent` and the rule policies), so the floor check, the compliance gate, the
templates and the owner tap downstream are the existing code.

Mapping (offer -> move):
- package and price equal the standing offer: the timing picks the move. A slot -> the offer_slot / earlier_slot move
  for that day, else offer_slot carrying the open slot of that day; a check-in of N days -> checkin_N; answer -> answer.
- the same package at a price above the standing one (offer() starts from the list price, and the shop already sent
  a concession): the price already sent is kept, never raised, and the owner sees a flag saying so; then as above.
- package or price differ: the drop or concede move with exactly that package and that price (the menu's price, never
  a new one). A slot or check-in from offer() is carried on that move.
- no such move (for example a partial package whose floor is not on the menu, a check-in the 90-day SMS rule removed,
  or a list price above a concession the shop already sent): the code default for what was read
  (`second_ask.chooser.default_move`), flagged.
"""
import re
from dataclasses import dataclass, replace
from datetime import timedelta

from second_ask.chooser import default_move
from second_ask.tournament.policies import SmartRules, _menu
from second_read.offer import ANSWER, CHECKIN, SLOT

from .template import local, slot_day, slot_text

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
    owner_flag: str | None = None  # shown to the owner: the price already sent was kept instead of offer()'s


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
    kept = None
    if dropped == std.dropped and price > std.price + 0.005:  # never raise a price after a concession went out
        kept = f"offer() list price {_money(price)}; kept the {_money(std.price)} already sent"
        price = std.price
    slot = slot_on(conv, timing["day"], now) if timing["kind"] == SLOT else None
    if dropped == std.dropped and abs(price - std.price) < 0.005:
        if timing["kind"] == SLOT and slot is not None:
            m = next((m for m in menu if m.kind in ("offer_slot", "earlier_slot") and m.slot is not None
                      and m.slot.start.date().isoformat() == timing["day"]), None)
            if m is not None:
                return Mapping(m, True, f"{m.id}: the slot offer() chose", kept)
            base = by["offer_slot"]
            return Mapping(replace(base, slot=slot, description=f"Offer to book {slot_text(slot, conv.recipient.tz)} at "
                                                                f"{_money(base.price)}."),
                           True, "offer_slot carrying the slot offer() chose", kept)
        if timing["kind"] == CHECKIN and f"checkin_{timing['days']}" in by:
            return Mapping(by[f"checkin_{timing['days']}"], True, f"check back in {timing['days']} days", kept)
        if timing["kind"] == ANSWER:
            return Mapping(by["answer"], True, "answer; nothing to change", kept)
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
                             f"timing {timing}; code default {m.id} used", kept)


# --- claims of a done booking in a written message -------------------------------------------------------------------
# No move on the menu confirms a booking: every move offers, and the job is booked only by the booking tool loop after
# the customer says yes (second_ask.agent.Agent.book). So a message never says it is booked, scheduled or confirmed.
CONFIRMING_KINDS = ()
_CLAIMS = re.compile(
    r"\bbooked\b|\b(?:is|are|been|it's|you're|you are|now|all)\s+scheduled\b|\b(?:we|i)(?:'ve| have)\s+(?:\w+\s+)?scheduled\b|"
    r"\bconfirmed\b|\breserved\b|\blocked in\b|\breservad[oa]s?\b|\bagendad[oa]s?\b|\bconfirmad[oa]s?\b", re.I)


def claim_problems(text, move):
    """Every phrase in `text` that says the job is already booked, scheduled, confirmed or reserved, unless the move
    confirms a booking (no move kind does). "Shall I book it?" or "Reply YES to confirm" pass: only the done-deal
    forms are matched."""
    if move.kind in CONFIRMING_KINDS:
        return []
    norm = (text or "").translate(str.maketrans({"’": "'", "‘": "'", "ʼ": "'"}))
    return [f'claims a done booking ("{(text or "")[m.start():m.end()]}") but move {move.id} only offers'
            for m in _CLAIMS.finditer(norm)]


# --- wording the facts do not support --------------------------------------------------------------------------------
# Three narrow checks (each found in a recorded draft):
# 1. "as discussed / as agreed / as promised": the shop's code knows only the conversation shown, so it is never
#    supported (always flagged; no exception for the customer's own words, to keep the check simple and strict).
# 2. a slot move whose day is not the quoted slot's day, described as the quoted slot or as unchanged ("the quoted slot
#    remains Mon Nov 09", "same slot", "keep the original slot"); "earlier than your quoted slot" passes.
# 3. a check-in move whose text asks the customer to do the checking back ("Check back in 30 days", "get back to us
#    in a month", "feel free to reach out in 30 days"); "we'll check back in 30 days" / "I will reach out in 30 days"
#    pass (we or I as the subject just before the verb, up to four words between, none of them "you").
_AS_SAID = re.compile(r"\bas (?:we )?(?:discussed|agreed|promised)\b", re.I)
_DAY_WORD = r"(?:mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
_SAME_SLOT = re.compile(
    r"\bquoted (?:slot|date|day|time)\s+(?:still\s+)?(?:is|remains|stays)\b|"
    r"\bquoted slot[,:]?\s+(?:on\s+)?" + _DAY_WORD + r"|"
    r"\b(?:slot|appointment|date|day|time|visit)(?:\s+still)?\s+(?:remains|stays)\b|"
    r"\bsame (?:slot|day|date|time)\b|"
    r"\b(?:keep|keeping|kept)\s+(?:the\s+|your\s+)?(?:quoted|original) (?:slot|date|day|time)\b", re.I)
_CHECK_BACK = re.compile(r"\b(?:check back|get back to (?:us|me)|reach out(?: to us)?|follow up(?: with us)?)\s+in\b", re.I)
_SHOP_SUBJECT = re.compile(r"\b(?:we|i)(?:'ll|'d|'re|'m)?(?:\s+(?!you\b)[\w']+){0,4}\s*$", re.I)


def wording_problems(text, move, est, tz=None):
    """Phrases in `text` that the facts of this move do not support (the three checks above)."""
    norm = (text or "").translate(str.maketrans({"’": "'", "‘": "'", "ʼ": "'"}))
    problems = [f'says "{m.group(0)}", which nothing in the conversation supports' for m in _AS_SAID.finditer(norm)]
    if move.slot is not None and slot_day(move.slot, tz) != slot_day(est.default_slot, tz):
        problems += [f'calls the offered slot the quoted or same one ("{m.group(0)}"); move {move.id} is another day'
                     for m in _SAME_SLOT.finditer(norm)]
    if move.checkin_days is not None:
        for m in _CHECK_BACK.finditer(norm):
            if not _SHOP_SUBJECT.search(norm[max(0, m.start() - 60):m.start()]):
                problems.append(f'asks the customer to check back ("{m.group(0)}"); the shop checks back in move {move.id}')
    return problems


# --- dates in a written message --------------------------------------------------------------------------------------
# A message may name only the move's own day: the slot's day, or the check-in day (now + the check-in span), written as a
# date ("Monday, Nov 9", "11/9", "the 9th", "9 de noviembre"), its weekday, or the move's own span ("in 30 days",
# "in about a month"). Anything else that places the job in time is a problem: another date, a bare month name
# ("late Dec", "early Jan", "en diciembre"), a month-relative phrase ("next month", "the holidays", "after New Year"),
# a relative day ("tomorrow") or another span ("in 3 weeks").
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_ES_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
_MONTH = r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|" \
         r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_ES_MONTH = r"(enero|ene|febrero|feb|marzo|mar|abril|abr|mayo|may|junio|jun|julio|jul|agosto|ago|septiembre|setiembre|" \
            r"sept?|set|octubre|oct|noviembre|nov|diciembre|dic)\.?"
_MONTH_DAY = re.compile(_MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH[2:] + r"(?![a-z])", re.I)
_ES_DAY_MONTH = re.compile(r"\b(\d{1,2})\s+de\s+" + _ES_MONTH + r"(?![a-z])", re.I)
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_NUMERIC = re.compile(r"(?<![\d$.,])(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b")
_ORDINAL = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b", re.I)
# a month named without its day: English and Spanish names and abbreviations. "May" alone is the modal verb ("you may
# want to"), so it counts only after a month word such as "in", "early", "mid", "late", "end of", "by"; "ago" is the
# English word, so the Spanish abbreviation counts only inside a date.
_BARE_MONTH = re.compile(
    r"\b(january|february|march|april|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|"
    r"aug|sept|sep|oct|nov|dec|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|"
    r"noviembre|diciembre|ene|abr|dic)\b\.?|"
    r"\b(?:in|early|mid|late|end of|beginning of|start of|since|until|till|by|before|after|through|this|next|last)[\s-]+may\b",
    re.I)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY = re.compile(r"\b(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|"
                      r"sat(?:urday)?|sun(?:day)?)\b", re.I)
_RELATIVE = re.compile(
    r"\b(today|tonight|tomorrow|yesterday|this weekend|next weekend|next week|this week|next month|this month|"
    r"last month|end of (?:the )?month|beginning of (?:the )?month|start of (?:the )?month|next year|this year|"
    r"end of (?:the )?year|the holidays|holiday season|after the holidays|new year'?s?|christmas|thanksgiving|"
    r"pr[oó]ximo mes|el mes que viene|este mes|fin de mes|fin de a[nñ]o|navidad|a[nñ]o nuevo)\b", re.I)
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10, "a couple of": 2, "a couple": 2, "a few": 3, "few": 3}
_SPAN = re.compile(r"\b(\d+|a couple of|a couple|a few|few|an|a|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
                   r"(days?|weeks?|months?)\b", re.I)
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}


def _month(s):
    s = s.lower()
    return (_MONTHS.index(s[:3]) if s[:3] in _MONTHS else _ES_MONTHS.index(s[:3])) + 1


def own_days(move, now=None, tz=None):
    """The days a message for this move may name: the slot's day, and the check-in day when `now` is known; calendar
    days in the customer's zone `tz` (UTC when unknown)."""
    days = set()
    if move.slot is not None:
        days.add(slot_day(move.slot, tz))
    if move.checkin_days is not None and now is not None:
        days.add(local(now + timedelta(days=move.checkin_days), tz).date())
    return days


def date_problems(text, move, now=None, tz=None):
    """Every date, month, weekday, relative day or span of days in `text` that is not the move's own (see the comment
    above). Conservative on purpose: a mention the code cannot match to the move is a problem, and the message falls
    back to the template."""
    text = (text or "").translate(str.maketrans({"’": "'", "‘": "'"}))
    days = own_days(move, now, tz)
    md = {(d.month, d.day) for d in days}
    span_days = move.checkin_days
    problems, taken = [], []

    def bad(what):
        problems.append(f"{what} is not part of move {move.id}")

    def inside(m):
        return any(a <= m.start() < b or m.start() <= a < m.end() for a, b in taken)

    for pattern, mo, dy in ((_MONTH_DAY, 1, 2), (_DAY_MONTH, 2, 1), (_ES_DAY_MONTH, 2, 1)):
        for m in pattern.finditer(text):
            if inside(m):
                continue
            taken.append(m.span())
            if (_month(m.group(mo)), int(m.group(dy))) not in md:
                bad(f"date {m.group(0)!r}")
    for m in _ISO.finditer(text):
        taken.append(m.span())
        if not any((int(m.group(1)), int(m.group(2)), int(m.group(3))) == (d.year, d.month, d.day) for d in days):
            bad(f"date {m.group(0)!r}")
    for m in _NUMERIC.finditer(text):
        taken.append(m.span())
        if (int(m.group(1)), int(m.group(2))) not in md:
            bad(f"date {m.group(0)!r}")
    for m in _ORDINAL.finditer(text):
        if inside(m):
            continue
        if int(m.group(1)) not in {d.day for d in days}:
            bad(f"day {m.group(0)!r}")
    for m in _BARE_MONTH.finditer(text):
        if not inside(m):
            bad(f"month {m.group(0)!r}")
    for m in _WEEKDAY.finditer(text):
        w = next(i for i, d in enumerate(_WEEKDAYS) if d.startswith(m.group(1).lower()[:3]))
        if w not in {d.weekday() for d in days}:
            bad(f"weekday {m.group(0)!r}")
    for m in _RELATIVE.finditer(text):
        bad(f"relative day {m.group(0)!r}")
    for m in _SPAN.finditer(text):
        n = m.group(1).lower()
        count = int(n) if n.isdigit() else _NUMBER_WORDS[n]
        unit = m.group(2).lower().rstrip("s")
        if span_days is None or count * _UNIT_DAYS[unit] != span_days:
            bad(f"span {m.group(0)!r}")
    return problems
