"""v2 wording-robustness test: the writer gets the same facts, everything but the writer is the v1 code, the ceilings and
the analysis are as pre-registered."""
import json, random, re, sys
from pathlib import Path

import pytest

from fakes import FakeClient, raw
from second_ask import models as M, tf_client as tf
from second_ask.agent import Conversation, Event
from second_ask.compliance import Recipient
from second_ask.menu import amounts_in
from second_ask.trace import Trace
from second_ask.tournament import buyers as B, env as E, manifest as MF, policies as P, run as RUN, runner as R

sys.path.insert(0, str(MF.ROOT / "tools"))
import v2_analysis as V  # noqa: E402

V1 = json.loads((MF.ROOT / "tournament" / "MANIFEST.json").read_text())
WEEKDAYS = r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
ACT_OF = [  # instruction sentence -> the intent act it must map to
    (r"^Say sorry, you only just saw this", "saw_it_only_now"), (r"^Say you talked it over with", "household_view"),
    (r"^Say it is more than you expected|^Say it is still more than you hoped|^Say the total is more than you can do|"
     r"^Say it is still more than you can do", "cost_concern"),
    (r"^Say you will think about it and get back", "will_decide_later"),
    (r"^Say you want to do it, but not for about", "wants_it_later"), (r"^Say you are still not ready", "still_not_ready"),
    (r"^Say the timing does not really work|^Say the timing is still the problem", "schedule_problem"),
    (r"^Say their quoted slot|^Say you really need it done by", "needs_it_sooner"),
    (r"^Say it is still more than you want to spend", "household_view"),
    (r"^Say you need to think it over|^Say you are still thinking it over", "needs_to_think"),
    (r"needs to look at it with you before you decide", "household_must_review"),
    (r"^Say you are still talking it over", "household_still_discussing"),
    (r"^Say it looks good and ask when|^Ask again when they can come", "go_ahead"), (r"^Say you are still thinking\.$", "still_thinking")]


@pytest.fixture(scope="module")
def cards():
    return B.generate(200)


def facts_of_instruction(card, text):
    act = next(a for pat, a in ACT_OF if re.search(pat, text))
    weeks = re.search(r"about (\d+) week", text)
    return {"act": act, "amounts": set(amounts_in(text)), "weeks": int(weeks.group(1)) if weeks else None,
            "weekdays": set(re.findall(WEEKDAYS, text)), "date": set(re.findall(r"[A-Z][a-z]{2} \d{2}", text)),
            "who": card.family_who if card.family_who and card.family_who in text else None,
            "item": next((n for n, p, o in card.items if o and f"the {n} is not needed" in text), None),
            "channel": card.reads_channel if f"you mostly use {card.reads_channel}" in text else None,
            "reason": next((c for r, c in {**E.PAUSE_REASON_CODES, **{v: E.SLOT_REASON_CODES[k] for k, v in E.SLOT_REASONS.items()}}.items()
                            if f"because {r}" in text), None),
            "no_figure": "Do not give a number" in text or "No number" in text,
            "no_why": "Do not say why" in text, "basis": card.anchor_kind if card.anchor_kind and f"your {card.anchor_kind}" in text else None}


def facts_of_intent(d):
    days = " ".join(str(d.get(k) or "") for k in ("quoted_visit", "done_by"))
    return {"act": d["act"], "amounts": {float(d["could_pay"])} if d.get("could_pay") else set(),
            "weeks": d.get("wait_weeks_about"), "weekdays": set(re.findall(WEEKDAYS, days)),
            "date": set(re.findall(r"[A-Z][a-z]{2} \d{2}", days)), "who": d.get("discussed_with") or d.get("who"),
            "item": d.get("unnecessary_item"), "channel": d.get("usually_checks"), "reason": d.get("because"),
            "no_figure": d.get("give_a_figure") is False, "no_why": d.get("give_reason") is False or d.get("give_reason_or_time") is False,
            "basis": d.get("figure_from")}


def situations(cards):
    for c in cards:
        if c.type == "opt_out":
            continue
        specs = ["first", "again"] + (["missed"] if c.type == "channel" else []) + (["family_no"] if c.type == "family" else [])
        for spec in specs:
            for clarity in ("clear", "vague"):
                for decided in ([False, True] if c.type == "family" else [False]):
                    for dt in (0.0, 9.5):
                        yield c, E.BuyerState(c.patience, clarity, True, decided=decided), spec, c.first_reply_hours / 24 + dt


