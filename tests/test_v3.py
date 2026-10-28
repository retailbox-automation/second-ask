"""v3: rules first, Nemotron only where the rules do not know. The rules are v1's, the hybrid hands off only on rules 8
and 9, both policies meet the same words until the first hand-off, the manifest and the decision rule are as
pre-registered."""
import json, random, subprocess, sys, threading, types

import pytest

from fakes import FakeClient, choice, label
from second_ask import agent as A, models as M
from second_ask.compliance import Recipient
from second_ask.menu import build_menu
from second_ask.trace import Trace
from second_ask.tournament import buyers as B, env as E, manifest as MF, run as RUN, runner as R
from second_ask.tournament.policies import (HYBRID, SMART, UNKNOWN_BRANCHES, FakeSimClient, RulesThenSuper, SmartRules,
                                            make)

sys.path.insert(0, str(MF.ROOT / "tools"))
import v3_analysis as V  # noqa: E402

V1 = json.loads((MF.ROOT / "tournament" / "MANIFEST.json").read_text())


@pytest.fixture(scope="module")
def cards():
    return B.load(MF.ROOT / "tournament" / "buyers.json")


@pytest.fixture(scope="module")
def v1_policies():
    """policies.py exactly as it was at the v1 run (commit 6d66d53), imported next to the live package."""
    src = subprocess.run(["git", "show", "6d66d53:second_ask/tournament/policies.py"], cwd=MF.ROOT, check=True,
                         capture_output=True, text=True).stdout
    mod = types.ModuleType("second_ask.tournament._policies_v1")
    mod.__package__ = "second_ask.tournament"
    exec(compile(src, "policies_v1.py", "exec"), mod.__dict__)
    return mod


def states_from_v1_transcripts(cards):
    """Conversation states rebuilt from every prefix of v1's smart_rules transcripts that ends in a customer line or a
    shop line (silence), with the menu at that moment."""
    by = {c.id: c for c in cards}
    for line in (MF.ROOT / "tournament" / "full" / "outcomes.jsonl").read_text().splitlines():
        o = json.loads(line)
        if o["policy"] != SMART:
            continue
        c = by[o["buyer"]]
        est = c.estimate()
        for i in range(1, len(o["transcript"]) + 1):
            conv = A.Conversation(est, Recipient(c.tz, c.state), c.open_slots(), trace=Trace(sink=lambda _: None))
            conv.views = 0 if c.type == "channel" else 1
            for e in o["transcript"][:i]:
                conv.events.append(A.Event(E.at(e["day"]), e["who"], e["channel"], e["text"], e["move"]))
            now = E.at(o["transcript"][i - 1]["day"] + E.SELLER_LATENCY_DAYS)
            yield conv, build_menu(est, conv.offer, conv.slots_after(now))
            if o["transcript"][i - 1]["who"] == "customer" and not any(e.move_id == "ask_why" for e in conv.events):
                asked = A.Conversation(est, Recipient(c.tz, c.state), c.open_slots(), trace=Trace(sink=lambda _: None))
                asked.views, asked.events = conv.views, conv.events[:-1] + [
                    A.Event(conv.events[-1].at, "shop", conv.events[-1].channel, "What is holding you back?", "ask_why"),
                    conv.events[-1]]
                yield asked, build_menu(est, asked.offer, asked.slots_after(now))  # the same words after asking why


def test_smart_rules_make_v1s_move_at_every_v1_state(cards, v1_policies):
    n, branches = 0, set()
    for conv, menu in states_from_v1_transcripts(cards):
        move, branch = SmartRules.choose_with_branch(conv, menu)
        assert move.id == v1_policies.SmartRules.choose(conv, menu).id
        assert SmartRules.choose(conv, menu).id == move.id
        n, branches = n + 1, branches | {branch}
    assert n > 1400 and {"amount", "sooner", "later", "family", "ready", "think", "none", "silence"} <= branches


def test_smart_rules_whole_conversations_equal_v1(cards, v1_policies):
    for c in cards:
        a = E.run_one(c, SmartRules(), E.TemplateWriter(), 0)
        b = E.run_one(c, v1_policies.SmartRules(), E.TemplateWriter(), 0)
        assert (a.booked, a.price, a.end, a.transcript) == (b.booked, b.price, b.end, b.transcript)


def test_smart_rules_spec_in_the_manifest_is_v1s():
    assert MF.policy_spec(SMART) == V1["policies"][SMART]


