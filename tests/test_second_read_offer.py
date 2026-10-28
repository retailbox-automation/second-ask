"""offer(), match() and money() on hand-made cards: every rule of V4-DESIGN §3 and their combinations."""
import pytest

from second_ask.tournament.buyers import Card
from second_read import offer as O
from second_read.contract import Facts

TODAY = "2026-11-03"  # Tuesday; T0 is Monday 2026-11-02


def card(items=None, quoted=9.0, open_days=(2.0, 3.0, 9.0, 12.0), max_discount=0.10):
    items = items or [["Main job", 1000, False], ["Opt A", 200, True], ["Opt B", 100, True]]
    return Card(id="T001", type="price", archetype="generic", style=[], casino_ref="", trade="HVAC", title="Main job",
                items=items, max_discount=max_discount, channel="sms", reads_channel="both", quoted_slot_day=quoted,
                open_slot_days=list(open_days), walkaway=0.0, anchor=None, anchor_kind=None, patience=2,
                first_reply_hours=1.0, clarity="clear")


C = card()
# list 1300, floor 1170; menu on the full package: 1300, 1260 (3 %), 1235 (5 %), 1170 (floor)
# core (no optional items): list 1000, floor 900; menu 1000, 970, 950, 900. Drop A only: list 1100, floor 990.
# slots after TODAY: Nov 4, Nov 5, Nov 11, Nov 14; quoted Wed Nov 11, its week starts Mon Nov 9


def off(**kw):
    return O.offer(Facts(**kw), C, TODAY)


def test_card_arithmetic_is_what_the_tests_assume():
    e = C.estimate()
    assert (e.list_price(), e.floor(), e.floor(frozenset({"Opt A", "Opt B"})), e.floor(frozenset({"Opt A"}))) == (1300, 1170, 900, 990)
    assert [s.start.date().isoformat() for s in O.open_slots(C, TODAY)] == ["2026-11-04", "2026-11-05", "2026-11-11", "2026-11-14"]


def test_nothing_read_is_the_standing_estimate_answered():
    assert off() == {"stop": False, "dropped": [], "price": 1300.0, "timing": {"kind": "answer"}}


def test_stop_overrides_everything():
    o = off(stop=True, ceiling_usd=900, drop_items=("Opt A",), ready=True, wait_weeks=2)
    assert o == {"stop": True, "dropped": [], "price": None, "timing": None}


@pytest.mark.parametrize("ceiling,price", [(5000, 1300), (1300, 1300), (1299, 1260), (1260, 1260), (1250, 1235),
                                           (1235.004, 1235), (1200, 1170), (1170, 1170), (1000, 1170), (1, 1170)])
def test_price_from_ceiling_on_the_full_package(ceiling, price):
    assert off(ceiling_usd=ceiling)["price"] == price


@pytest.mark.parametrize("ceiling,price", [(None, 1000), (2000, 1000), (960, 950), (930, 900), (800, 900)])
def test_price_from_ceiling_on_the_core_package(ceiling, price):
    o = off(ceiling_usd=ceiling, drop_items=("Opt B", "Opt A"))
    assert (o["dropped"], o["price"]) == (["Opt A", "Opt B"], price)


def test_partial_drop_has_only_its_list_price_on_the_initial_menu():
    assert off(drop_items=("Opt A",))["price"] == 1100
    assert off(drop_items=("Opt A",), ceiling_usd=1050)["price"] == 990  # no menu price fits: the package floor


def test_non_optional_and_unknown_items_are_not_dropped():
    assert off(drop_items=("Main job", "Gold plating"))["dropped"] == []
    assert O.offer({"drop_items": ["opt a", "Main job"]}, C, TODAY)["dropped"] == ["Opt A"]  # dict path: contract parse


@pytest.mark.parametrize("weeks,days", [(1, 7), (2, 14), (3, 14), (4, 30), (5, 30), (10, 30)])
def test_wait_weeks_checkin_nearest_menu_option(weeks, days):
    assert off(wait_weeks=weeks)["timing"] == {"kind": "checkin", "days": days}


def test_wait_weeks_comes_before_need_by_ready_and_changes():
    o = off(wait_weeks=2, need_by="2026-11-05", ready=True, ceiling_usd=1200, drop_items=("Opt B",))
    # drop B: its list price 1200 is on the menu and fits the ceiling
    assert o == {"stop": False, "dropped": ["Opt B"], "price": 1200.0, "timing": {"kind": "checkin", "days": 14}}


def test_need_by_takes_the_earliest_open_slot_after_today():
    assert off(need_by="2026-11-20")["timing"] == {"kind": "slot", "day": "2026-11-04"}
    assert off(need_by="2026-11-01")["timing"] == {"kind": "slot", "day": "2026-11-04"}  # none before it: earliest
    assert O.offer(Facts(need_by="2026-11-20"), C, "2026-11-04")["timing"] == {"kind": "slot", "day": "2026-11-05"}


def test_need_by_comes_before_ready():
    assert off(need_by="2026-11-20", ready=True)["timing"]["day"] == "2026-11-04"


def test_ready_offers_the_earliest_slot_from_the_quoted_week():
    assert off(ready=True)["timing"] == {"kind": "slot", "day": "2026-11-11"}
    assert off(ready=True, decider="my wife")["timing"] == {"kind": "slot", "day": "2026-11-11"}


def test_ready_counts_the_quoted_week_from_its_monday():
    c = card(quoted=10.0, open_days=(2.0, 3.0, 7.0, 10.0, 12.0))  # quoted Thu Nov 12; Mon Nov 9 is open
    assert O.offer(Facts(ready=True), c, TODAY)["timing"] == {"kind": "slot", "day": "2026-11-09"}
    assert O.offer(Facts(ready=True), c, "2026-11-10")["timing"] == {"kind": "slot", "day": "2026-11-12"}


