import json, random
from dataclasses import replace

import pytest

from fakes import FakeClient, call, label, raw
from second_ask import agent as A, compliance as C, models as M
from second_ask.menu import Offer, build_menu, initial_offer
from second_ask.trace import Step
from second_ask.tournament import buyers as B, env as E, manifest as MF, scoring as S
from second_ask.tournament.policies import (ALL, ConcessionHeuristic, FakeSimClient, LLMPolicy, ShopRule, SmartRules,
                                            _stop_decision, make)


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


class Neutral(E.TemplateWriter):
    """Rewrites every buyer message into words with no opt-out keyword and no number."""
    def write(self, *a):
        _, s, f = super().write(*a)
        return "Okay, noted. Thanks for the note.", s, f


def test_text_blind_rules_do_not_depend_on_wording(cards):
    """Why shop_rule and concession_heuristic run once (K = 1): on all 200 buyers their outcome, violations included,
    is the same whatever words the buyer model writes (MF6). Opt-out buyers' words come from code."""
    for pol in (ShopRule(), ConcessionHeuristic()):
        for c in cards:
            a, b = E.run_one(c, pol, E.TemplateWriter(), 0), E.run_one(c, pol, Neutral(), 1)
            assert (a.booked, a.price, a.moves, a.end, a.violations) == (b.booked, b.price, b.moves, b.end, b.violations), c.id


def test_opt_out_buyers_words_come_from_the_published_lists(cards):
    class Refuse(E.TemplateWriter):
        def write(self, card, st, spec, *a):
            assert card.type != "opt_out", "the writer must not word an opt-out"
            return super().write(card, st, spec, *a)
    outs = [E.run_one(c, ShopRule(), Refuse(), 0) for c in cards if c.type == "opt_out"]
    phrase = [o for o, c in zip(outs, [c for c in cards if c.type == "opt_out"]) if c.opt_out_style == "phrase"]
    assert phrase and all(o.transcript[0]["text"] in E.OPT_OUT_PHRASES for o in phrase)
    assert all(o.violations == 0 and o.moves == 0 for o in outs)


def test_every_published_opt_out_phrase_is_caught_by_the_code_layer():
    assert all(C.opt_out_status(t) == "opt_out" for t in E.OPT_OUT_PHRASES + E.OPT_OUT_KEYWORDS)


def test_paired_bootstrap_and_losing_cells():
    rows = []
    for i in range(30):
        rows.append({"buyer": f"b{i}", "policy": "shop_rule", "net": 100.0, "type": "price", "trade": "HVAC"})
        rows.append({"buyer": f"b{i}", "policy": "routed", "net": 110.0 if i % 3 else 90.0, "type": "price", "trade": "HVAC"})
        rows.append({"buyer": f"b{i}", "policy": "super_only", "net": 120.0, "type": "price", "trade": "HVAC"})
    ci = S.paired_bootstrap(rows, "routed", "shop_rule")
    assert ci["lo"] <= ci["point"] <= ci["hi"] and ci["buyers"] == 30
    assert S.paired_bootstrap(rows, "routed") is None  # the default baseline is smart_rules (MF7), absent here
    cells = S.losing_cells(rows)
    assert cells and cells[0]["best"] == "super_only" and cells[0]["n"] == 30 and cells[0]["clear_loss"]


def _rows(policy, buyers, repeat, margin=100.0, **kw):
    return [{"policy": policy, "repeat": repeat, "buyer": b, "type": "price", "trade": "HVAC", "booked": True,
             "margin": margin, "given_away": 0.0, "lost": 0.0, "model_usd": 0.0, "net": margin, "violations": 0,
             "unsure": 0, **kw} for b in buyers]


