"""Wording the facts do not support, and the template in the customer's local time (fact-check of the recorded morning)."""
import re
from datetime import datetime, timezone

from fakes import FakeClient
from read_first_fakes import facts, text

from second_ask import menu as MN, tf_client as tf
from second_ask.tournament import buyers as B
from read_first import agent as RA, decide as D, morning as MO, template as TP, writer as W

# the recorded drafts (demo/data/read_first_morning.json at 74b127d), disclosure and STOP line removed
DRAFT_0001 = ("Hi there! We’ve adjusted your estimate B007 to $360 for fixing the leaking shower valve as discussed. "
              "Since you need your landlord’s approval first, just let us know when they give the OK—reply YES to "
              "confirm we can proceed then. The quoted slot remains Mon Nov 09 if all clears.")
DRAFT_0003 = ("We’ll hold your estimate B178 at $9,560 for Replace 3-ton AC and air handler, Smart thermostat, UV air "
              "purifier. Check back in 30 days if you’d like to proceed then. Reply YES when ready.")


def morning(line_id):
    item = next(x for x in MO.inbox() if x["id"] == line_id)
    return item["case"], item["conv"], item["now"]


def decide(line_id, draft):
    case, conv, now = morning(line_id)
    gold = {**case["gold"], "drop_items": list(case["gold"]["drop_items"])}
    ag = RA.ReadFirstAgent(FakeClient([facts(**gold), text(draft)] if draft is not None else [facts(**gold), tf.TFError(503, "busy")]))
    return ag.decide(conv, now), conv


# --- regressions from the recorded morning ---------------------------------------------------------------------------

def test_0001_quoted_slot_remains_and_as_discussed_get_the_template():
    rd, conv = decide("test-0001", DRAFT_0001)
    assert rd.move.id == "concede_pkg_3" and rd.written.template_used
    assert rd.written.problems == ['says "as discussed", which nothing in the conversation supports',
                                   'calls the offered slot the quoted or same one ("quoted slot remains"); move '
                                   'concede_pkg_3 is another day']
    assert any('"as discussed"' in f for f in rd.flags) and "as discussed" not in rd.message


def test_0003_check_back_put_on_the_customer_gets_the_template():
    rd, _ = decide("test-0003", DRAFT_0003)
    assert rd.move.id == "checkin_30" and rd.written.template_used
    assert rd.written.problems == ['asks the customer to check back ("Check back in"); the shop checks back in move checkin_30']
    assert rd.message.startswith("[Sunrise Home Services assistant, not a person] No rush at all. I will check back in 30 days")


def test_0006_template_shows_the_customers_local_time():
    rd, conv = decide("test-0006", None)  # the writer fails: the template is sent
    assert conv.recipient.tz == "America/New_York" and rd.written.template_used
    assert "Sun Nov 08 09:00 AM" in rd.message and "02:00 PM" not in rd.message


# --- each check, both ways -------------------------------------------------------------------------------------------

QUOTED = MN.Slot("q", datetime(2026, 11, 12, 14, tzinfo=timezone.utc))      # Thu Nov 12, 9:00 AM New York
EST = MN.Estimate("E", "S", "plumbing", "Job", (MN.LineItem("Job", 370.0, 200.0),), 0.1,
                  datetime(2026, 11, 2, 14, tzinfo=timezone.utc), "email", QUOTED)
EARLIER = MN.Move("concede_pkg_3", "concede", "", frozenset(), 360, MN.Slot("s", datetime(2026, 11, 9, 14, tzinfo=timezone.utc)),
                  "email", needs_owner=True, proposes_booking=True)
AT_QUOTED = MN.Move("offer_slot", "offer_slot", "", frozenset(), 370, QUOTED, "email", proposes_booking=True)
CHECKIN = MN.Move("checkin_30", "checkin", "", frozenset(), 370, None, "email", checkin_days=30)
TZ = "America/New_York"


def test_as_discussed_is_flagged_and_ordinary_as_passes():
    for bad in ("As discussed, $360.", "as agreed, here it is", "As we discussed, Mon Nov 9.", "as promised"):
        assert D.wording_problems(bad, AT_QUOTED, EST, TZ), bad
    for ok in ("Reply YES as soon as you're ready.", "Happy to discuss the price.", "We agreed to nothing yet? Ask away.",
               "Just as you asked: $370."):
        assert D.wording_problems(ok, AT_QUOTED, EST, TZ) == [], ok


