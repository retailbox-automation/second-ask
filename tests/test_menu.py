import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from second_ask import menu as M

T0 = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)


def estimate(max_discount=0.07, optional=(("Surge protector", 180.0),)):
    items = (M.LineItem("Capacitor and contactor", 560.0, 300.0),) + tuple(
        M.LineItem(n, p, round(p * 0.55, 2), optional=True) for n, p in optional)
    return M.Estimate("E-1", "Cool Air", "HVAC", "AC repair", items, max_discount, T0, "email",
                      M.Slot("s-def", T0 + timedelta(days=7)))


SLOTS = [M.Slot(f"s{i}", T0 + timedelta(days=i)) for i in (1, 2, 9)]


def test_floor_is_list_minus_max_discount_rounded_up_and_never_below_cost():
    e = estimate()
    assert e.list_price() == 740 and e.floor() == 690  # 740 * 0.93 = 688.2 -> 690
    assert e.floor(frozenset({"Surge protector"})) == 525  # 560 * 0.93 = 520.8 -> 525
    cheap = estimate(max_discount=0.9)
    assert cheap.floor() >= cheap.cost()


def test_menu_has_the_basic_moves_and_ids_are_unique():
    e = estimate()
    ids = [m.id for m in M.build_menu(e, M.initial_offer(e), SLOTS)]
    assert len(ids) == len(set(ids))
    for mid in ("answer", "ask_why", "reminder", "checkin_7", "offer_slot", "earlier_slot_1", "earlier_slot_2",
                "resend_sms", "drop_surge_protector", "concede_pkg_3", "concede_pkg_5", "concede_pkg_floor",
                "concede_core_3", "concede_core_floor", "close_out"):
        assert mid in ids, mid
    assert "resend_email" not in ids  # the estimate already went by email


def test_earlier_slots_are_only_slots_before_the_quoted_one():
    e = estimate()
    menu = {m.id: m for m in M.build_menu(e, M.initial_offer(e), SLOTS)}
    assert menu["earlier_slot_1"].slot.id == "s1" and menu["earlier_slot_2"].slot.id == "s2"
    assert all(m.slot.id != "s9" for m in menu.values() if m.slot)


def test_drop_keeps_a_concession_already_on_the_table():
    e = estimate()
    offer = M.Offer(frozenset(), 700.0, e.default_slot, "email")
    drop = next(m for m in M.build_menu(e, offer, SLOTS) if m.kind == "drop")
    assert drop.price == 525 and drop.needs_owner  # 700 - 180 = 520 is below the 525 floor of the smaller package


def test_concessions_only_go_down_from_the_standing_price():
    e = estimate()
    offer = M.Offer(frozenset(), 700.0, e.default_slot, "email")
    for m in M.build_menu(e, offer, SLOTS):
        if m.kind == "concede" and m.dropped == frozenset():
            assert m.price < 700


@pytest.mark.parametrize("seed", range(200))
def test_no_menu_move_is_ever_below_its_floor(seed):
    rng = random.Random(seed)
    opt = tuple((f"Option {k}", float(rng.randrange(50, 900))) for k in range(rng.randint(0, 2)))
    e = estimate(max_discount=rng.choice([0.0, 0.05, 0.07, 0.1, 0.3]), optional=opt)
    offer = M.initial_offer(e)
    for _ in range(4):  # walk a few concessions deep
        menu = M.build_menu(e, offer, SLOTS, max_checkin_days=rng.choice([None, 10]))
        for m in menu:
            assert e.floor(m.dropped) <= m.price <= e.list_price(m.dropped)
            assert m.needs_owner == (m.price < e.list_price(m.dropped)) or m.kind not in ("concede", "drop")
        offer = rng.choice(menu).offer(offer)


def test_validate_rejects_a_hand_made_move_below_the_floor():
    e = estimate()
    bad = M.Move("x", "concede", "x", frozenset(), 600.0, None, "sms", needs_owner=True)
    with pytest.raises(M.FloorViolation):
        M.validate(bad, e)
    with pytest.raises(M.FloorViolation):
        M.validate(replace(bad, price=800.0), e)  # above list is refused too


def test_check_message_flags_amounts_and_percents_not_in_the_move():
    e = estimate()
    offer = M.initial_offer(e)
    move = next(m for m in M.build_menu(e, offer, SLOTS) if m.id == "concede_pkg_5")
    assert move.price == 705
    assert M.check_message("We can do $705 instead of $740.", move, e, offer) == []
    assert M.check_message("We can do it for 5% off: $705.", move, e, offer) == []
    assert M.check_message("Best I can do is $650.", move, e, offer)
    assert M.check_message("Take 15% off!", move, e, offer)
    assert M.check_message("Only 650 dollars.", move, e, offer)
    assert M.check_message("", move, e, offer) == ["empty message"]
    assert M.check_message("x" * 481, move, e, offer)


def test_every_move_kind_has_a_template():
    e = estimate()
    offer = M.initial_offer(e)
    for m in M.build_menu(e, offer, SLOTS):
        text = M.template_message(m, e, offer)
        assert text and M.check_message(text, m, e, offer) == []


def test_two_optional_items_also_get_a_drop_all_move():
    e = estimate(optional=(("Smart thermostat", 380.0), ("UV air purifier", 650.0)))
    menu = {m.id: m for m in M.build_menu(e, M.initial_offer(e), SLOTS)}
    assert {"drop_smart_thermostat", "drop_uv_air_purifier", "drop_all_optional"} <= set(menu)
    assert menu["drop_all_optional"].price == 560 and not menu["drop_all_optional"].needs_owner


def test_customer_amounts_may_be_quoted_back_only_at_or_above_the_move_price():
    e = estimate()
    offer = M.initial_offer(e)
    drop = next(m for m in M.build_menu(e, offer, SLOTS) if m.id == "drop_surge_protector")  # $560
    text = "That beats the $650 quote: the repair alone is $560."
    assert M.check_message(text, drop, e, offer) and M.check_message(text, drop, e, offer, [650.0]) == []
    assert M.check_message("Sure, $500 works.", drop, e, offer, [500.0])  # below the move's price: refused
