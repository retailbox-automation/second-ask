from datetime import datetime, timedelta, timezone

import pytest

from fakes import FakeClient, call, choice, label, raw
from second_ask import agent as A, classifier as CL, menu as MN, models as M, tf_client as tf
from second_ask.compliance import Recipient
from second_ask.trace import Trace

T0 = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(days=2, hours=1)  # Thursday 11:00 New York


def estimate(sent_at=T0, channel="email"):
    items = (MN.LineItem("Capacitor and contactor", 560.0, 300.0), MN.LineItem("Surge protector", 180.0, 99.0, True))
    return MN.Estimate("E-1", "Cool Air", "HVAC", "AC repair", items, 0.07, sent_at, channel,
                       MN.Slot("s-def", T0 + timedelta(days=7, hours=-1)))


def conv(text="Another shop quoted me $640. Can you do better?", est=None, **kw):
    est = est or estimate()
    c = A.Conversation(est, Recipient("America/New_York", "FL"),
                       [MN.Slot(f"s{i}", T0 + timedelta(days=i, hours=-1)) for i in (3, 4, 9)], trace=Trace(sink=lambda _: None), **kw)
    if text is not None:
        c.events.append(A.Event(NOW - timedelta(minutes=5), "customer", est.channel, text))
    return c


def agent(script, **cfg):
    fc = FakeClient(script)
    return A.Agent(fc, A.AgentConfig(**cfg)), fc


def test_price_reply_gets_a_concession_with_owner_tap_and_disclosure():
    ag, fc = agent([label("price"), choice("concede_pkg_5", "We can do $705 for the full repair.")])
    c = conv()
    d = ag.decide(c, NOW)
    assert d.move.id == "concede_pkg_5" and d.needs_owner and d.effect["change"] == -35
    assert d.message.startswith("[Cool Air assistant, not a person] We can do $705")
    assert [x["model"] for x in fc.calls] == [M.SUPER, M.SUPER]
    assert fc.calls[0]["params"]["reasoning_effort"] == "none"
    owner = A.OwnerTapStub()
    assert ag.apply(c, d, owner) and c.offer.price == 705 and owner.requests == [("E-1", "concede_pkg_5", 705)]
    assert [s.step for s in c.trace.steps] == ["classify", "choose"] and all(s.response_id for s in c.trace.steps)


def test_owner_decline_sends_nothing():
    ag, _ = agent([label("price"), choice("concede_pkg_floor", "We can do $690.")])
    c = conv()
    d = ag.decide(c, NOW)
    assert not ag.apply(c, d, A.OwnerTapStub(approve=False))
    assert c.offer.price == 740 and not c.sent_times and "owner declined" in d.flags[-1]


def test_stop_keyword_stops_before_any_model_call():
    ag, fc = agent([])
    c = conv("STOP")
    d = ag.decide(c, NOW)
    assert d.move is None and c.recipient.opted_out and c.status == "opted_out" and fc.calls == []
    assert ag.decide(c, NOW + timedelta(days=1)).move is None


def test_label_model_opt_out_also_stops():
    ag, fc = agent([label("opt_out")])
    c = conv("we're done here, don't bother")
    assert ag.decide(c, NOW).move is None and c.recipient.opted_out and len(fc.calls) == 1


def test_invalid_choice_is_retried_then_code_default_is_flagged():
    ag, fc = agent([label("channel"), choice("make_it_free"), raw(content="I think resend.")])
    c = conv(None)
    d = ag.decide(c, NOW)
    assert d.move.id == "resend_sms" and not d.choice.valid and any("code default" in f for f in d.flags)
    assert len(fc.calls) == 3


def test_message_with_an_amount_below_the_floor_is_replaced():
    ag, _ = agent([label("price"), choice("concede_pkg_5", "Fine, $500 and we're done.")])
    d = ag.decide(conv(), NOW)
    assert "$500" not in d.message and "$705" in d.message and d.choice.template_used
    assert any("replaced by the template" in f for f in d.flags)


def test_invalid_label_is_retried_then_sent_to_the_fallback_model():
    bad = raw(tool_calls=[call("label_reply", {"label": "money", "confidence": "high", "evidence": "x"})])
    ag, fc = agent([bad, raw(content="price"), label("price"), choice("answer", "Here is what it covers.")],
                   classifier_model=M.LIGHTNING)
    d = ag.decide(conv(), NOW)
    assert [x["model"] for x in fc.calls] == [M.LIGHTNING, M.LIGHTNING, M.SUPER, M.SUPER]
    assert d.label.valid and d.label.model == M.SUPER and d.move.id == "answer"


def test_http_errors_are_logged_not_swallowed():
    ag, fc = agent([tf.TFError(503, "busy"), label("timing"), choice("checkin_14", "No rush, talk in two weeks.")])
    c = conv("Not ready yet, maybe after the holidays")
    d = ag.decide(c, NOW)
    assert d.move.id == "checkin_14"
    assert [(s.step, s.ok) for s in c.trace.steps] == [("classify", False), ("classify", True), ("choose", True)]