def test_ready_falls_back_to_the_earliest_slot_when_none_in_or_after_the_quoted_week():
    c = card(quoted=9.0, open_days=(2.0, 3.0))
    assert O.offer(Facts(ready=True), c, TODAY)["timing"] == {"kind": "slot", "day": "2026-11-04"}


def test_price_or_drop_change_offers_to_book_like_ready():
    assert off(ceiling_usd=1250)["timing"] == {"kind": "slot", "day": "2026-11-11"}
    assert off(drop_items=("Opt B",))["timing"] == {"kind": "slot", "day": "2026-11-11"}
    assert off(ceiling_usd=1250, decider="the HOA")["timing"]["kind"] == "slot"


def test_ceiling_at_or_above_list_is_not_a_change():
    assert off(ceiling_usd=1400)["timing"] == {"kind": "answer"}


def test_decider_answers_and_waits_without_a_discount():
    assert off(decider="my landlord") == {"stop": False, "dropped": [], "price": 1300.0, "timing": {"kind": "answer"}}


def test_no_open_slot_after_today_falls_through_to_answer():
    assert O.offer(Facts(ready=True), C, "2026-11-20")["timing"] == {"kind": "answer"}
    assert O.offer(Facts(need_by="2026-11-25"), C, "2026-11-20")["timing"] == {"kind": "answer"}


def test_today_itself_is_not_bookable():
    assert O.offer(Facts(need_by="2026-11-30"), C, "2026-11-04")["timing"]["day"] == "2026-11-05"


def test_offer_is_json_serialisable_and_deterministic():
    import json
    o = off(ceiling_usd=1250, drop_items=("Opt B", "Opt A"), ready=True)
    assert json.loads(json.dumps(o)) == o == off(ceiling_usd=1250, drop_items=("Opt A", "Opt B"), ready=True)


def test_every_price_is_within_floor_and_list():
    e = C.estimate()
    for ceiling in (None, 1, 899, 950, 1100, 1234, 1299, 9999):
        for drop in ((), ("Opt A",), ("Opt B",), ("Opt A", "Opt B")):
            o = off(ceiling_usd=ceiling, drop_items=drop)
            pkg = frozenset(o["dropped"])
            assert e.floor(pkg) <= o["price"] <= e.list_price(pkg)


# ------------------------------------------------------------------ match

def test_match_rules():
    a = off(ceiling_usd=1250)
    assert O.match(a, dict(a))
    assert O.match(a, dict(a, price=1235.001))
    assert not O.match(a, dict(a, price=1235.02))
    assert not O.match(a, dict(a, timing={"kind": "answer"}))
    assert not O.match(a, dict(a, dropped=["Opt A"]))
    assert not O.match(a, None) and not O.match(None, None)
    assert O.match(off(stop=True), off(stop=True, ready=True))
    assert not O.match(off(stop=True), off())


# ------------------------------------------------------------------ money

def money(read, gold, today=TODAY):
    return O.money(C, O.offer(read, C, today) if read is not None else None, gold, today)


def test_money_perfect_read_books_with_its_margin():
    m = money(Facts(ceiling_usd=1250), Facts(ceiling_usd=1250))
    assert m["booked"] and m["gold_books"] and m["margin"] == m["gold_margin"] == 1235 - 715
    assert m["given_away"] == m["lost"] == m["gained"] == 0


def test_money_given_away_when_the_read_ceiling_is_too_low():
    m = money(Facts(ceiling_usd=1200), Facts(ceiling_usd=1250))
    assert m["booked"] and m["given_away"] == 65 and m["lost"] == 0


def test_money_lost_when_the_price_is_above_the_gold_ceiling():
    m = money(Facts(), Facts(ceiling_usd=1250))
    assert not m["booked"] and m["lost"] == 520


def test_money_needs_the_gold_drop_items_dropped():
    assert not money(Facts(), Facts(drop_items=("Opt A",)))["booked"]
    assert money(Facts(drop_items=("Opt A", "Opt B")), Facts(drop_items=("Opt A",)))["booked"]  # extra drop is fine


def test_money_need_by_needs_a_slot_in_time():
    assert money(Facts(need_by="2026-11-06"), Facts(need_by="2026-11-06"))["booked"]
    m = money(Facts(), Facts(need_by="2026-11-06"))
    assert not m["booked"] and m["gold_books"] and m["lost"] == 1300 - 715
    assert not money(Facts(ready=True), Facts(need_by="2026-11-06"))["booked"]  # Nov 11 is too late


def test_money_wait_needs_a_checkin_within_a_week():
    assert money(Facts(wait_weeks=2), Facts(wait_weeks=3))["booked"]  # 14 vs 21
    assert not money(Facts(), Facts(wait_weeks=3))["booked"]
    assert not money(Facts(ready=True), Facts(wait_weeks=2))["booked"]
    assert not money(Facts(wait_weeks=6), Facts(wait_weeks=6))["gold_books"]  # check-in 30 vs 42 days


def test_money_stop():
    m = money(Facts(), Facts(stop=True))
    assert not m["booked"] and m["message_after_stop"] and m["lost"] == 0
    m = money(Facts(stop=True), Facts(stop=True))
    assert not m["booked"] and not m["message_after_stop"]
    m = money(Facts(stop=True), Facts(ready=True))
    assert not m["booked"] and m["lost"] == 585


def test_money_reader_failure_sends_nothing():
    m = money(None, Facts(ready=True))
    assert not m["booked"] and m["lost"] == 585 and not m["message_after_stop"]
    assert not money(None, Facts(stop=True))["message_after_stop"]
