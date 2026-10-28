"""Second Read data generator: gold sampling, the writer's prompt and retries, fidelity agreement, the test-split guard."""
import json, subprocess
from collections import Counter
from datetime import date

import pytest

from second_read import data as D
from second_read.contract import FIELDS, Facts
from second_ask import tf_client as tf
from second_ask.tournament.buyers import load
from fakes import FakeClient, raw

CARDS = load(D.CARDS)
BIG = D.sample_all(CARDS, "dev", 3000, "unit-seed")
CARD_BY_ID = {c.id: c for c in CARDS}


def n_facts(g):
    return sum([g.ceiling_usd is not None, bool(g.drop_items), g.need_by is not None, g.wait_weeks is not None,
                g.decider is not None, g.ready])


def fine(s):
    """A message that passes the wording checks of sample s."""
    return {"keyword": "STOP please", "phrase": "Please take me off your list."}.get(s.words.get("stop"), "Okay.")


def ok_reply(msg):
    return raw(content=json.dumps({"message": msg}))


# ---- sampling ----------------------------------------------------------------------------------------------------------
def test_plan_quotas_exact():
    rows = D.plan(200, "dev-20261028")
    assert Counter(s for s, _ in rows) == {"S1": 50, "S2": 70, "S3": 50, "S4": 30}
    stops = [st for _, st in rows if st]
    assert len(stops) == 16 and Counter(stops) == {"keyword": 8, "phrase": 8}
    assert {s for s, st in rows if st} == {"S1", "S2", "S3", "S4"}  # spread across strata
    assert D.plan(200, "dev-20261028") == rows and D.plan(200, "other") != rows


def test_sampling_is_deterministic():
    a = D.sample_all(CARDS, "dev", 50, "x")
    b = D.sample_all(CARDS, "dev", 50, "x")
    assert [(s.card.id, s.today, s.gold) for s in a] == [(s.card.id, s.today, s.gold) for s in b]


def test_strata_mix_and_fact_counts():
    assert Counter(s.stratum for s in BIG) == D.quota(3000, D.STRATA)
    for s in BIG:
        g = s.gold
        if g.stop:
            assert g == Facts(stop=True) and s.kinds == ("stop",)
            continue
        k = n_facts(g)
        assert k >= 1  # at least one fact (a pure "ready" line counts)
        assert {"S1": k == 1, "S2": k in (2, 3), "S3": k in (1, 2), "S4": k in (1, 2)}[s.stratum], (s.stratum, g)
        kinds = set(s.kinds)
        assert not any(c <= kinds for c in D.CLASHES)
        if s.stratum == "S3":
            assert kinds & {"ceiling", "drop", "need_by", "wait"}
    multi = Counter(n_facts(s.gold) for s in BIG if s.stratum == "S2" and not s.gold.stop)
    assert multi[2] > 0 and multi[3] > 0


def test_stop_share_and_wording():
    stops = [s for s in BIG if s.gold.stop]
    assert len(stops) == round(3000 * D.STOP_SHARE)
    styles = Counter(s.meta["stop_style"] for s in stops)
    assert abs(styles["keyword"] - styles["phrase"]) <= 1


def test_constraints_on_values():
    for s in BIG:
        g, est = s.gold, s.card.estimate()
        optional = {i.name for i in est.items if i.optional}
        assert s.today > date(2026, 11, 2) and (s.today - date(2026, 11, 2)).days <= 10
        assert s.today < s.quoted and s.slots and all(d > s.today for d in s.slots) and s.quoted in s.slots
        assert set(g.drop_items) <= optional
        if g.need_by:
            assert date.fromisoformat(g.need_by) > s.today
        if g.wait_weeks is not None:
            assert 1 <= g.wait_weeks <= 8
        if g.decider is not None:
            assert g.decider in D.DECIDERS
        if g.ceiling_usd is not None:
            fl, lst = est.floor(frozenset(g.drop_items)), est.list_price(frozenset(g.drop_items))
            assert s.meta["ceiling_feasible"] == (g.ceiling_usd >= fl)
            if s.meta["ceiling_feasible"]:
                assert fl <= g.ceiling_usd < lst
            else:
                assert 0 < g.ceiling_usd < fl