def test_intent_carries_exactly_the_facts_of_instruction(cards):
    n = 0
    for c, st, spec, t in situations(cards):
        text = E.instruction(c, st, spec, t, random.Random(0))
        fi, fd = facts_of_instruction(c, text), facts_of_intent(E.intent(c, st, spec, t))
        assert fi == fd, (c.id, spec, st.clarity, st.decided, text, E.intent(c, st, spec, t))
        n += 1
    assert n > 1500


V2_CHANGED = set(MF.V2_CHANGED)


def test_everything_but_the_writer_hookup_is_the_v1_code():
    """Hash test: every source file has the sha256 it has in manifest 1c231403, except the three v2 files."""
    now = MF.source_hashes()
    assert set(now) == set(V1["code"]), "files added or removed under second_ask/"
    changed = {f for f in now if now[f] != V1["code"][f]}
    assert changed <= V2_CHANGED | set(MF.V3_CHANGED), changed - V2_CHANGED  # v3 changed policies.py later (test_v3.py)
    for f in ("second_ask/agent.py", "second_ask/chooser.py", "second_ask/menu.py",
              "second_ask/compliance.py", "second_ask/tournament/scoring.py", "second_ask/tournament/runner.py",
              "second_ask/tf_client.py", "second_ask/tournament/buyers.py", "second_ask/classifier.py"):
        assert now[f] == V1["code"][f], f
    assert MF.sha256_file(MF.ROOT / "tournament" / "buyers.json") == V1["buyers"]["sha256"]


@pytest.fixture(scope="module")
def m2():
    return json.loads(MF.canonical(MF.build(V1["buyers"]["file"], V1["buyers"]["n"], V1["repeats"]["k"],
                                            V1["repeats"]["subsample"], MF.V1_BUYER_MODEL, None, {"projected_usd": 1.0},
                                            v2=True)))


def test_v2_manifest_ceilings_policies_and_writer(m2):
    assert m2["test"] == MF.V2_TEST and "ultra_only" not in m2["run_policies"]
    assert m2["budget"]["run_ceiling_usd"] == 4.50 and m2["smoke_plan"]["ceiling_usd"] == 0.50
    assert m2["analysis"]["judge_ceiling_usd"] == 0.50
    assert m2["budget"]["run_ceiling_usd"] + m2["smoke_plan"]["ceiling_usd"] + m2["analysis"]["judge_ceiling_usd"] <= 6.0
    bs = m2["buyer_simulator"]
    assert bs["writer"] == E.V2_WRITER and bs["model"] == M.DEEPSEEK_V4_FLASH and bs["prompt"] == E.V2_SYSTEM
    assert m2["policies"] == V1["policies"] and m2["repeats"] == V1["repeats"] and m2["buyers"] == V1["buyers"]
    assert m2["baseline"]["rows_sha256"] == MF.sha256_file(MF.ROOT / "tournament" / "full" / "rows.jsonl")
    assert len(RUN.manifest_jobs(m2)) == 200 * 6 + 2 * 100 * 4
    assert m2["analysis"]["sha256"] == MF.sha256_file(MF.ROOT / "tools" / "v2_analysis.py")
    assert (m2["gates_v2"]["L_own_max"], m2["gates_v2"]["fidelity_min"], m2["gates_v2"]["template_max"]) == (0.59, 0.90, 0.02)
    assert m2["amendment"]["from_manifest_sha256"] == MF.V2_FIRST_SHA and "No interval, D2 or DiD" in m2["amendment"]["disclosure"]


def test_second_smoke_uses_customers_the_first_did_not(m2):
    first = {j[0] for j in m2["smoke_plan"]["first_smoke"]["jobs"]}
    second = [tuple(j) for j in m2["smoke_plan"]["jobs"]]
    assert len(second) == 48 and not first & {b for b, _, _ in second} and m2["smoke_plan"]["prints_outcomes"] is False
    first_dir = MF.ROOT / "tournament" / "v2" / "smoke.83010fc4"
    ran = {r["buyer"] for r in R.read_lines(first_dir / "rows.jsonl")[0]}
    assert ran == first and json.loads((first_dir / "MANIFEST.json").read_text())["smoke_plan"]["jobs"] == m2["smoke_plan"]["first_smoke"]["jobs"]


