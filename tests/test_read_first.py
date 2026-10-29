"""ReadFirstAgent: code decides the price, Nemotron reads the reply and writes the words. Fake client only."""
from datetime import datetime, timedelta, timezone

from fakes import FakeClient, call, raw
from read_first_fakes import facts, text

from second_ask import agent as A, menu as MN, models as M, tf_client as tf
from second_ask.compliance import Recipient
from second_ask.trace import Trace
from second_read.readers import nemotron as N
from second_read.readers import reader_input, render
from read_first import agent as RA, decide as D, reader as R, writer as W

T0 = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)   # Tuesday
NOW = T0 + timedelta(days=2, hours=1)                      # Thursday Oct 22, 11:00 New York
# List $740 (repair $560 + optional surge protector $180), floor $690; without the surge protector: $560, floor $525.
# Open slots Fri Oct 23, Sat Oct 24, Thu Oct 29; quoted Tue Oct 27 (not open). offer() today = Oct 22.


def estimate(sent_at=T0, channel="email"):
    items = (MN.LineItem("Capacitor and contactor", 560.0, 300.0), MN.LineItem("Surge protector", 180.0, 99.0, True))
    return MN.Estimate("E-1", "Cool Air", "HVAC", "AC repair", items, 0.07, sent_at, channel,
                       MN.Slot("s-def", T0 + timedelta(days=7, hours=-1)))


def conv(reply="Another shop quoted me $700. Can you do better?", est=None, **kw):
    est = est or estimate()
    c = A.Conversation(est, Recipient("America/New_York", "FL"),
                       [MN.Slot(f"s{i}", T0 + timedelta(days=i, hours=-1)) for i in (3, 4, 9)],
                       trace=Trace(sink=lambda _: None), **kw)
    if reply is not None:
        c.events.append(A.Event(NOW - timedelta(minutes=5), "customer", est.channel, reply))
    return c


def agent(script):
    fc = FakeClient(script)
    return RA.ReadFirstAgent(fc), fc


def models_of(fc):
    return [x["model"] for x in fc.calls]


# --- the main path ---------------------------------------------------------------------------------------------------

def test_competitor_price_gets_the_floor_concession_owner_tap_and_the_slot_offer_chose():
    ag, fc = agent([facts(ceiling_usd=700), text("We can do the full repair for $690, Thursday Oct 29. That beats the $700 quote.")])
    c = conv()
    rd = ag.decide(c, NOW)
    assert rd.path == "reply" and rd.facts.ceiling_usd == 700
    assert rd.offer == {"stop": False, "dropped": [], "price": 690.0, "timing": {"kind": "slot", "day": "2026-10-29"}}
    assert rd.move.id == "concede_pkg_floor" and rd.move.price == 690 and rd.mapping.exact
    assert rd.move.slot.id == "s9" and rd.needs_owner
    assert rd.message == ("[Cool Air assistant, not a person] We can do the full repair for $690, Thursday Oct 29. "
                          "That beats the $700 quote. Reply STOP to opt out.")
    assert models_of(fc) == [M.SUPER, M.SUPER] and not rd.written.template_used
    owner = A.OwnerTapStub()
    assert ag.apply(c, rd, owner) and c.offer.price == 690 and c.offer.slot.id == "s9"
    assert owner.requests == [("E-1", "concede_pkg_floor", 690)]
    assert [(s.stage, s.actor, s.model) for s in rd.stages] == [
        ("read", "Nemotron 3 Super", M.SUPER), ("decide", "code", None), ("write", "Nemotron 3 Super", M.SUPER),
        ("owner", "owner tap", None)]
    assert [s.step for s in c.trace.steps] == ["read", "decide", "write", "owner"]
    assert [s.model for s in c.trace.steps] == [M.SUPER, None, M.SUPER, None]
    assert [s.response_id for s in c.trace.steps] == ["read-1", None, "write-1", None]


def test_reader_call_is_the_frozen_reader_request():
    ag, fc = agent([facts(ceiling_usd=700), text("We can do $690 for the full repair.")])
    c = conv()
    ag.decide(c, NOW)
    messages, params = N.request("tool", render(reader_input(R.reader_case(c, c.events[-1].text, NOW), R.ConversationCard(c))))
    assert fc.calls[0]["messages"] == messages and fc.calls[0]["params"] == params
    assert fc.calls[0]["messages"][0]["content"] == N.SYSTEM_PROMPT + N.TOOL_SUFFIX
    assert params["reasoning_effort"] == "none" and params["tool_choice"]["function"]["name"] == "record_facts"
    w = fc.calls[1]
    assert w["model"] == M.SUPER and w["params"]["reasoning_effort"] == "none"
    assert w["params"]["tool_choice"] == {"type": "function", "function": {"name": W.TOOL_NAME}}
    assert "Price: $690" in w["messages"][1]["content"] and "Slot: Thursday Oct 29" in w["messages"][1]["content"]