def test_offered_slot_called_the_quoted_one_is_flagged_only_when_it_is_another_day():
    for bad in ("The quoted slot remains Mon Nov 09.", "Your quoted slot is Mon Nov 9.", "We kept the original slot.",
                "Same slot as before: Mon Nov 9.", "Your appointment stays Mon Nov 9.", "your quoted slot, Mon Nov 09"):
        assert D.wording_problems(bad, EARLIER, EST, TZ), bad
        assert D.wording_problems(bad, AT_QUOTED, EST, TZ) == [], bad  # true when the offered day is the quoted one
    for ok in ("Mon Nov 9 is earlier than your quoted slot.", "The price remains $360.",
               "Your quoted slot was Thu Nov 12; we can come Mon Nov 9."):
        assert D.wording_problems(ok, EARLIER, EST, TZ) == [], ok


def test_check_in_put_on_the_customer_is_flagged_and_the_shop_checking_back_passes():
    for bad in ("Check back in 30 days.", "Feel free to check back in 30 days.", "Please get back to us in a month.",
                "You can reach out in 30 days.", "We hope you'll check back in 30 days.", "Follow up with us in 30 days."):
        assert D.wording_problems(bad, CHECKIN, EST, TZ), bad
    for ok in ("We'll check back in 30 days.", "I will reach out in 30 days.", "We will be sure to check back in 30 days.",
               "We’ll follow up in 30 days.", "No rush. I'll check back in about a month."):
        assert D.wording_problems(ok, CHECKIN, EST, TZ) == [], ok
    assert D.wording_problems("Check back in 30 days.", AT_QUOTED, EST, TZ) == []  # only check-in moves


def test_writer_prompt_states_the_slots_and_who_checks_back():
    case, conv, now = morning("test-0001")
    msgs, _ = W.request(conv, EARLIER_FOR(conv), now)
    user = msgs[1]["content"]
    assert "Quoted slot on the estimate: Thursday Nov 12." in user
    assert "Offered slot: Mon Nov 09 09:00 AM local time, earlier than the quoted slot." in user
    assert 'Never write "as discussed"' in msgs[0]["content"]
    _, conv3, now3 = morning("test-0003")
    user3 = W.request(conv3, CHECKIN, now3)[0][1]["content"]
    assert "The shop will check back in 30 days." in user3 and "never ask the customer to check back" in user3


def EARLIER_FOR(conv):
    slot = next(s for s in conv.open_slots if s.start.date().isoformat() == "2026-11-09")
    return MN.Move("concede_pkg_3", "concede", "", frozenset(), 360, slot, "sms", needs_owner=True, proposes_booking=True)


# --- the template never prints a UTC clock time ----------------------------------------------------------------------

def test_no_template_for_any_card_contains_a_utc_clock_time():
    cards = B.load(MO.ROOT / "tournament" / "buyers.json")
    checked = 0
    for card in cards:
        est = card.estimate()
        offer = MN.initial_offer(est)
        for move in MN.build_menu(est, offer, card.open_slots()):
            slot = move.slot or offer.slot
            got = TP.template(move, est, offer, card.tz)
            utc, local = f"{slot.start:%I:%M %p}", f"{slot.start.astimezone(TP.ZoneInfo(card.tz)):%I:%M %p}"
            assert utc != local  # every card is in a US zone
            if "{slot}" in MN.TEMPLATES[move.kind]:
                assert utc not in got and local in got, (card.id, move.id, got)
                checked += 1
            assert got == MN.template_message(move, est, offer).replace(
                f"{slot.start:%a %b %d %I:%M %p}", TP.slot_text(slot, card.tz))  # same words otherwise
            assert not re.search(r"\d{2}:\d{2}", TP.template(move, est, offer, None))  # zone unknown: no clock time
    assert checked > 200


def test_morning_templates_are_local_too():
    for x in MO.inbox():
        conv = x["conv"]
        for move in D.menu_for(conv, x["now"]):
            got = TP.template(move, conv.estimate, conv.offer, conv.recipient.tz)
            slot = move.slot or conv.offer.slot
            assert f"{slot.start:%I:%M %p}" not in got