def test_v2_smoke_prints_no_outcome_table(m2, tmp_path, monkeypatch, capsys):
    class FakeLedger:
        def __init__(self, *a, **k):
            pass

        def spent(self):
            return 7.0, {"b2": 6.5}
    row = {"buyer": "B001", "type": "timing_slot", "trade": "HVAC", "policy": "super_only", "repeat": 0, "attempt": 1,
           "status": "ok", "booked": True, "margin": 123.0, "given_away": 0.0, "lost": 0.0, "model_usd": 0.001, "net": 122.999,
           "violations": 0, "unsure": 0, "feasible": True, "oracle": 123.0}

    def fake_run(jobs, cards, conv, out, **kw):
        kw["log"]("[run] B001 super_only r0 a1: ok booked=True end=booked $0.00100")
        R.append_line(out / "rows.jsonl", row)
        return {"stop": "complete"}
    monkeypatch.setattr(RUN, "Ledger", FakeLedger)
    monkeypatch.setattr(RUN, "live_conversations", lambda m, l: None)
    monkeypatch.setattr(R, "run", fake_run)
    for test, m, blind in (("v2", m2, True), ("v1", V1, False)):
        monkeypatch.setattr(RUN, "guard", lambda approve, environ=None, test="v1", m=m: m)
        monkeypatch.setitem(RUN.TESTS, test, (tmp_path / f"{test}.json", tmp_path / f"{test}-smoke", tmp_path / f"{test}-full"))
        RUN.main(["smoke", "--approve", "x", "--test", test])
        out = capsys.readouterr().out
        assert ("| super_only |" in out) is not blind and ("booked=" in out) is not blind, test


def test_writer_prompt_carries_no_rule_trigger():
    assert V.fires(E.V2_SYSTEM) == set()
    for clause in ("no new requests or questions", "no day, date or time that is not in the facts", "What the acts mean"):
        assert clause in E.V2_SYSTEM


def test_full_v2_runs_under_the_v2_ceiling(m2, tmp_path, monkeypatch):
    seen = {}

    class FakeLedger:
        def __init__(self, *a, **k):
            pass

        def spent(self):
            return 7.0, {"b2": 6.43}
    monkeypatch.setattr(RUN, "guard", lambda approve, environ=None, test="v1": m2)
    monkeypatch.setattr(RUN, "Ledger", FakeLedger)
    monkeypatch.setattr(RUN, "live_conversations", lambda m, l: None)
    monkeypatch.setattr(RUN, "write_results", lambda out, m: None)
    monkeypatch.setattr(R, "run", lambda jobs, cards, conv, out, **kw: seen.update(kw, jobs=len(jobs)) or {"stop": "complete"})
    monkeypatch.setitem(RUN.TESTS, "v2", (tmp_path / "M.json", tmp_path / "smoke", tmp_path / "full"))
    assert RUN.main(["full", "--approve", "x", "--test", "v2"]) == 0
    assert seen["ceiling_usd"] == 4.50 and seen["checkpoint"]["limit_usd"] == 4.50 and seen["jobs"] == 2000


def test_free_writer_sees_facts_not_the_simulators_sentences(cards):
    c = next(x for x in cards if x.type == "timing_pause" and x.clarity == "clear")
    st = E.BuyerState(c.patience, "clear", True)
    conv = Conversation(c.estimate(), Recipient(c.tz, c.state), c.open_slots(), trace=Trace(sink=lambda _: None))
    fc = FakeClient([raw(content=json.dumps({"message": "We're keen but away a while, ping me in a few weeks.", "notes": "n"}))])
    w = E.FreeWordingWriter(fc)
    t = c.first_reply_hours / 24
    text, steps, fb = w.write(c, st, "first", conv, t, random.Random(0))
    prompt = json.dumps(fc.calls[0]["messages"])
    assert text.startswith("We're keen") and not fb and fc.calls[0]["model"] == M.DEEPSEEK_V4_FLASH
    assert E.instruction(c, st, "first", t, random.Random(0)) not in prompt
    for stem in ("Say ", "Do not give a number", "This turn", "week(s)"):
        assert stem not in prompt, stem
    for rx in (P._SOONER, P._WEEKS, P._FAMILY, P._READY, P._PRICE, P._THINK, P._NOT_NEEDED):
        assert rx.pattern not in prompt
    assert st.turns == [{"t": round(t, 4), "spec": "first", "clarity": "clear", "decided": False,
                         "facts": E.intent(c, st, "first", t), "text": text, "fallback": False}]