def test_seconds_and_dollars_per_stage_come_from_the_replies():
    ag, fc = agent([facts(ceiling_usd=700), text("We can do $690.")])
    rd = ag.decide(conv(), NOW)
    read, decide, write = rd.stages
    assert read.seconds == 0.5 and read.usd == round((800 * 0.30 + 60 * 0.90) / 1e6, 6)
    assert write.seconds == 0.5 and write.usd == round((900 * 0.30 + 50 * 0.90) / 1e6, 6)
    assert decide.usd == 0.0 and decide.model is None


def test_a_ceiling_below_the_floor_gets_the_floor_never_less():
    ag, _ = agent([facts(ceiling_usd=500), text("The lowest we can go is $690.")])
    c = conv("I can only pay $500, that's my limit")
    rd = ag.decide(c, NOW)
    assert rd.offer["price"] == 690.0 and rd.move.price == 690 >= c.estimate.floor()
    assert rd.move.id == "concede_pkg_floor" and "$500" not in rd.message


def test_dropping_an_optional_item_needs_no_tap():
    ag, _ = agent([facts(drop_items=["surge protector"]), text("Sure, just the repair for $560.")])
    c = conv("Skip the surge protector please")
    rd = ag.decide(c, NOW)
    assert rd.offer["dropped"] == ["Surge protector"] and rd.move.id == "drop_surge_protector" and rd.move.price == 560
    assert not rd.needs_owner
    assert ag.apply(c, rd, A.OwnerTapStub()) and c.offer.dropped == frozenset({"Surge protector"})
    assert rd.stages[-1].stage == "owner" and rd.stages[-1].note.startswith("not needed")


def test_ready_customer_gets_the_slot_offer_chose_at_the_standing_price():
    ag, _ = agent([facts(ready=True), text("Great, we can come Thursday Oct 29 at $740.")])
    rd = ag.decide(conv("Looks good, when can you come?"), NOW)
    assert rd.move.id == "offer_slot" and rd.move.slot.id == "s9" and rd.move.price == 740 and rd.mapping.exact
    assert not rd.written.template_used


def test_deadline_maps_to_the_earlier_slot_on_the_menu():
    ag, _ = agent([facts(need_by="2026-10-24"), text("We can come Friday, Oct 23 for $740.")])
    rd = ag.decide(conv("Need it fixed by Saturday"), NOW)
    assert rd.offer["timing"] == {"kind": "slot", "day": "2026-10-23", "meets_deadline": True}
    assert rd.move.id == "earlier_slot_1" and not rd.written.template_used


def test_wait_and_decider_become_check_ins():
    ag, _ = agent([facts(wait_weeks=2), text("No rush. We'll check back in 14 days.")])
    rd = ag.decide(conv("Can't do it for a couple of weeks"), NOW)
    assert rd.move.id == "checkin_14" and not rd.written.template_used
    ag, _ = agent([facts(decider="my wife"), text("Take your time with your wife. We'll check back in a week.")])
    rd = ag.decide(conv("Need to ask my wife first"), NOW)
    assert rd.move.id == "checkin_7" and not rd.written.template_used and rd.move.price == 740


def test_nothing_actionable_is_answered():
    ag, _ = agent([facts(), text("The $740 covers the capacitor, contactor and a surge protector.")])
    rd = ag.decide(conv("What does this include?"), NOW)
    assert rd.move.id == "answer" and rd.offer["price"] == 740.0


# --- opt-out and stop ------------------------------------------------------------------------------------------------

def test_stop_keyword_stops_before_any_model_call():
    ag, fc = agent([])
    c = conv("STOP")
    rd = ag.decide(c, NOW)
    assert rd.move is None and rd.path == "stopped" and c.recipient.opted_out and c.status == "opted_out"
    assert fc.calls == [] and c.trace.steps == []
    assert ag.decide(c, NOW + timedelta(days=1)).move is None and fc.calls == []


def test_a_stop_the_reader_reads_also_stops_and_nothing_is_written():
    ag, fc = agent([facts(stop=True)])
    c = conv("No me contacten más, gracias")
    rd = ag.decide(c, NOW)
    assert rd.move is None and c.recipient.opted_out and c.status == "opted_out" and len(fc.calls) == 1
    assert "request to stop" in rd.stop_reason and not ag.apply(c, rd, A.OwnerTapStub())