@pytest.mark.parametrize("text,branch", [("I'll think about it and get back to you.", "think"),
                                         ("The timing doesn't really work for us.", "none"),
                                         ("Maybe.", "think"), ("Hmm ok.", "none")])
def test_unknown_branches_are_rules_8_and_9(text, branch):
    from test_agent import conv, NOW
    c = conv(text)
    assert SmartRules.choose_with_branch(c, build_menu(c.estimate, c.offer, c.slots_after(NOW)))[1] == branch
    assert UNKNOWN_BRANCHES == ("think", "none")


@pytest.mark.parametrize("text", ["Another shop quoted me $640. Can you do better?", "We want it, but not for about 3 weeks.",
                                  "My wife needs to look at it with me first.", "It is more than I expected.",
                                  "Looks good, when can you come?", None])
def test_hybrid_follows_the_rules_without_a_model_call(text):
    from test_agent import conv, NOW
    c, fc = conv(text), FakeClient([])
    if text is None:
        c.views = 2
    d = RulesThenSuper(fc).act(c, NOW, "objection")
    menu = build_menu(c.estimate, c.offer, c.slots_after(NOW))
    assert fc.calls == [] and d.move.id == SmartRules.choose(c, menu).id and c.trace.steps == []


def test_hybrid_hands_off_on_rules_8_and_9_and_logs_it():
    from test_agent import conv, NOW
    c = conv("I'll think about it and get back to you.")
    fc = FakeClient([label("timing"), choice("checkin_14", "No problem, we will check back in two weeks.")])
    pol = RulesThenSuper(fc)
    d = pol.act(c, NOW, "objection")
    assert d.move.id == "checkin_14" and [x["model"] for x in fc.calls] == [M.SUPER, M.SUPER]
    hand = [s for s in c.trace.steps if s.step == "handoff"]
    assert len(hand) == 1 and json.loads(hand[0].note) == {"branch": "think", "rule_move": "ask_why",
                                                          "model_move": "checkin_14", "stop": None}
    assert E.integrity(c.trace.steps, pol.arm(), M.QWEN3_30B) == (0, 0, 0)


def test_hybrid_logs_a_model_opt_out_as_a_stop():
    from test_agent import conv, NOW
    c = conv("Hmm ok.")
    d = RulesThenSuper(FakeClient([label("opt_out")])).act(c, NOW, "objection")
    note = json.loads(next(s for s in c.trace.steps if s.step == "handoff").note)
    assert d.move is None and note["model_move"] is None and note["stop"].startswith("label model read an opt-out")
    assert ("policy stopped: " + d.stop_reason).startswith(V.OPT_OUT_STOP)


def test_hybrid_runs_end_to_end_on_the_fake_client(cards):
    handoffs = 0
    for c in cards:
        fc = FakeSimClient(seed=1)
        fc.card = c
        o = E.run_one(c, make(HYBRID, fc), E.TemplateWriter(), 0)
        assert o.off_arm_calls == 0 and o.chronology_errors == 0 and o.platform_failures == 0
        handoffs += sum(s["step"] == "handoff" for s in o.steps)
        assert o.seller_calls == 2 * sum(s["step"] == "handoff" and json.loads(s["note"])["model_move"] is not None
                                         for s in o.steps) + sum(s["step"] == "handoff" and json.loads(s["note"])["stop"]
                                                                 is not None for s in o.steps)
    assert handoffs > 0


class CountingWriter:
    """A buyer writer whose every call gives new words (like a sampled model), and remembers each call."""
    model = M.QWEN3_30B

    def __init__(self):
        self.calls, self.lock = 0, threading.Lock()

    def write(self, card, st, spec, conv, t, rng):
        with self.lock:
            self.calls += 1
            n = self.calls
        text, _, fb = E.TemplateWriter().write(card, st, spec, conv, t, rng)
        st.notes = f"note {n}"
        return f"{text} (#{n})", [], fb


def test_shared_writer_gives_both_policies_the_same_words_until_they_part(cards):
    w, cache, lock = CountingWriter(), {}, threading.Lock()
    same = 0
    for c in cards:
        fc = FakeSimClient(seed=3)
        fc.card = c
        a = E.run_one(c, make(SMART), E.SharedWriter(w, 0, cache, lock), 0)
        b = E.run_one(c, make(HYBRID, fc), E.SharedWriter(w, 0, cache, lock), 0)
        if not any(s["step"] == "handoff" for s in b.steps):
            assert (a.transcript, a.booked, a.price, a.end) == (b.transcript, b.booked, b.price, b.end)
            same += 1
        else:
            first = next(i for i, e in enumerate(b.transcript) if e["who"] == "customer")
            assert a.transcript[first]["text"] == b.transcript[first]["text"]  # the first line is always shared
    assert 50 < same < len(cards)
    alone = CountingWriter()
    for c in cards:
        E.run_one(c, make(SMART), E.SharedWriter(alone, 0, {}, threading.Lock()), 0)
        E.run_one(c, make(SMART), E.SharedWriter(alone, 1, {}, threading.Lock()), 1)
    assert w.calls < alone.calls  # lines were reused across policies, never across repeats


