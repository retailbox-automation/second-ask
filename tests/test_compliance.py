from datetime import datetime, timedelta, timezone

import pytest

from second_ask import compliance as C

UTC = timezone.utc
NY = "America/New_York"


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


@pytest.mark.parametrize("text", ["STOP", "stop.", "Stop please", "UNSUBSCRIBE", "opt-out", "Opt out", "quit", "End",
                                  "cancel", "revoke", "Please stop texting me", "remove me from your list",
                                  "don't text me again", "no more messages thanks", "stop it"])
def test_opt_out_keywords_and_phrases(text):
    assert C.opt_out_status(text) == "opt_out"


@pytest.mark.parametrize("text", ["End of the month works for us", "Can we cancel the extra thermostat?",
                                  "We will stop by the store"])
def test_keyword_inside_a_sentence_is_only_flagged(text):
    assert C.opt_out_status(text) == "ambiguous"


@pytest.mark.parametrize("text", ["Sounds good, when can you come?", "", None, "Too expensive", "Stopping by later"])
def test_ordinary_messages_are_not_opt_outs(text):
    assert C.opt_out_status(text) is None


def test_florida_window_is_the_stricter_one():
    assert C.window_for("FL", "GA") == (8, 20)
    assert C.window_for("GA", "FL") == (8, 20)
    assert C.window_for("GA", "TX") == (8, 21)


def test_quiet_hours_use_the_recipients_local_time():
    # 20:30 in New York (00:30 UTC next day) is outside 8-20 -> next 8:00 New York time = 12:00 UTC (EDT, October).
    t = C.next_send_time(utc(2026, 10, 24, 0, 30), NY, (8, 20))
    assert t == utc(2026, 10, 24, 12, 0)
    # Same instant is 17:30 in Los Angeles: inside the window, sent now.
    assert C.next_send_time(utc(2026, 10, 24, 0, 30), "America/Los_Angeles", (8, 20)) == utc(2026, 10, 24, 0, 30)


def test_federal_window_allows_until_nine_pm():
    at = utc(2026, 10, 24, 0, 30)  # 20:30 New York
    assert C.next_send_time(at, NY, (8, 21)) == at
    assert C.next_send_time(utc(2026, 10, 24, 1, 0), NY, (8, 21)) == utc(2026, 10, 24, 12, 0)  # 21:00 is outside


def test_window_boundaries_are_exact():
    assert C.next_send_time(utc(2026, 10, 23, 12, 0), NY, (8, 20)) == utc(2026, 10, 23, 12, 0)   # 08:00 sharp
    assert C.next_send_time(utc(2026, 10, 23, 11, 59), NY, (8, 20)) == utc(2026, 10, 23, 12, 0)  # 07:59
    assert C.next_send_time(utc(2026, 10, 23, 23, 59), NY, (8, 20)) == utc(2026, 10, 23, 23, 59)  # 19:59
    assert C.next_send_time(utc(2026, 10, 24, 0, 0), NY, (8, 20)) == utc(2026, 10, 24, 12, 0)    # 20:00


def test_unknown_time_zone_must_fit_every_us_zone():
    t = C.next_send_time(utc(2026, 10, 23, 12, 0), None, (8, 20))  # 8:00 New York but 2:00 Honolulu
    for z in C.US_ZONES:
        local = t.astimezone(C.ZoneInfo(z))
        assert 8 <= local.hour < 20, z


def test_fourth_message_in_24_hours_waits():
    now = utc(2026, 10, 23, 15, 0)
    sent = [now - timedelta(hours=h) for h in (20, 5, 1)]
    t = C.next_send_time(now, NY, (8, 20), sent)
    assert t >= sent[0] + timedelta(hours=24)
    assert len([s for s in sent if t - timedelta(hours=24) < s <= t]) < 3
    assert C.next_send_time(now, NY, (8, 20), sent[1:]) == now  # two in 24 h: third goes now


def test_gate_blocks_opted_out_recipients():
    g = C.gate("hi", "sms", C.Recipient(NY, "FL", opted_out=True), "Cool Air", "FL", utc(2026, 10, 1, 12), utc(2026, 10, 23, 15))
    assert not g.ok and g.text == "" and g.send_at is None


def test_old_estimate_is_not_revived_by_sms_without_consent():
    sent = utc(2026, 7, 1, 12)
    now = sent + timedelta(days=91)
    assert not C.gate("hi", "sms", C.Recipient(NY, "FL"), "Cool Air", "FL", sent, now).ok
    assert C.gate("hi", "email", C.Recipient(NY, "FL"), "Cool Air", "FL", sent, now).ok
    assert C.gate("hi", "sms", C.Recipient(NY, "FL", written_consent=True), "Cool Air", "FL", sent, now).ok
    assert C.gate("hi", "sms", C.Recipient(NY, "FL"), "Cool Air", "FL", sent, sent + timedelta(days=90)).ok


def test_every_sent_message_discloses_the_assistant():
    g = C.gate("Can do Tuesday 9am.", "sms", C.Recipient(NY, "FL"), "Cool Air", "FL", utc(2026, 10, 20, 12), utc(2026, 10, 23, 15))
    assert g.ok and g.text.startswith("[Cool Air assistant, not a person]") and "Reply STOP" in g.text


def test_gate_delays_into_the_window_and_says_so():
    g = C.gate("hi", "sms", C.Recipient(NY, "FL"), "Cool Air", "FL", utc(2026, 10, 20, 12), utc(2026, 10, 24, 2, 0))
    assert g.ok and g.send_at == utc(2026, 10, 24, 12, 0) and g.reasons