def test_aggregate_with_unequal_repeats_sums_each_buyers_mean():
    """MF1: repeats on 200/100/100 buyers. Every buyer earns $100 in every policy, so every total must be $20,000 and
    the paired difference 0; the band uses the same 100 buyers for every policy."""
    all_, sub = [f"b{i:03d}" for i in range(200)], [f"b{i:03d}" for i in range(0, 200, 2)]
    rows = _rows("shop_rule", all_, 0) + _rows("routed", all_, 0) + _rows("routed", sub, 1) + _rows("routed", sub, 2)
    agg = S.aggregate(rows)
    for p in ("shop_rule", "routed"):
        assert agg[p]["margin"] == pytest.approx(20000) and agg[p]["bookings"] == pytest.approx(200) and agg[p]["n"] == 200
        assert agg[p]["band"]["n"] == 100 and agg[p]["band"]["margin"] == {"mean": 10000, "min": 10000, "max": 10000}
    assert agg["routed"]["K"] == 3 and agg["routed"]["band"]["K"] == 3 and agg["shop_rule"]["band"]["K"] == 1
    assert S.paired_bootstrap(rows, "routed", "shop_rule")["point"] == pytest.approx(0)


def test_aggregate_averages_a_buyers_repeats_before_summing():
    rows = _rows("routed", ["a", "b"], 0) + _rows("routed", ["a"], 1, margin=0.0) + _rows("routed", ["a"], 2, margin=50.0)
    assert S.aggregate(rows)["routed"]["margin"] == pytest.approx(100 + 50)  # a: mean(100, 0, 50) = 50; b: 100


def test_losing_cells_count_each_buyer_once():
    """A buyer with three repeats must not weigh three times in its cell."""
    rows = []
    for i in range(12):
        b = f"b{i}"
        rows += _rows("smart_rules", [b], 0, margin=100.0)
        reps = 3 if i < 2 else 1
        for k in range(reps):  # two buyers repeated three times at $0, ten buyers once at $112
            rows += _rows("routed", [b], k, margin=0.0 if i < 2 else 112.0)
    # per buyer: routed mean = (2 x 0 + 10 x 112) / 12 = 93.3 < 100 -> a loss; per row it would be 1120 / 16 = 70
    cell = S.losing_cells(rows, "routed")[0]
    assert cell["policy_net"] == pytest.approx(93.33, abs=0.01) and cell["n"] == 12 and not cell["small"]


def test_label_default_rule():
    buyers = [f"b{i}" for i in range(50)]
    near = _rows("super_only", buyers, 0) + _rows("routed", buyers, 0, margin=99.0)
    assert S.label_default(near)["default_label_model"] == "Lightning"   # $50 worse in total: inside -$2,000
    far = _rows("super_only", buyers, 0) + _rows("routed", buyers, 0, margin=40.0)
    assert S.label_default(far)["default_label_model"] == "Super"        # $3,000 worse
    worse = _rows("super_only", buyers, 0) + _rows("routed", buyers, 0, violations=1)
    assert S.label_default(worse)["default_label_model"] == "Super"      # same $, more violations


def test_booking_rate_gate_counts_only_the_text_blind_rules(cards):
    rows = [S.score(c, E.run_one(c, ShopRule(), E.TemplateWriter(), 0)) for c in cards]
    rows += [dict(r, policy="routed", booked=False) for r in rows]
    gates = S.sanity_gates(cards, rows, ("shop_rule",))
    assert set(gates["booking_rate_in_30_80"]) == {"shop_rule"} and gates["invariants"] == list(S.INVARIANT_GATES)


def test_given_away_parts_add_up(cards):
    full = next(c for c in cards if c.type == "ready")
    est = full.estimate()
    out = E.Outcome(full.id, full.type, full.trade, "x", 0, True, est.floor(), [], "booked", 1, 0, 0, 0, 0, 0, 0, 0)
    row = S.score(full, out)
    assert row["given_to_full_payers"] == row["given_away"] > 0 and row["menu_gap"] == 0
    checked = 0
    for c in [c for c in cards if c.type == "price" and S.oracle(c)[1]]:
        est = c.estimate()
        fits = [(m.price, m.dropped) for d, lst, _, _ in S.packages(c)
                for m in build_menu(est, Offer(d, lst, est.default_slot, est.channel), [])
                if m.dropped == d and m.price <= c.walkaway]
        price, dropped = min(fits, key=lambda x: x[0])  # the deepest reachable concession: over-gives on purpose
        row = S.score(c, E.Outcome(c.id, c.type, c.trade, "x", 0, True, price, sorted(dropped), "booked", 1, 0, 0, 0,
                                   0, 0, 0, 0))
        checked += 1
        assert row["given_to_full_payers"] == 0
        assert row["given_away"] == pytest.approx(row["menu_gap"] + S.menu_oracle(c) - row["margin"], abs=0.02)
        assert row["menu_gap"] >= 0 and S.menu_oracle(c) <= S.oracle(c)[0] + 1e-9
    assert checked >= 20