def test_free_writer_falls_back_and_logs_it(cards):
    c = next(x for x in cards if x.type == "price" and x.clarity == "clear")
    c = c.__class__(**{**c.__dict__, "walkaway": 1234.0, "anchor": 1000.0})
    st = E.BuyerState(c.patience, "clear", True)
    conv = Conversation(c.estimate(), Recipient(c.tz, c.state), c.open_slots(), trace=Trace(sink=lambda _: None))
    leak = raw(content=json.dumps({"message": "Honestly 1,234 is my max.", "notes": ""}))
    text, steps, fb = E.FreeWordingWriter(FakeClient([leak, raw(content="not json")])).write(c, st, "first", conv, 0.5, random.Random(0))
    assert fb and st.turns[-1]["fallback"] and [s.note for s in steps] == ["leak", "invalid"]


def test_v2_live_runs_use_the_free_writer_through_the_strict_client(m2, tmp_path, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "test-key")
    converse = RUN.live_conversations(m2, tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "b2": 1.0}))
    w = converse.writer
    assert isinstance(w, E.FreeWordingWriter) and w.model == M.DEEPSEEK_V4_FLASH and w.params == E.V2_WRITER_PARAMS
    assert isinstance(w.client, R.StrictClient) and w.client.client.fallbacks == {}
    v1 = RUN.live_conversations(V1, tf.Ledger(tmp_path / "l2.jsonl", caps={"total": 1.0, "b2": 1.0}))
    assert type(v1.writer) is E.LLMWriter


def test_v2_verify_catches_a_changed_prompt_or_analysis(m2, tmp_path, monkeypatch):
    path = tmp_path / "v2" / "MANIFEST.json"
    path.parent.mkdir()
    h = MF.write(MF.build(V1["buyers"]["file"], V1["buyers"]["n"], V1["repeats"]["k"], V1["repeats"]["subsample"],
                          MF.V1_BUYER_MODEL, None, {"projected_usd": 1.0}, v2=True), path)
    assert MF.verify(path) == (h, [])
    monkeypatch.setattr(E, "V2_SYSTEM", E.V2_SYSTEM + " Avoid the word weeks.")
    assert "buyer_simulator differs from the live code" in MF.verify(path)[1]
    monkeypatch.undo()
    other = tmp_path / "other.py"
    other.write_text("# changed\n")
    monkeypatch.setattr(MF, "V2_ANALYSIS", str(other))
    assert "analysis differs from the live code" in MF.verify(path)[1]


def test_a_run_directory_refuses_another_manifest(tmp_path):
    class L:
        def spent(self):
            return 0.0, {"b2": 1.0}
    RUN.spent_since_start(L(), tmp_path, "b2", "A")
    RUN.spent_since_start(L(), tmp_path, "b2", "A")
    with pytest.raises(SystemExit):
        RUN.spent_since_start(L(), tmp_path, "b2", "B")


# ---- analysis on synthetic data ---------------------------------------------------------------------------------------
def test_leakage_counts_the_rule_meant_for_the_type():
    lines = [("timing_pause", "Not for about 3 weeks, we're away."), ("timing_pause", "Once we're back from the lake."),
             ("timing_slot", "We need it done by Wednesday."), ("timing_slot", "Midweek at the latest, the house is an oven."),
             ("price", "Could you do $3,400?")]
    lk = V.leakage(lines)
    assert lk["L_own"]["share"] == pytest.approx(2 / 5) and lk["L_own_by_type"]["timing_pause"]["share"] == 0.5
    assert lk["L_own_with_amount"]["share"] == pytest.approx(3 / 5)  # the $-amount pattern only counts descriptively


