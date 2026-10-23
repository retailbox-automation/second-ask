import json, random
from dataclasses import replace

import pytest

from second_ask.menu import Offer
from second_ask.tournament import buyers as B, env as E, manifest as MF, scoring as S
from second_ask.tournament.policies import ALL, ConcessionHeuristic, FakeSimClient, ShopRule, make


@pytest.fixture(scope="module")
def cards():
    return B.generate(200)


def first(cards, typ, **kw):
    return next(c for c in cards if c.type == typ and all(getattr(c, k) == v for k, v in kw.items()))


def test_generation_is_deterministic_and_matches_the_mix(cards):
    again = B.generate(200)
    assert [c.__dict__ for c in cards] == [c.__dict__ for c in again]
    counts = {t: sum(c.type == t for c in cards) for t in B.TYPE_MIX}
    assert counts == {"price": 50, "timing_pause": 34, "timing_slot": 20, "family": 34, "channel": 30, "ready": 22, "opt_out": 10}
    assert B.generate(200, seed=1)[0].__dict__ != cards[0].__dict__


def test_cards_hold_their_invariants(cards):
    for c in cards:
        est = c.estimate()
        assert est.floor() <= est.list_price()
        if c.type == "price":
            assert c.anchor < c.walkaway < est.list_price()
        if c.type in ("timing_pause", "timing_slot", "channel", "ready"):
            assert c.walkaway >= est.list_price()
        if c.type == "channel":
            assert c.reads_channel != c.channel
        if c.type == "timing_slot":
            assert c.quoted_slot_day > c.need_by_days


def test_oracle_and_scoring_add_up(cards):
    c = first(cards, "price")
    orc, feasible = S.oracle(c)
    out = E.Outcome(c.id, c.type, c.trade, "x", 0, False, None, [], "lost", 1, 0.01, 2, 0.0, 0, 0, 0, 0)
    row = S.score(c, out)
    assert row["lost"] == (round(orc, 2) if feasible else 0.0) and row["margin"] == 0.0
    est = c.estimate()
    booked = replace(out, booked=True, price=min(est.list_price(), c.walkaway) if feasible else est.floor())
    row = S.score(c, booked)
    assert row["margin"] + row["given_away"] == pytest.approx(round(orc, 2), abs=0.02) or not feasible


def test_walkaway_and_floor_gates_count_synthetic_violations(cards):
    c = first(cards, "ready")
    est = c.estimate()
    above = S.score(c, E.Outcome(c.id, c.type, c.trade, "x", 0, True, c.walkaway + 100, [], "booked", 1, 0, 0, 0, 0, 0, 0, 0))
    below = S.score(c, E.Outcome(c.id, c.type, c.trade, "x", 0, True, est.floor() - 50, [], "booked", 1, 0, 0, 0, 0, 0, 0, 0))
    gates = S.sanity_gates(cards, [above, below])
    assert gates["bookings_above_walkaway"] == 1 and gates["bookings_below_floor"] == 1  # the gates can light


def test_buyer_never_books_above_walkaway_and_needs_are_respected(cards):
    rng = random.Random(0)
    c = first(cards, "price")
    est = c.estimate()
    from second_ask.menu import build_menu, initial_offer
    menu = {m.id: m for m in build_menu(est, initial_offer(est), c.open_slots())}
    st = E.BuyerState(c.patience, "clear", True)
    offer = Offer(frozenset(), est.list_price(), est.default_slot, est.channel)
    assert E.react(c, st, offer, menu["offer_slot"], 0.5, True, rng).kind != "book"  # list > walkaway
    pause = first(cards, "timing_pause")
    est2 = pause.estimate()
    m2 = {m.id: m for m in build_menu(est2, initial_offer(est2), pause.open_slots())}
    st2 = E.BuyerState(pause.patience, "clear", True)
    o2 = Offer(frozenset(), est2.list_price(), est2.default_slot, est2.channel)
    assert E.react(pause, st2, o2, m2["concede_pkg_floor"], pause.ready_after_days - 1, True, rng).kind != "book"
    assert E.react(pause, st2, o2, m2["checkin_3"], pause.ready_after_days + 0.1, False, rng).kind == "book"
    assert E.react(pause, st2, o2, m2["checkin_3"], pause.hire_by_days + 0.1, False, rng).kind == "lost"


def test_channel_buyer_does_not_see_the_wrong_channel_until_they_notice(cards):
    rng = random.Random(0)
    c = first(cards, "channel")
    est = c.estimate()
    from second_ask.menu import build_menu, initial_offer
    menu = {m.id: m for m in build_menu(est, initial_offer(est), c.open_slots())}
    st = E.BuyerState(c.patience, "clear", False)
    offer = Offer(frozenset(), est.list_price(), est.default_slot, est.channel)
    early = min(c.notice_day, c.hire_by_days) - 0.5
    assert E.react(c, st, offer, menu["reminder"], early, False, rng).kind == "silent"
    resend = menu[f"resend_{c.reads_channel}"]
    assert E.react(c, st, resend.offer(offer), resend, early, False, rng).kind == "book"


