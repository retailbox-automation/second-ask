"""Readers: turn one customer reply into Facts. Every reader gets exactly the same input.

Protocol (the rule reader in `readers/rules.py` and the Nemotron reader both follow it):

    class Reader:
        name: str
        def read(self, case) -> tuple[Facts | None, dict]: ...

`case` is one line of a split file (see second_read/cases.py). `reader_input(case)` is the whole input a reader may
use: the estimate (title, line items with price and optional flag, total), today's date with its weekday, the quoted
slot and the reply text. A reader returns (Facts, meta) or, when its output cannot be validated, (None, meta): the
failure counts as a non-match and is never repaired. meta records at least:
    {"reader", "model", "seconds", "usd", "attempts", "error", "error_kind"}
with error_kind None, "invalid" (output failed the contract after the reader's own retry) or "platform" (the call
failed after the client's retries; the harness may re-run the line).
"""
from datetime import date

from .. import cases as C

ERROR_KINDS = (None, "invalid", "platform")


def _weekday(d):
    return date.fromisoformat(d).strftime("%A")


def reader_input(case, card=None):
    """Everything a reader may see for one line, as plain data."""
    card = card or C.card_for(case)
    est = card.estimate()
    today, quoted = case["today"], case["quoted_slot"]
    if case.get("weekday") and case["weekday"] != _weekday(today):
        raise ValueError(f"case {case['id']}: weekday {case['weekday']!r} is not {today}'s ({_weekday(today)})")
    if quoted != est.default_slot.start.date().isoformat():
        raise ValueError(f"case {case['id']}: quoted_slot {quoted} is not the card's quoted slot "
                         f"{est.default_slot.start.date().isoformat()}")
    return {"title": est.title, "trade": est.trade,
            "items": [{"name": i.name, "price": i.price, "optional": i.optional} for i in est.items],
            "total": est.list_price(), "today": today, "weekday": _weekday(today), "quoted_slot": quoted,
            "quoted_weekday": _weekday(quoted), "text": case["text"]}


def optional_items(inp):
    return [i["name"] for i in inp["items"] if i["optional"]]


def render(inp):
    """The reader input as text (what a model reader is shown)."""
    lines = [f"Estimate: {inp['title']} ({inp['trade']})", "Line items:"]
    lines += [f"- {i['name']}: ${i['price']:,.2f}{' (optional)' if i['optional'] else ''}" for i in inp["items"]]
    lines += [f"Total: ${inp['total']:,.2f}", f"Today: {inp['weekday']}, {inp['today']}",
              f"Quoted slot: {inp['quoted_weekday']}, {inp['quoted_slot']}", "", "Customer's reply:",
              '"""', inp["text"], '"""']
    return "\n".join(lines)