def test_ceiling_infeasible_share_and_need_by_before_earliest():
    ceil = [s for s in BIG if s.gold.ceiling_usd is not None]
    share = sum(not s.meta["ceiling_feasible"] for s in ceil) / len(ceil)
    assert 0.10 <= share <= 0.20
    nb = [s for s in BIG if s.gold.need_by]
    early = sum(s.meta["need_by_before_earliest_slot"] for s in nb)
    assert 0.05 * len(nb) <= early <= 0.5 * len(nb)


def test_s3_end_of_next_week_is_friday():
    hits = [s for s in BIG if s.words.get("need_by") == "by the end of next week"]
    assert hits
    for s in hits:
        d = date.fromisoformat(s.gold.need_by)
        assert d.weekday() == 4 and 5 <= (d - s.today).days <= 11  # Friday of the week after today's


def test_gold_is_valid_contract_facts():
    from second_read.contract import parse
    for s in BIG[:500]:
        assert parse(s.gold.to_dict(), [i.name for i in s.card.estimate().items if i.optional]) == s.gold


# ---- the writer's prompt -----------------------------------------------------------------------------------------------
FORBIDDEN = ("fact", "rule", "regex", "pattern", "ceiling", "stratum", "gold", "drop_items", "need_by", "wait_weeks",
             "decider", "\\b", "(?", "[a-z", "smartrules", "nemotron")


def test_writer_prompt_never_shows_rules_or_field_names():
    for s in BIG[:800]:
        text = " ".join(m["content"] for m in D.writer_messages(s)).lower()
        for w in FORBIDDEN:
            assert w not in text, (w, s.id)


def test_writer_prompt_states_every_gold_fact():
    for s in BIG[:800]:
        g, text = s.gold, D.writer_messages(s)[1]["content"]
        if g.stop:
            assert "contact you again" in text or "stop texting" in text
            continue
        if g.ceiling_usd is not None:
            assert f"{g.ceiling_usd:,.0f}" in text
        for name in g.drop_items:
            assert name in text
        if g.need_by:
            assert D.long_date(date.fromisoformat(g.need_by)) in text
        if g.wait_weeks:
            assert (D.WAIT_WORDS[g.wait_weeks] if s.stratum == "S3" else f"about {g.wait_weeks} week") in text
        if g.decider:
            assert g.decider in text
        assert ("book it now" in text) == g.ready


# ---- writing, retries, redraws -----------------------------------------------------------------------------------------
def line(stratum="S1", stop=None, i=0):
    return D.sample_line(CARDS, "dev", "seed-w", i, stratum, stop)


def test_writer_retry_then_ok():
    fc = D.MeteredClient(FakeClient([raw(content="not json"), ok_reply("We don't need the extra part, thanks.")]))
    text, tries, notes = D.write(fc, line())
    assert text and tries == 2 and notes == [["not a JSON message"]]


def test_writer_rejects_long_and_stop_rules():
    s = line(stop="phrase")
    fc = D.MeteredClient(FakeClient([ok_reply("Please stop texting me."), ok_reply("x" * 401)]))
    text, tries, notes = D.write(fc, s)
    assert text is None and notes == [["stop word used"], ["too long"]]
    s = line(stop="keyword")
    assert D.problems(s, "Take me off your list") == ["no stop word"] and D.problems(s, "STOP") == []


def test_s3_checks_item_name_and_dollar_sign():
    s = next(x for x in BIG if x.stratum == "S3" and x.gold.drop_items and x.gold.ceiling_usd is not None)
    name = s.gold.drop_items[0]
    assert f"item name {name!r} used" in D.problems(s, f"No {name.upper()} please, $500 max")
    assert "dollar sign used" in D.problems(s, "skip the extra gadget, $500 tops")
    assert D.problems(s, "skip the extra gadget, 500 tops") == []