def test_family_decides_on_its_own_clock_and_a_discount_only_lowers_the_price(cards):
    c = next(x for x in cards if x.type == "family" and x.walkaway >= x.estimate().list_price())
    rule = E.run_one(c, ShopRule(), E.TemplateWriter(), 0)  # discounts to the floor at the first message
    first_msg = c.first_reply_hours / 24
    assert rule.booked and rule.price == c.estimate().floor()
    assert rule.transcript[-1]["day"] == pytest.approx(first_msg + c.family_delay_days, abs=1e-3)
    st = E.BuyerState(c.patience, "clear", True)
    from second_ask.menu import build_menu, initial_offer
    est = c.estimate()
    menu = {m.id: m for m in build_menu(est, initial_offer(est), c.open_slots())}
    offer = Offer(frozenset(), est.list_price(), est.default_slot, est.channel)
    assert E.react(c, st, offer, menu["offer_slot"], first_msg + 0.1, True, random.Random(0)).kind == "silent"


def test_check_in_makes_a_waiting_buyer_wait(cards):
    rng = random.Random(0)
    from second_ask.menu import build_menu, initial_offer
    for typ in ("price", "timing_pause"):
        c = first(cards, typ)
        est = c.estimate()
        menu = {m.id: m for m in build_menu(est, initial_offer(est), c.open_slots())}
        st = E.BuyerState(c.patience, "clear", True)
        offer = Offer(frozenset(), est.list_price(), est.default_slot, est.channel)
        assert E.react(c, st, offer, menu["checkin_3"], c.first_reply_hours / 24 + 0.01, True, rng).kind == "silent"


@pytest.mark.parametrize("name", ALL)
def test_every_policy_runs_end_to_end_on_the_fake_client(cards, name):
    for c in cards[:40]:
        client = FakeSimClient(seed=1)
        client.card = c
        out = E.run_one(c, make(name, client), E.TemplateWriter(), 0)
        row = S.score(c, out)
        assert not row["above_walkaway"] and not row["below_floor"]
        if c.type == "opt_out" and c.opt_out_style == "keyword":
            assert out.violations == 0 and out.moves == 0
        days = [e["day"] for e in out.transcript]
        assert days == sorted(days)


def test_rule_policies_ignore_message_text(cards):
    """Why rule policies run with K = 1: their outcome cannot depend on the buyer's words."""
    class Shout(E.TemplateWriter):
        def write(self, *a):
            text, s, f = super().write(*a)
            return text.upper() + " !!!", s, f
    for pol in (ShopRule(), ConcessionHeuristic()):
        for c in cards[:60]:
            a, b = E.run_one(c, pol, E.TemplateWriter(), 0), E.run_one(c, pol, Shout(), 1)
            assert (a.booked, a.price, a.moves, a.end) == (b.booked, b.price, b.moves, b.end)


def test_paired_bootstrap_and_losing_cells():
    rows = []
    for i in range(30):
        rows.append({"buyer": f"b{i}", "policy": "shop_rule", "net": 100.0, "type": "price", "trade": "HVAC"})
        rows.append({"buyer": f"b{i}", "policy": "routed", "net": 110.0 if i % 3 else 90.0, "type": "price", "trade": "HVAC"})
        rows.append({"buyer": f"b{i}", "policy": "super_only", "net": 120.0, "type": "price", "trade": "HVAC"})
    ci = S.paired_bootstrap(rows, "routed")
    assert ci["lo"] <= ci["point"] <= ci["hi"] and ci["buyers"] == 30
    cells = S.losing_cells(rows)
    assert cells and cells[0]["best"] == "super_only"


def test_aggregate_reports_bands_over_repeats():
    rows = [{"policy": "p", "repeat": k, "buyer": "b", "type": "price", "booked": k == 0, "margin": 10.0 * (k == 0),
             "given_away": 0.0, "lost": 5.0 * (k != 0), "model_usd": 0.01, "net": 10.0 * (k == 0) - 0.01,
             "violations": 0, "unsure": 0} for k in range(3)]
    a = S.aggregate(rows)["p"]
    assert a["K"] == 3 and a["bookings"] == {"mean": 1 / 3, "min": 0, "max": 1}


def test_manifest_hash_is_canonical_and_detects_changes(tmp_path):
    m = {"b": 1, "a": [1, 2, {"z": "x"}]}
    assert MF.digest(m) == MF.digest(json.loads(json.dumps(m, indent=3)))
    assert MF.digest(m) != MF.digest({"b": 2, "a": [1, 2, {"z": "x"}]})
    p = tmp_path / "MANIFEST.json"
    h = MF.write(m, p)
    assert (tmp_path / "MANIFEST.sha256").read_text().startswith(h)