def test_leak_check_compares_numbers_not_substrings(cards):
    c = replace(next(c for c in cards if c.type == "price"), walkaway=1000.0, anchor=900.0)
    est = c.estimate()
    assert not E.leaks_walkaway(c, est, "The other quote was 11,000 for the whole house.")
    assert E.leaks_walkaway(c, est, "I could go to $1,000 at most.") and E.leaks_walkaway(c, est, "maybe 1000 tops")
    seen = replace(c, walkaway=est.items[0].price)  # equal to a number on the card: quoting it is not a leak
    assert not E.leaks_walkaway(seen, est, f"The ${est.items[0].price:,.0f} part is the problem.")


class AskWhy:
    name, llm, next_check_days = "ask_why_always", False, 3

    def act(self, conv, now, hint):
        by = {m.id: m for m in build_menu(conv.estimate, conv.offer, conv.slots_after(now))}
        return _stop_decision(conv, now, by["ask_why"])


def test_time_only_moves_forward_whatever_the_policy(cards):
    """Acceptance probe: a policy that always asks why produced out-of-order transcripts (B100, B175)."""
    for c in cards:
        out = E.run_one(c, AskWhy(), E.TemplateWriter(), 0)
        days = [e["day"] for e in out.transcript]
        assert out.chronology_errors == 0 and days == sorted(days), c.id


def test_a_household_books_the_offer_it_had_when_it_decided(cards):
    """The shop's floor offer is decided at 23:10 but goes out at 08:00; the household decides at 01:24 and books the
    list price it had then, not the undelivered floor."""
    c = next(x for x in cards if x.type == "family" and x.walkaway >= x.estimate().list_price())
    c = replace(c, first_reply_hours=14.0, family_delay_days=0.1, tz="America/New_York", state="FL")
    out = E.run_one(c, ShopRule(), E.TemplateWriter(), 0)
    assert out.booked and out.price == c.estimate().list_price() and out.chronology_errors == 0


def test_integrity_counters():
    arm = LLMPolicy("routed", None).arm()
    steps = [Step("classify", M.LIGHTNING, served_model=M.LIGHTNING), Step("classify", M.SUPER),
             Step("choose", M.SUPER), Step("choose", M.ULTRA), Step("choose", M.SUPER, served_model=M.SUPER_VL),
             Step.platform_failure("choose", M.SUPER, "503"), Step("buyer", M.QWEN3_30B), Step("buyer", M.SUPER)]
    assert E.integrity(steps, arm, M.QWEN3_30B) == (3, 1, 1)  # off-arm: Ultra, served VL, buyer on Super


def test_routed_label_from_the_fallback_model_is_counted_not_hidden():
    bad = raw(content="not json")
    fc = FakeClient([bad, bad, label("price"), raw(tool_calls=[call("choose_move", {"move_id": "answer",
                     "message": "Here is what it covers.", "reason": "r"})])])
    pol = LLMPolicy("routed", fc)
    from test_agent import conv, NOW
    c = conv()
    pol.act(c, NOW, "objection")
    assert E.integrity(c.trace.steps, pol.arm(), M.QWEN3_30B) == (0, 1, 0)