def test_make_line_redraws_after_two_failed_tries(tmp_path):
    rows = D.plan(10, "seed-r")
    s0 = D.sample_line(CARDS, "dev", "seed-r", 0, *rows[0])
    s1 = D.sample_line(CARDS, "dev", "seed-r", 0, *rows[0], redraw=1)
    assert s1.seed != s0.seed
    judge_reply = raw(content=json.dumps(s1.gold.to_dict()))
    fc = D.MeteredClient(FakeClient([ok_reply(""), raw(content="{}"), ok_reply(fine(s1)), judge_reply]))
    x = D.make_line(fc, CARDS, s0, rows[0], "seed-r", 0)
    assert x["writer"] == {"model": D.WRITER_MODEL, "attempts": 1, "fallback": 1}
    assert x["seed"] == s1.seed and x["gold"] == s1.gold.to_dict() and x["meta"]["redrawn"][0]["seed"] == s0.seed
    assert x["fidelity"]["agree"] is True and set(x["fidelity"]["agree_fields"]) == set(FIELDS)
    assert set(x) >= {"id", "split", "stratum", "card_id", "today", "weekday", "quoted_slot", "open_slots", "text", "gold",
                      "writer", "fidelity"}


def test_writer_platform_error_is_visible_not_a_template():
    fc = D.MeteredClient(FakeClient([tf.TFError(503, "down")]))
    with pytest.raises(D.PlatformError):
        D.write(fc, line())


def test_judge_platform_error_is_recorded_line_kept():
    s = line()
    fc = D.MeteredClient(FakeClient([ok_reply(fine(s)), tf.TFError(500, "x")]))
    x = D.make_line(fc, CARDS, s, ("S1", None), "seed-w", 0)
    assert x["fidelity"]["facts"] is None and x["fidelity"]["agree"] is False and "platform" in x["fidelity"]["error"]


def test_writer_uses_v2_writer_model_and_params():
    fc = FakeClient([ok_reply("Sounds fine.")])
    D.write(D.MeteredClient(fc), line())
    call = fc.calls[0]
    assert call["model"] == "deepseek-ai/DeepSeek-V4-Flash-0731"
    assert call["params"]["response_format"] == {"type": "json_object"}
    assert {k: call["params"][k] for k in D.WRITER_PARAMS} == D.WRITER_PARAMS


# ---- fidelity agreement --------------------------------------------------------------------------------------------------
GOLD = Facts(False, 1000.0, ("Surge protector",), "2026-11-06", None, "wife", False).to_dict()


def test_agreement_tolerances():
    ok, f = D.agreement(GOLD, dict(GOLD, ceiling_usd=1019.0, drop_items=["surge PROTECTOR"], decider="my wife"))
    assert ok and all(f.values())
    ok, f = D.agreement(GOLD, dict(GOLD, ceiling_usd=1021.0))
    assert not ok and not f["ceiling_usd"]
    ok, f = D.agreement(GOLD, dict(GOLD, need_by="2026-11-07", decider=None, ready=True))
    assert not ok and [k for k, v in f.items() if not v] == ["need_by", "decider", "ready"]
    ok, f = D.agreement(GOLD, None)
    assert not ok and not any(f.values())
    ok, f = D.agreement(GOLD, dict(GOLD, ceiling_usd=None))
    assert not f["ceiling_usd"]


def test_judge_reads_blind_and_parses():
    s = line("S2")
    fc = FakeClient([raw(content=json.dumps(dict(s.gold.to_dict(), wait_weeks=float(s.gold.wait_weeks)
                                                 if s.gold.wait_weeks else None)))])
    read, err = D.judge(D.MeteredClient(fc), s, "some text")
    assert err is None and read == s.gold.to_dict()
    prompt = json.dumps(fc.calls[0]["messages"])
    assert fc.calls[0]["model"] == D.JUDGE_MODEL and fc.calls[0]["params"]["temperature"] == 0.0
    for x in (s.seed, "gold"):
        assert x not in prompt
    read, err = D.judge(D.MeteredClient(FakeClient([raw(content="nope")])), s, "t")
    assert read is None and err.startswith("unreadable")