def test_ambiguous_opt_out_word_is_flagged_and_read():
    ag, fc = agent([facts(need_by="2026-10-31"), text("We can come Friday, Oct 23 for $740.")])
    rd = ag.decide(conv("End of the month works for us"), NOW)
    assert rd.move is not None and any("opt-out word" in f for f in rd.flags) and len(fc.calls) == 2


# --- silence: code only ----------------------------------------------------------------------------------------------

def test_silence_is_decided_by_rules_with_no_model_call():
    ag, fc = agent([])
    c = conv(None)
    rd = ag.decide(c, NOW)
    assert rd.path == "silence" and rd.branch == "silence_resend" and rd.move.id == "resend_sms"
    assert fc.calls == [] and rd.message.startswith("[Cool Air assistant, not a person] Resending your HVAC estimate")
    assert [(s.stage, s.model) for s in rd.stages] == [("decide", None)]


def test_silence_after_a_check_in_offers_the_slot_without_a_model():
    ag, fc = agent([])
    c = conv(None)
    c.events.append(A.Event(NOW - timedelta(days=1), "customer", "email", "maybe in two weeks"))
    c.events.append(A.Event(NOW - timedelta(hours=20), "shop", "email", "No rush.", "checkin_14"))
    rd = ag.decide(c, NOW)
    assert rd.branch == "silence_checkin" and rd.move.id == "offer_slot" and fc.calls == []


# --- the text can never change the offer -----------------------------------------------------------------------------

def test_model_text_with_another_price_is_replaced_by_the_template():
    ag, _ = agent([facts(ceiling_usd=700), text("Fine, $650 and we're done.")])
    rd = ag.decide(conv(), NOW)
    assert "$650" not in rd.message and "$690" in rd.message and rd.written.template_used
    assert any("replaced by the template" in f for f in rd.flags)
    assert rd.stages[2].stage == "write" and not rd.stages[2].ok


def test_model_text_with_a_percent_or_another_date_is_replaced():
    ag, _ = agent([facts(ceiling_usd=700), text("We can take 10% off, so $690.")])
    assert ag.decide(conv(), NOW).written.template_used
    ag, _ = agent([facts(ceiling_usd=700), text("We can do $690 on Friday.")])
    rd = ag.decide(conv(), NOW)
    assert rd.written.template_used and any("weekday" in p for p in rd.written.problems)
    ag, _ = agent([facts(wait_weeks=2), text("No rush, we'll check back in 3 weeks.")])
    assert ag.decide(conv("in a couple of weeks"), NOW).written.template_used


def test_writer_failure_gives_the_template():
    ag, _ = agent([facts(ceiling_usd=700), tf.TFError(503, "busy")])
    c = conv()
    rd = ag.decide(c, NOW)
    assert rd.written.template_used and rd.message.startswith("[Cool Air assistant, not a person] We can do")
    assert "$690" in rd.message and [(s.step, s.platform_error) for s in c.trace.steps][-1] == ("write", True)
    ag, _ = agent([facts(ceiling_usd=700), raw(content="Sure, $690!")])  # no tool call
    assert ag.decide(conv(), NOW).written.template_used


# --- reader failure and fallbacks ------------------------------------------------------------------------------------

def test_reader_failure_uses_the_code_default_and_never_reads_empty_facts():
    ag, fc = agent([tf.TFError(503, "busy")])
    c = conv()
    rd = ag.decide(c, NOW)
    assert rd.facts is None and rd.offer is None and rd.move.id == "ask_why" and rd.move.price == 740
    assert any("read failed (platform" in f for f in rd.flags) and len(fc.calls) == 1
    assert [(s.step, s.ok) for s in c.trace.steps] == [("read", False), ("decide", True)]


def test_invalid_read_is_retried_once_by_the_frozen_reader():
    bad = raw(tool_calls=[call(N.TOOL_NAME, {"stop": "no"})])
    ag, fc = agent([bad, facts(ceiling_usd=700), text("We can do $690.")])
    c = conv()
    rd = ag.decide(c, NOW)
    assert rd.move.id == "concede_pkg_floor" and [s.ok for s in c.trace.steps][:2] == [False, True]


def test_no_menu_move_for_the_offer_falls_back_to_the_code_default_with_a_flag():
    # an SMS estimate 80 days old: the 90-day rule leaves check-ins up to 10 days; offer() wants 14
    est = estimate(sent_at=NOW - timedelta(days=80), channel="sms")
    ag, _ = agent([facts(wait_weeks=2), text("Quick question: is it the timing?")])
    rd = ag.decide(conv("in a couple of weeks", est=est), NOW)
    assert rd.offer["timing"] == {"kind": "checkin", "days": 14} and not rd.mapping.exact
    assert rd.move.id == "ask_why" and any("no menu move carries" in f for f in rd.flags)