def test_shared_writer_keys_on_repeat_history_and_restores_notes(cards):
    c = next(x for x in cards if x.type == "price")
    w, cache, lock = CountingWriter(), {}, threading.Lock()

    def line(rep, extra=None):
        st = E.BuyerState(c.patience, c.clarity, True)
        conv = A.Conversation(c.estimate(), Recipient(c.tz, c.state), c.open_slots(), trace=Trace(sink=lambda _: None))
        if extra:
            conv.events.append(A.Event(E.at(0.5), "shop", "email", extra, "ask_why"))
        text, steps, _ = E.SharedWriter(w, rep, cache, lock).write(c, st, "first", conv, 1.0, random.Random(0))
        return text, st.notes, steps
    assert line(0) == line(0)[:2] + ([],) and w.calls == 1
    assert line(1)[0] != line(0)[0] and line(0, "What is holding you back?")[0] != line(0)[0] and w.calls == 3


def test_shared_writer_does_not_cache_a_failure(cards):
    c = cards[0]

    class Flaky(CountingWriter):
        def write(self, *a):
            if self.calls == 0:
                self.calls += 1
                raise R.PlatformFailure("503")
            return super().write(*a)
    w, cache, lock = Flaky(), {}, threading.Lock()
    conv = A.Conversation(c.estimate(), Recipient(c.tz, c.state), c.open_slots(), trace=Trace(sink=lambda _: None))
    st = E.BuyerState(c.patience, c.clarity, True)
    with pytest.raises(R.PlatformFailure):
        E.SharedWriter(w, 0, cache, lock).write(c, st, "first", conv, 1.0, random.Random(0))
    assert cache == {} and E.SharedWriter(w, 0, cache, lock).write(c, st, "first", conv, 1.0, random.Random(0))[0]


def test_shared_writer_writes_once_under_concurrency(cards):
    c = cards[0]
    w, cache, lock = CountingWriter(), {}, threading.Lock()
    conv = A.Conversation(c.estimate(), Recipient(c.tz, c.state), c.open_slots(), trace=Trace(sink=lambda _: None))
    out = []

    def go():
        st = E.BuyerState(c.patience, c.clarity, True)
        out.append(E.SharedWriter(w, 0, cache, lock).write(c, st, "first", conv, 1.0, random.Random(0))[0])
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert w.calls == 1 and len(set(out)) == 1


# ---- manifest and run -------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def m3():
    return json.loads(MF.canonical(MF.build(V1["buyers"]["file"], V1["buyers"]["n"], MF.V3_K, "all",
                                            V1["buyer_simulator"]["model"], None, {"projected_usd": 1.0}, v3=True)))


def test_only_the_v3_files_differ_from_v1_beyond_v2():
    now = MF.source_hashes()
    assert set(now) == set(V1["code"])
    changed = {f for f in now if now[f] != V1["code"][f]}
    assert changed <= set(MF.V3_CHANGED) | set(MF.V2_CHANGED)
    for f in ("second_ask/agent.py", "second_ask/chooser.py", "second_ask/menu.py", "second_ask/compliance.py",
              "second_ask/classifier.py", "second_ask/tf_client.py", "second_ask/tournament/scoring.py",
              "second_ask/tournament/runner.py", "second_ask/tournament/buyers.py"):
        assert now[f] == V1["code"][f], f