@pytest.mark.parametrize("text,expect", [
    ("Another shop quoted me $640. Can you do better?", "drop_surge_protector"),
    ("Honestly the most I can do is $500.", "concede_core_floor"),
    ("The AC is out, I need it done by Wednesday, someone else can come sooner.", "earlier_slot_1"),
    ("We want it, but not for about 3 weeks.", "checkin_14"),
    ("Maybe next month.", "checkin_30"),
    ("My wife needs to look at it with me first.", "ask_why"),
    ("Looks good, when can you come?", "offer_slot"),
    ("My husband thinks the Surge protector is not needed.", "drop_surge_protector"),
    ("It is more than I expected.", "ask_why"),
])
def test_smart_rules_read_the_words(text, expect):
    from test_agent import conv, NOW
    c = conv(text)
    menu = build_menu(c.estimate, c.offer, c.slots_after(NOW))
    move = SmartRules.choose(c, menu)
    assert move.id == expect and move in menu


def test_smart_rules_silence_and_follow_ups():
    from test_agent import conv, NOW
    c = conv(None)  # no reply yet
    c.views = 0
    menu = build_menu(c.estimate, c.offer, c.slots_after(NOW))
    assert SmartRules.choose(c, menu).id == "resend_sms"   # never opened: the other channel
    c.views = 2
    assert SmartRules.choose(c, menu).id == "ask_why"      # opened and silent
    c.events.append(A.Event(NOW, "shop", "email", "No rush.", "checkin_7"))
    assert SmartRules.choose(c, menu).id == "offer_slot"   # the promised check-back comes with a booking offer
    c2 = conv("It is more than I expected.")
    c2.events.append(A.Event(NOW, "shop", "email", "What is holding you back?", "ask_why"))
    c2.events.append(A.Event(NOW, "customer", "email", "Still more than I hoped."))
    m2 = build_menu(c2.estimate, c2.offer, c2.slots_after(NOW))
    assert SmartRules.choose(c2, m2).id == "concede_pkg_3"  # after asking: the smallest concession


def test_smart_rules_ignore_the_coarse_hint(cards):
    for c in cards[:40]:
        a = E.run_one(c, SmartRules(), E.TemplateWriter(), 0)
        class Lie(SmartRules):
            def act(self, conv, now, hint):
                return super().act(conv, now, "ready" if hint != "ready" else "silence")
        b = E.run_one(c, Lie(), E.TemplateWriter(), 0)
        assert (a.booked, a.price, a.moves, a.end) == (b.booked, b.price, b.moves, b.end)


def test_smart_rules_win_what_the_old_rules_structurally_could_not(cards):
    out = {p: [E.run_one(c, make(p), E.TemplateWriter(), 0) for c in cards] for p in ("shop_rule", "smart_rules")}
    booked = lambda p, t: sum(o.booked for o in out[p] if o.type == t)  # noqa: E731
    assert booked("shop_rule", "timing_slot") == 0 < booked("smart_rules", "timing_slot")
    assert booked("smart_rules", "channel") >= booked("shop_rule", "channel")
    assert all(not S.score(c, o)["below_floor"] for c, o in zip(cards, out["smart_rules"]))


def test_home_cases_hold_only_seed_parameters():
    """MF9: narratives are not in the hashed seed file; the generator uses only these fields."""
    data = json.loads((MF.ROOT / "seeds" / "home_cases.json").read_text())
    assert list(data) == ["cases"] and all(set(c) == {"id", "objection", "trade", "seeds"} for c in data["cases"])


def test_buyers_json_is_what_the_generator_makes(tmp_path):
    B.save(B.generate(), tmp_path / "b.json")
    assert MF.sha256_file(tmp_path / "b.json") == MF.sha256_file(MF.ROOT / "tournament" / "buyers.json")


def test_manifest_hash_is_canonical_and_detects_changes(tmp_path):
    m = {"b": 1, "a": [1, 2, {"z": "x"}]}
    assert MF.digest(m) == MF.digest(json.loads(json.dumps(m, indent=3)))
    assert MF.digest(m) != MF.digest({"b": 2, "a": [1, 2, {"z": "x"}]})
    p = tmp_path / "MANIFEST.json"
    h = MF.write(m, p)
    assert (tmp_path / "MANIFEST.sha256").read_text().startswith(h)
