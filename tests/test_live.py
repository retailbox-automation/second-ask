"""Live checks against Token Factory. Skipped unless NEBIUS_API_KEY is set; every call goes through the capped ledger.

    python3 tools/tfenv.py -- python3 -m pytest -m live      (from the campaign repo; costs about one cent)
"""
import os
from datetime import timedelta

import pytest

from second_ask import agent as A, models as M
from second_ask.tf_client import Client
from second_ask.trace import Trace

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not os.environ.get("NEBIUS_API_KEY"), reason="needs NEBIUS_API_KEY")]


@pytest.fixture(scope="module")
def client():
    return Client("b2", max_attempts=4, fallbacks=M.FALLBACKS)


def _conv(text):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spikes"))
    from states import OPEN, QUOTED, T0, ESTIMATES
    from second_ask.compliance import Recipient
    from second_ask.menu import Estimate
    shop, trade, title, items = ESTIMATES["hvac_repair"]
    est = Estimate("E-LIVE", shop, trade, title, items, 0.07, T0, "sms", QUOTED)
    c = A.Conversation(est, Recipient("America/New_York", "FL"), list(OPEN), trace=Trace(sink=lambda _: None))
    now = T0 + timedelta(days=2, hours=1)
    c.events.append(A.Event(now - timedelta(minutes=10), "customer", "sms", text))
    return c, now


def test_live_label_and_move_for_a_slot_request(client):
    c, now = _conv("The AC is out and it's 92 in here. Your slot is next week; someone else can come tomorrow.")
    d = A.Agent(client, A.AgentConfig(classifier_model=M.LIGHTNING, classifier_method="json_object")).decide(c, now)
    assert d.label.valid and d.move is not None and d.message.startswith("[Cool Air Heating & Cooling assistant")
    assert all(s.response_id for s in c.trace.steps if s.model)
    assert d.move.price >= c.estimate.floor(d.move.dropped)


def test_live_booking_loop_books_at_the_agreed_price(client):
    c, now = _conv("Looks good. Book it.")
    job = A.Agent(client).book(c, now)
    assert job is not None and job["price"] == c.offer.price and job["recorded"]
    assert c.status == "booked" and job["slot_id"] not in [s.id for s in c.open_slots]