def test_v1_baseline_is_the_one_in_the_draft():
    lk = V.leakage(V.v1_lines(R.read_lines(V.V1_OUTCOMES)[0]))
    assert lk["L_own"]["n"] == 3133 and lk["L_own"]["share"] == 0.7667 and round(lk["L_any"]["share"], 2) == 0.97
    assert lk["L_own_with_amount"]["share"] == 0.7897  # the pre-registered definition of 83010fc4 (79 %)
    assert {t: v["share"] for t, v in lk["L_own_by_type"].items()} == {
        "channel": 1.0, "family": 0.6831, "price": 0.9069, "ready": 0.9961, "timing_pause": 0.5798, "timing_slot": 0.8615}


@pytest.mark.parametrize("facts,judge,ok", [
    ({"act": "wants_it_later", "wait_weeks_about": 3}, {"kind": "wait_until_later", "weeks": 3, "household": False}, True),
    ({"act": "wants_it_later", "wait_weeks_about": 3}, {"kind": "wait_until_later", "weeks": None}, False),
    ({"act": "cost_concern", "could_pay": 3600}, {"kind": "cost_too_high", "amount": "3,500"}, True),
    ({"act": "cost_concern", "could_pay": 3600}, {"kind": "cost_too_high", "amount": 3000}, False),
    ({"act": "cost_concern", "give_a_figure": False}, {"kind": "cost_too_high", "amount": 2000}, False),
    ({"act": "needs_it_sooner", "done_by": "Wednesday"}, {"kind": "need_sooner", "weekday": "wednesday"}, True),
    ({"act": "household_view", "discussed_with": "my wife", "unnecessary_item": "Surge protector"},
     {"kind": "household", "household": True, "item": "the surge protector"}, True),
    ({"act": "household_must_review", "who": "my dad"}, {"kind": "undecided", "household": True}, True),
    ({"act": "household_must_review", "who": "my dad"}, {"kind": "undecided", "household": False}, False),
    ({"act": "household_must_review", "who": "my landlord"}, {"kind": "household", "household": True}, True),
])
def test_faithful(facts, judge, ok):
    assert V.faithful(facts, judge)[0] is ok


def test_old_mapping_is_kept_for_comparison():
    facts, j = {"act": "household_must_review", "who": "my partner"}, {"kind": "undecided", "household": True}
    assert V.faithful(facts, j)[0] and not V.faithful(facts, j, V.OLD_ACCEPT)[0]


def test_fidelity_reports_lines_the_judge_did_not_read():
    class J:
        def __init__(self):
            self.n = 0

        def read(self, text):
            self.n += 1
            return None if text == "skip" else {"error": "TFError: 503"} if text == "down" else {"kind": "go_ahead"}
    res = V.fidelity([({"act": "go_ahead"}, "yes"), ({"act": "go_ahead"}, "skip"), ({"act": "cost_concern"}, "no"),
                      ({"act": "go_ahead"}, "down")], J(), workers=1)
    assert res["share"] == 0.5 and res["not_judged"] == 2 and res["n"] == 2 and res["judge_errors"] == ["TFError: 503"]
    assert res["share_old_mapping"] == 0.5 and len(res["judged"]) == 2


def _final(diff_by_buyer, test):
    rows = []
    for b, d in diff_by_buyer.items():
        rows.append({"buyer": b, "policy": "smart_rules", "repeat": 0, "type": "price", "net": 1000.0})
        for p in ("super_only", "routed", "super_vl", "shop_rule", "concession_heuristic"):
            rows.append({"buyer": b, "policy": p, "repeat": 0, "type": "price", "net": 1000.0 + d})
    return rows


def test_decision_rule_on_synthetic_runs():
    buyers = [f"b{i}" for i in range(60)]
    v1 = _final({b: -100.0 + (i % 5) for i, b in enumerate(buyers)}, "v1")
    better = _final({b: 50.0 + (i % 7) for i, b in enumerate(buyers)}, "v2")
    out = V.decide(v1, better)
    assert out["primary_met"] and out["robustness_met"] and out["DiD_super_only"]["point"] > 0
    assert out["DiD_super_only"]["point"] == pytest.approx(sum(150.0 + (i % 7) - (i % 5) for i in range(60)))
    same = V.decide(v1, v1)
    assert not same["primary_met"] and not same["robustness_met"] and "v1 headline stands" in same["verdict"]
