"""Feed the frozen v4 reader from a live conversation.

`second_read.readers.nemotron.NemotronReader.read(case, card)` takes a v4 case (today, quoted slot, reply text) and a
card with `.estimate()` and `.open_slots()`. A live Conversation has an estimate and open slots but no card, so
`ConversationCard` stands in for one and `reader_case` builds the case. The reader itself (system prompt, request
parameters, `render()` of `reader_input()`, contract `parse()`) is used unchanged; a test checks that, for v4 test
lines, this path renders byte-identical text and sends identical request parameters.

Dates: "today" is the UTC date of `now`, as in the v4 cases and in `offer()`, which compares the UTC dates of slots.
"""
from dataclasses import dataclass
from datetime import date

from second_ask import models as M
from second_read.readers import nemotron as N

READER_MODEL, READER_METHOD = M.SUPER, "tool"  # the v4 primary arm (FREEZE.json arms.super)


@dataclass
class ConversationCard:
    """The card interface `reader_input()` and `offer()` need, backed by a live conversation."""
    conv: object

    def estimate(self):
        return self.conv.estimate

    def open_slots(self):
        return list(self.conv.open_slots)


def today_of(now):
    return now.date() if hasattr(now, "date") else date.fromisoformat(str(now))


def reader_case(conv, text, now):
    """A v4-shaped case for one customer reply (the fields `reader_input()` reads; no gold, no stratum)."""
    est = conv.estimate
    today = today_of(now)
    return {"id": f"{est.id}-live", "card_id": est.id, "today": today.isoformat(), "weekday": today.strftime("%A"),
            "quoted_slot": est.default_slot.start.date().isoformat(), "text": text}


def reader(client, model=READER_MODEL, method=READER_METHOD):
    return N.NemotronReader(model, method, client=client, name=f"read_first:{model}:{method}")


def read(client, conv, text, now, model=READER_MODEL, method=READER_METHOD):
    """(Facts | None, meta) from the frozen reader for this conversation's reply."""
    card = ConversationCard(conv)
    return reader(client, model, method).read(reader_case(conv, text, now), card)
