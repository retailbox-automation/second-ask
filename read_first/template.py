"""The code template for a move, in the customer's local time.

Same words as `second_ask.menu.template_message` (its TEMPLATES are used as they are), with one difference: the slot.
`template_message` prints the slot's UTC clock time ("Sun Nov 08 02:00 PM" for a 9:00 AM New York slot). Here:
- the recipient's time zone is known -> the slot in that zone, with its clock time ("Sun Nov 08 09:00 AM");
- it is unknown -> the day only, with no clock time ("Sun Nov 08"). A clock time in a guessed zone could be wrong by
  hours, a missing one only sends the customer to ask, so the safer choice is to leave it out. (The day is the UTC
  day; every slot here starts at 14:00 UTC, the same day in every US zone.)
`second_ask/menu.py` is pinned by the tournament manifests, so it is not changed.
"""
from zoneinfo import ZoneInfo

from second_ask.menu import TEMPLATES

DAY = "%a %b %d"


def local(dt, tz):
    return dt.astimezone(ZoneInfo(tz)) if tz else dt


def slot_text(slot, tz):
    """The slot as the customer reads it: local day and clock time, or the day alone when the zone is unknown."""
    return f"{local(slot.start, tz):{DAY} %I:%M %p}" if tz else f"{slot.start:{DAY}}"


def slot_day(slot, tz):
    """The slot's calendar day in the customer's zone (UTC when unknown)."""
    return local(slot.start, tz).date()


def template(move, est, offer, tz):
    items = ", ".join(i.name for i in est.items if i.name not in move.dropped)
    return TEMPLATES[move.kind].format(price=f"${move.price:,.0f}", items=items, trade=est.trade,
                                       slot=slot_text(move.slot or offer.slot, tz), days=move.checkin_days)