def test_v3_manifest(m3, cards):
    assert m3["test"] == MF.V3_TEST and m3["run_policies"] == [SMART, HYBRID] and set(m3["policies"]) == {SMART, HYBRID}
    assert m3["policies"][SMART] == V1["policies"][SMART]
    assert m3["policies"][HYBRID]["model_on_unknown"]["label"] == V1["policies"]["super_only"]["label"]
    assert m3["policies"][HYBRID]["model_on_unknown"]["choose"] == V1["policies"]["super_only"]["choose"]
    assert m3["policies"][HYBRID]["unknown_branches"]["ids"] == ["think", "none"]
    assert m3["buyers"] == V1["buyers"] and m3["buyer_simulator"]["model"] == V1["buyer_simulator"]["model"]
    assert m3["buyer_simulator"]["params"] == V1["buyer_simulator"]["params"] and m3["buyer_simulator"]["common_random_numbers"]
    assert m3["repeats"]["k"] == 5 and m3["repeats"]["subsample"] == "all"
    assert len(RUN.manifest_jobs(m3)) == 2000
    assert m3["budget"]["run_ceiling_usd"] == 3.00 and m3["smoke_plan"]["ceiling_usd"] == 0.30
    assert m3["budget"]["run_ceiling_usd"] + m3["smoke_plan"]["ceiling_usd"] <= 6.0
    assert len(m3["smoke_plan"]["jobs"]) == 28 and m3["smoke_plan"]["prints_outcomes"] is False
    assert m3["prereg"]["sha256"] == MF.sha256_file(MF.ROOT / "tournament" / "v3" / "PREREG.md")
    assert m3["analysis"]["sha256"] == MF.sha256_file(MF.ROOT / "tools" / "v3_analysis.py")
    assert "designed after v1's results were known" in m3["disclosure"]
    assert "lower bound of D3" in m3["decision_rules"]["primary"] and "is above 0" in m3["decision_rules"]["primary"]
    assert "false opt-outs" in m3["decision_rules"]["primary"]
    assert m3["baseline"]["rows_sha256"] == MF.sha256_file(MF.ROOT / "tournament" / "full" / "rows.jsonl")


def test_v3_verify_catches_a_changed_prereg_analysis_or_rule(tmp_path, monkeypatch):
    path = tmp_path / "v3" / "MANIFEST.json"
    path.parent.mkdir()
    h = MF.write(MF.build(V1["buyers"]["file"], V1["buyers"]["n"], MF.V3_K, "all", V1["buyer_simulator"]["model"], None,
                          {"projected_usd": 1.0}, v3=True), path)
    assert MF.verify(path) == (h, [])
    other = tmp_path / "x.md"
    other.write_text("changed\n")
    monkeypatch.setattr(MF, "V3_PREREG", str(other))
    assert "prereg differs from the live code" in MF.verify(path)[1]
    monkeypatch.undo()
    monkeypatch.setattr("second_ask.tournament.policies.UNKNOWN_BRANCHES", ("none",))
    monkeypatch.setattr(MF, "UNKNOWN_BRANCHES", ("none",))
    assert any("policy rules_then_super" in p for p in MF.verify(path)[1])


def test_v3_live_runs_share_the_v1_writer_through_the_strict_client(m3, tmp_path, monkeypatch):
    from second_ask import tf_client as tf
    monkeypatch.setenv("NEBIUS_API_KEY", "test-key")
    conv = RUN.live_conversations(m3, tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "b2": 1.0}))
    assert type(conv.writer) is E.LLMWriter and conv.writer.model == V1["buyer_simulator"]["model"]
    assert conv.shared is not None and isinstance(conv.writer.client, R.StrictClient)
    v1 = RUN.live_conversations(V1, tf.Ledger(tmp_path / "l2.jsonl", caps={"total": 1.0, "b2": 1.0}))
    assert v1.shared is None


def test_v3_smoke_and_run_print_no_outcomes(m3, tmp_path, monkeypatch, capsys):
    class FakeLedger:
        def __init__(self, *a, **k):
            pass

        def spent(self):
            return 7.0, {"b2": 6.62}
    row = {"buyer": "B001", "type": "timing_slot", "trade": "HVAC", "policy": HYBRID, "repeat": 0, "attempt": 1,
           "status": "ok", "booked": True, "margin": 123.0, "given_away": 0.0, "lost": 0.0, "model_usd": 0.001,
           "net": 122.999, "violations": 0, "unsure": 0, "feasible": True, "oracle": 123.0}
    seen = {}

    def fake_run(jobs, cards, conv, out, **kw):
        kw["log"]("[run] B001 rules_then_super r0 a1: ok booked=True end=booked $0.00100")
        R.append_line(out / "rows.jsonl", row)
        seen.update(kw, jobs=len(jobs))
        return {"stop": "complete"}
    monkeypatch.setattr(RUN, "Ledger", FakeLedger)
    monkeypatch.setattr(RUN, "live_conversations", lambda m, l: None)
    monkeypatch.setattr(RUN, "guard", lambda approve, environ=None, test="v1": m3)
    monkeypatch.setattr(RUN, "write_results", lambda out, m: None)
    monkeypatch.setattr(R, "run", fake_run)
    monkeypatch.setitem(RUN.TESTS, "v3", (tmp_path / "M.json", tmp_path / "smoke", tmp_path / "full"))
    RUN.main(["smoke", "--approve", "x", "--test", "v3"])
    out = capsys.readouterr().out
    assert "booked=" not in out and f"| {HYBRID} |" not in out and seen["ceiling_usd"] == 0.30 and seen["jobs"] == 28
    RUN.main(["full", "--approve", "x", "--test", "v3"])
    out = capsys.readouterr().out
    assert "booked=" not in out and seen["ceiling_usd"] == 3.00 and seen["checkpoint"]["limit_usd"] == 3.00
    assert seen["jobs"] == 2000