# ---- spend guard -------------------------------------------------------------------------------------------------------
def test_spend_guard_stops_new_work(tmp_path):
    ledger = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 100, "b2": 100})
    ledger.settle("b2", 5.0, "before")
    g = D.SpendGuard(ledger, "b2", 1.20)
    assert not g.exceeded()
    ledger.settle("b2", 1.0, "x")
    ledger.settle("c1", 9.0, "other tag")
    assert not g.exceeded()
    ledger.settle("b2", 0.3, "y")
    assert g.exceeded()


def test_run_offline_writes_lines_and_summary(tmp_path):
    ledger = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 100, "b2": 100})
    n = 6
    rows = D.plan(n, "seed-run")
    script = []
    for i, (st, stop) in enumerate(rows):  # workers=1 keeps the script in order
        s = D.sample_line(CARDS, "dev", "seed-run", i, st, stop)
        script += [ok_reply(fine(s)),
                   raw(content=json.dumps(s.gold.to_dict()))]
    client = D.MeteredClient(FakeClient(script))
    lines, summ = D.run("dev", n, "seed-run", client, D.SpendGuard(ledger, "b2", 1.2), tmp_path, CARDS, workers=1)
    assert len(lines) == n and summ["written"] == n and summ["overall"]["fidelity_agree"] == 1.0
    assert [json.loads(x)["id"] for x in (tmp_path / "dev.jsonl").read_text().splitlines()] == [f"dev-{i:04d}"
                                                                                               for i in range(1, n + 1)]
    assert summ["sha256"] == D.sha256(tmp_path / "dev.jsonl")


def test_run_skips_lines_once_guard_trips(tmp_path):
    ledger = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 100, "b2": 100})
    g = D.SpendGuard(ledger, "b2", 1.2)
    ledger.settle("b2", 2.0, "spent elsewhere")
    lines, summ = D.run("dev", 4, "s", D.MeteredClient(FakeClient([])), g, tmp_path, CARDS, workers=1)
    assert lines == [] and len(summ["skipped_by_spend_guard"]) == 4


# ---- the test split's guard --------------------------------------------------------------------------------------------
def git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], check=True,
                   capture_output=True)


def test_test_command_refuses_without_committed_freeze(tmp_path):
    root = tmp_path
    git(root, "init", "-q")
    d = root / "data" / "second_read"
    d.mkdir(parents=True)
    (d / "dev.jsonl").write_text('{"id": "dev-0001"}\n')
    ok, why = D.freeze_ok(root)
    assert not ok and "does not exist" in why
    (d / "FREEZE.json").write_text(json.dumps({"dev_sha256": D.sha256(d / "dev.jsonl")}))
    ok, why = D.freeze_ok(root)
    assert not ok and "not committed" in why
    git(root, "add", ".")
    ok, why = D.freeze_ok(root)
    assert not ok and "differs" in why  # staged but not committed
    git(root, "commit", "-qm", "freeze")
    assert D.freeze_ok(root) == (True, "ok")
    (d / "dev.jsonl").write_text('{"id": "dev-0001", "x": 1}\n')
    ok, why = D.freeze_ok(root)
    assert not ok and "sha256" in why
    (d / "dev.jsonl").write_text('{"id": "dev-0001"}\n')
    (d / "FREEZE.json").write_text(json.dumps({"dev_sha256": "0" * 64}))
    ok, why = D.freeze_ok(root)
    assert not ok and "differs" in why
    fc = FakeClient([])
    assert D.main(["test"], client=fc, ledger=tf.Ledger(tmp_path / "l.jsonl"), root=root) == 2
    assert fc.calls == []


def test_repo_test_split_is_not_written():
    assert not (D.DATA / "test.jsonl").exists()