def test_a_price_already_conceded_is_never_raised_back_to_list():
    c = conv("Ok, when can you come?")
    c.offer = MN.Offer(frozenset(), 705.0, c.estimate.default_slot, "email")
    ag, _ = agent([facts(ready=True), text("We can book you at $705.")])
    rd = ag.decide(c, NOW)
    assert rd.offer["price"] == 740.0 and not rd.mapping.exact
    assert rd.move.id == "offer_slot" and rd.move.price == 705


def test_partial_package_floor_not_on_the_menu_falls_back():
    items = (MN.LineItem("Main job", 1000.0, 550.0), MN.LineItem("Opt A", 200.0, 110.0, True),
             MN.LineItem("Opt B", 100.0, 55.0, True))
    est = MN.Estimate("E-2", "Cool Air", "HVAC", "Main job", items, 0.10, T0, "email", MN.Slot("s-def", T0 + timedelta(days=9)))
    ag, _ = agent([facts(drop_items=["Opt A"], ceiling_usd=900), text("We can look at the price.")])
    rd = ag.decide(conv("No Opt A, and 900 is my max", est=est), NOW)
    assert rd.offer["dropped"] == ["Opt A"] and rd.offer["price"] == est.floor(frozenset({"Opt A"}))
    assert not rd.mapping.exact and rd.move.id == "answer" and rd.move.price == 1300  # chooser default for "price"


# --- owner and booking -----------------------------------------------------------------------------------------------

def test_owner_decline_sends_nothing():
    ag, _ = agent([facts(ceiling_usd=700), text("We can do $690.")])
    c = conv()
    rd = ag.decide(c, NOW)
    assert not ag.apply(c, rd, A.OwnerTapStub(approve=False))
    assert c.offer.price == 740 and not c.sent_times and rd.stages[-1].note.startswith("declined")


def test_booking_reuses_the_super_tool_loop_and_logs_a_book_stage():
    ag, fc = agent([facts(ceiling_usd=700), text("We can do $690."),
                    raw(tool_calls=[call("book_job", {"slot_id": "s9", "estimate_id": "E-1", "price": 690})]),
                    raw(tool_calls=[call("record_job", {"job_id": "J-E-1-1", "note": "booked"})])])
    c = conv()
    rd = ag.decide(c, NOW)
    ag.apply(c, rd, A.OwnerTapStub())
    c.events.append(A.Event(NOW + timedelta(hours=1), "customer", "email", "YES"))
    job = ag.book(c, NOW + timedelta(hours=1), rd)
    assert job["price"] == 690 and job["slot_id"] == "s9" and job["recorded"] and c.status == "booked"
    assert [s.stage for s in rd.stages] == ["read", "decide", "write", "owner", "book"]
    assert rd.stages[-1].model == M.SUPER and rd.stages[-1].usd > 0 and models_of(fc)[2:] == [M.SUPER, M.SUPER]


def test_old_path_is_untouched_and_still_callable():
    from fakes import choice, label
    old = A.Agent(FakeClient([label("price"), choice("concede_pkg_5", "We can do $705.")]))
    assert old.decide(conv(), NOW).move.id == "concede_pkg_5"


def test_decision_serialises_for_the_demo():
    import json
    ag, _ = agent([facts(ceiling_usd=700), text("We can do $690.")])
    c = conv()
    rd = ag.decide(c, NOW)
    ag.apply(c, rd, A.OwnerTapStub())
    out = json.loads(json.dumps(rd.to_dict()))
    assert out["move"]["id"] == "concede_pkg_floor" and out["gate"]["ok"] and out["facts"]["ceiling_usd"] == 700
    assert [s["stage"] for s in out["stages"]] == ["read", "decide", "write", "owner"]


# --- the date checker ------------------------------------------------------------------------------------------------

def test_date_problems_accepts_only_the_moves_own_day_and_span():
    slot = MN.Slot("s9", datetime(2026, 10, 29, 13, tzinfo=timezone.utc))
    move = MN.Move("offer_slot", "offer_slot", "", frozenset(), 740, slot, "email", proposes_booking=True)
    for ok in ("See you Thursday, Oct 29.", "Thu 10/29 works.", "on 2026-10-29", "the 29th", "29 October",
               "Sunrise Home Services can come"):
        assert D.date_problems(ok, move) == [], ok
    for bad in ("Friday works", "Oct 30", "10/30", "the 30th", "tomorrow", "in 3 days", "November 29"):
        assert D.date_problems(bad, move), bad
    chk = MN.Move("checkin_14", "checkin", "", frozenset(), 740, None, "email", checkin_days=14)
    assert D.date_problems("We'll check back in 14 days, or two weeks.", chk) == []
    assert D.date_problems("We'll check back in a week.", chk) and D.date_problems("See you Oct 29", chk)