# ---- the decision rule on synthetic runs ------------------------------------------------------------------------------
def synthetic(tmp_path, gain, false_stop=False, n=200, k=5):
    rows, outs = [], []
    for i in range(n):
        b = f"B{i + 1:03d}"
        for rep in range(k):
            for p in (SMART, HYBRID):
                net = 1000.0 + (gain + (i % 7 - 3) * 2 if p == HYBRID else 0.0)
                end = "booked"
                if false_stop and p == HYBRID and i == 3 and rep == 0:
                    end = "policy stopped: label model read an opt-out; no further messages"
                rows.append({"buyer": b, "type": "price", "trade": "HVAC", "policy": p, "repeat": rep, "attempt": 1,
                             "status": "ok", "booked": True, "net": net, "margin": net, "model_usd": 0.0, "violations": 0,
                             "unsure": 0, "off_arm_calls": 0, "platform_failures": 0, "end": end})
                steps = [{"step": "handoff", "note": json.dumps({"branch": "think", "rule_move": "ask_why",
                                                                 "model_move": "checkin_14", "stop": None})}] if p == HYBRID else []
                outs.append({"buyer": b, "type": "price", "policy": p, "repeat": rep, "attempt": 1, "steps": steps})
    d = tmp_path / f"run{gain}{false_stop}"
    d.mkdir()
    (d / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "outcomes.jsonl").write_text("".join(json.dumps(o) + "\n" for o in outs))
    ids = [f"B{i + 1:03d}" for i in range(n)]
    return d, R.plan(ids, [SMART, HYBRID], [SMART, HYBRID], k, "all")


@pytest.mark.parametrize("gain,false_stop,expect", [(5.0, False, "beats"), (-5.0, False, "behind"), (0.0, False, "no clear difference"),
                                                    (0.3, False, "no clear difference"),
                                                    (5.0, True, "no clear difference")])
def test_decision_rule(tmp_path, m3, gain, false_stop, expect):
    d, jobs = synthetic(tmp_path, gain, false_stop)
    res = V.analyse(d, m3, jobs)
    assert res["verdict"] == expect
    if gain == 0.3:
        assert res["d3"]["point"] > 0 > res["d3"]["lo"]  # a positive point estimate alone is not a win
    assert res["handoffs"]["total"] == 1000 and res["handoffs"]["model_move_differs"] == 1000
    if false_stop:
        assert res["table"][HYBRID]["false_opt_out_conversations"] == 1 and res["not_worse_failed_on"] == ["false_opt_outs"]
        assert "false opt-outs" in V.markdown(res)


def test_analysis_refuses_an_unfinished_run(tmp_path):
    (tmp_path / "run_report.json").write_text(json.dumps({"stop": "run ceiling $3.00 reached"}))
    assert V.main([str(tmp_path)]) == 2


def test_handoff_steps_are_not_model_calls():
    from test_agent import conv, NOW
    c = conv("Hmm ok.")
    RulesThenSuper(FakeClient([label("other"), choice("answer", "Happy to explain what the job covers.")])).act(c, NOW, "x")
    assert sum(1 for s in c.trace.steps if s.model) == 2 and c.trace.steps[-1].cost == 0.0


def test_a_customer_the_code_layer_stopped_stays_with_the_rules():
    from test_agent import conv, NOW
    c = conv("STOP")
    c.recipient.opted_out = True
    fc = FakeClient([])
    d = RulesThenSuper(fc).act(c, NOW, "objection")
    assert fc.calls == [] and c.trace.steps == [] and d.message is None and d.stop_reason.startswith("blocked by compliance")