def test_old_sms_estimate_is_blocked_and_nothing_is_applied():
    est = estimate(sent_at=NOW - timedelta(days=100), channel="sms")
    ag, _ = agent([label("timing"), choice("answer", "Here is what the repair covers.")])
    c = conv("ok maybe later", est=est)
    d = ag.decide(c, NOW)
    assert d.message is None and "blocked by compliance" in d.stop_reason
    assert not ag.apply(c, d, A.OwnerTapStub())
    assert not [m.id for m in d.menu if m.kind == "checkin"]  # 90-day rule also removes SMS check-ins
    assert "resend_email" in [m.id for m in d.menu]  # email is still allowed


def test_conversations_do_not_share_state():
    ag, _ = agent([label("price"), choice("concede_pkg_3", "We can do $720."), label("ready_to_book"),
                   choice("offer_slot", "We can book you then.")])
    a, b = conv(), conv("Looks good, when can you come?")
    da, db = ag.decide(a, NOW), ag.decide(b, NOW)
    ag.apply(a, da, A.OwnerTapStub())
    ag.apply(b, db, A.OwnerTapStub())
    assert (a.offer.price, b.offer.price) == (720, 740)
    assert len(a.trace.steps) == 2 and len(b.trace.steps) == 2


def test_booking_loop_enforces_the_agreed_price():
    script = [raw(tool_calls=[call("find_slots", {"earliest_date": "2026-10-23", "duration_hours": 2})]),
              raw(tool_calls=[call("book_job", {"slot_id": "s3", "estimate_id": "E-1", "price": 650})]),
              raw(tool_calls=[call("book_job", {"slot_id": "s3", "estimate_id": "E-1", "price": 705})]),
              raw(tool_calls=[call("record_job", {"job_id": "J-E-1-1", "note": "booked"})])]
    ag, fc = agent(script)
    c = conv()
    c.offer = MN.Offer(frozenset(), 705.0, c.estimate.default_slot, "email")
    job = ag.book(c, NOW)
    assert job["price"] == 705 and job["recorded"] and c.status == "booked"
    assert "s3" not in [s.id for s in c.open_slots]
    tool_msgs = [m for m in fc.calls[2]["messages"] if m["role"] == "tool"]
    assert "agreed" in tool_msgs[-1]["content"]  # the $650 attempt was refused by code


def test_booking_refuses_a_price_below_the_floor_even_if_agreed():
    c = conv()
    c.offer = MN.Offer(frozenset(), 600.0, c.estimate.default_slot, "email")  # corrupted state below the 690 floor
    out = A.run_booking_tool(c, call("book_job", {"slot_id": "s3", "estimate_id": "E-1", "price": 600}), NOW)
    assert out == {"error": "price is below the owner's floor"} and c.status == "open"


def test_empty_tool_arguments_are_handled():
    c = conv()
    out = A.run_booking_tool(c, {"id": "x", "function": {"name": "get_shop_hours", "arguments": ""}}, NOW)
    assert "hours" in out


@pytest.mark.parametrize("method", CL.METHODS)
def test_label_parsing_per_method(method):
    good = {"label": "family", "confidence": "low", "evidence": "talk to my wife"}
    if method == "tool":
        r = tf._parse(M.LIGHTNING, raw(tool_calls=[call("label_reply", good)]), 0.1, 0.06, 0.24)
    else:
        r = tf._parse(M.LIGHTNING, raw(content="```json\n" + __import__("json").dumps(good) + "\n```"), 0.1, 0.06, 0.24)
    assert CL.parse(r, method) == (good, None)


@pytest.mark.parametrize("content,problem", [("not json", "not JSON"), ('["price"]', "not an object"),
                                             ('{"label":"cost","confidence":"high","evidence":"x"}', "not in"),
                                             ('{"label":"price","confidence":"sure","evidence":"x"}', "confidence"),
                                             ('{"label":"price","confidence":"high"}', "evidence")])
def test_label_parsing_rejects_bad_objects(content, problem):
    r = tf._parse(M.LIGHTNING, raw(content=content), 0.1, 0.06, 0.24)
    obj, why = CL.parse(r, "json_object")
    assert obj is None and problem in why


def test_render_state_says_when_the_customer_never_opened_the_estimate():
    text = CL.render_state(estimate(), [], NOW, views=0)
    assert "opened 0 time(s)" in text and "has not replied" in text


def test_agent_lets_the_message_quote_the_customers_higher_number():
    ag, _ = agent([label("price"), choice("drop_surge_protector", "That beats the $640 quote: the repair alone is $560.")])
    d = ag.decide(conv(), NOW)
    assert "$640" in d.message and not d.choice.template_used
