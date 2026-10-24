import io, json, os, subprocess, sys, threading, time, urllib.error, zlib
from dataclasses import replace

import pytest

from fakes import FakeClient
from second_ask import classifier as CL, models as M, tf_client as tf
from second_ask.trace import Trace
from second_ask.tournament import buyers as B, env as E, manifest as MF, policies as P, run as RUN, runner as R
from second_ask.tournament.policies import ALL, REPEATED, FakeSimClient, make


@pytest.fixture(scope="module")
def cards():
    return {c.id: c for c in B.generate(200)}


def outcome(card, policy, rep, cost=0.001, **kw):
    return E.Outcome(card.id, card.type, card.trade, policy, rep, False, None, [], "test", 1, cost, 1, 0.0, 0, 0, 0, 0,
                     **kw)


class Fake:
    """converse() stand-in. script: {(buyer, policy, repeat): [item per attempt]}; an item is an exception to raise or
    a dict of Outcome fields. on_call(n) runs before each call (n = calls so far, this one included)."""

    def __init__(self, script=None, cost=0.001, delay=0.0, on_call=None):
        self.script, self.cost, self.delay, self.on_call = script or {}, cost, delay, on_call
        self.calls, self.lock = [], threading.Lock()
        self.active, self.active_ultra, self.peak, self.peak_ultra = 0, 0, 0, 0

    def __call__(self, card, policy, rep):
        job = (card.id, policy, rep)
        with self.lock:
            self.calls.append(job)
            n_job = self.calls.count(job)
            self.active += 1
            self.active_ultra += policy == "ultra_only"
            self.peak, self.peak_ultra = max(self.peak, self.active), max(self.peak_ultra, self.active_ultra)
            n = len(self.calls)
        try:
            if self.on_call:
                self.on_call(n)
            if self.delay:
                time.sleep(self.delay)
            items = self.script.get(job, [])
            item = items[min(n_job, len(items)) - 1] if items else None
            if isinstance(item, BaseException):
                raise item
            return outcome(card, policy, rep, self.cost, **(item or {}))
        finally:
            with self.lock:
                self.active -= 1
                self.active_ultra -= policy == "ultra_only"


def small_plan(cards, n=6, policies=("shop_rule", "routed"), repeated=("routed",), k=2, sub=3):
    ids = list(cards)[:n]
    return R.plan(ids, list(policies), list(repeated), k, ids[:sub])


def test_plan_is_buyer_by_buyer_with_repeats_on_the_subsample(cards):
    ids = list(cards)
    jobs = R.plan(ids, list(ALL), list(REPEATED), 3, ids[::2])
    assert len(jobs) == 200 * len(ALL) + 2 * 100 * len(REPEATED)
    order = [b for b, _, _ in jobs]
    assert [b for i, b in enumerate(order) if i == 0 or order[i - 1] != b] == ids  # each buyer's jobs are contiguous
    assert not [j for j in jobs if j[2] > 0 and (j[1] not in REPEATED or j[0] not in ids[::2])]


def test_each_conversation_is_on_disk_before_the_next_and_a_restart_skips_it(cards, tmp_path):
    jobs = small_plan(cards)  # 6 buyers x 2 policies + 3 repeats = 15

    def on_disk(n):  # concurrency 1: every earlier conversation is already a line in rows.jsonl
        assert len(R.read_lines(tmp_path / "rows.jsonl")[0]) == n - 1
        if n == 8:
            raise tf.BudgetExceeded("cap")

    first = R.run(jobs, cards, Fake(on_call=on_disk), tmp_path, concurrency=1, log=lambda m: None)
    rows, bad = R.read_lines(tmp_path / "rows.jsonl")
    assert first["stop"].startswith("budget cap") and len(rows) == 7 and bad == 0
    assert len(R.read_lines(tmp_path / "outcomes.jsonl")[0]) == 7  # every outcome kept, with its steps
    again = Fake()
    second = R.run(jobs, cards, again, tmp_path, concurrency=1, log=lambda m: None)
    rows, _ = R.read_lines(tmp_path / "rows.jsonl")
    assert second["stop"] == "complete" and len(again.calls) == 8 and sorted(map(R.key, rows)) == sorted(jobs)
    assert again.calls == [j for j in jobs if j not in set(map(R.key, rows[:7]))]  # the rest, in plan order


def test_a_failing_conversation_is_an_errored_row_rerun_then_its_buyer_is_excluded(cards, tmp_path):
    jobs = small_plan(cards)
    bad_buyer = jobs[0][0]
    fake = Fake({(bad_buyer, "routed", 0): [ValueError("parser blew up")]})
    report = R.run(jobs, cards, fake, tmp_path, concurrency=1, log=lambda m: None)
    rows, _ = R.read_lines(tmp_path / "rows.jsonl")
    errored = [r for r in rows if r["status"] == "errored"]
    assert [r["attempt"] for r in errored] == [1, 2, 3] and all("parser blew up" in r["error"] for r in errored)
    assert report["excluded_buyers"] == [bad_buyer] and (bad_buyer, "routed", 1) not in fake.calls
    final, info = R.final_rows(rows, jobs)
    assert bad_buyer not in {r["buyer"] for r in final} and info["excluded"] == [bad_buyer]  # paired: every policy
    assert len(final) == len([j for j in jobs if j[0] != bad_buyer])


def test_a_platform_failure_is_rerun_and_a_clean_retry_counts(cards, tmp_path):
    jobs = small_plan(cards)
    b = jobs[0][0]
    fake = Fake({(b, "routed", 0): [R.PlatformFailure("Token Factory error 503"), {}]})
    R.run(jobs, cards, fake, tmp_path, concurrency=1, log=lambda m: None)
    rows, _ = R.read_lines(tmp_path / "rows.jsonl")
    first, second = [r for r in rows if R.key(r) == (b, "routed", 0)]
    assert first["status"] == "errored" and first["platform_failures"] == 1 and R.is_clean(second)
    final, info = R.final_rows(rows, jobs)
    assert len(final) == len(jobs) and info["reruns"] == 1 and info["errored_rows"] == 1


def test_an_off_arm_call_makes_a_conversation_unclean(cards, tmp_path):
    jobs = small_plan(cards)
    b = jobs[1][0]
    R.run(jobs, cards, Fake({(b, "routed", 0): [{"off_arm_calls": 1}]}), tmp_path, concurrency=1, log=lambda m: None)
    rows, _ = R.read_lines(tmp_path / "rows.jsonl")
    assert [r["off_arm_calls"] for r in rows if R.key(r) == (b, "routed", 0)] == [1, 1, 1]
    assert b in R.final_rows(rows, jobs)[1]["excluded"]


def test_checkpoint_stops_when_the_projection_passes_the_limit(cards, tmp_path):
    jobs = small_plan(cards, n=10, k=1)  # 20 conversations at $0.50
    fake = Fake(cost=0.5)
    spent = lambda: sum(r.get("model_usd", 0) for r in R.read_lines(tmp_path / "rows.jsonl")[0])  # noqa: E731
    cp = {"after_buyers": 2, "safety": 1.0, "limit_usd": 3.0}
    report = R.run(jobs, cards, fake, tmp_path, concurrency=1, checkpoint=cp, spent=spent, log=lambda m: None)
    check = json.loads((tmp_path / "checkpoint.json").read_text())
    assert report["stop"].startswith("checkpoint") and not check["passed"] and len(fake.calls) == 4
    assert check["projected_total_usd"] == pytest.approx(2.0 + 16 * 0.5)
    again = Fake(cost=0.5)
    assert R.run(jobs, cards, again, tmp_path, concurrency=1, checkpoint=cp, spent=spent,
                 log=lambda m: None)["stop"].startswith("checkpoint failed earlier") and again.calls == []
    done = R.run(jobs, cards, again, tmp_path, concurrency=1, checkpoint=cp | {"accept_failed": True}, spent=spent,
                 log=lambda m: None)
    assert done["stop"] == "complete" and len(again.calls) == 16


def test_a_passed_checkpoint_lets_the_run_finish(cards, tmp_path):
    jobs = small_plan(cards, n=10, k=1)
    cp = {"after_buyers": 2, "safety": 1.2, "limit_usd": 100.0}
    report = R.run(jobs, cards, Fake(cost=0.5), tmp_path, concurrency=1, checkpoint=cp, log=lambda m: None)
    assert report["stop"] == "complete" and json.loads((tmp_path / "checkpoint.json").read_text())["passed"]


def test_the_run_ceiling_stops_new_conversations(cards, tmp_path):
    jobs = small_plan(cards)
    spent = lambda: 0.001 * len(R.read_lines(tmp_path / "rows.jsonl")[0])  # noqa: E731
    report = R.run(jobs, cards, Fake(), tmp_path, concurrency=1, ceiling_usd=0.0045, spent=spent, log=lambda m: None)
    assert report["stop"].startswith("run ceiling") and report["rows"] == 5


def test_ultra_conversations_have_their_own_concurrency_limit(cards, tmp_path):
    ids = list(cards)[:15]
    jobs = R.plan(ids, ["ultra_only", "routed"], [], 1, [])
    fake = Fake(delay=0.03)
    R.run(jobs, cards, fake, tmp_path, concurrency=6, ultra_concurrency=2, log=lambda m: None)
    assert fake.peak_ultra == 2 and fake.peak <= 6 and len(fake.calls) == 30  # the limit binds and holds


def test_strict_client_turns_platform_errors_into_aborts():
    strict = R.StrictClient(FakeClient([tf.TFError(503, "busy")]))
    with pytest.raises(R.PlatformFailure):
        CL.classify(strict, M.SUPER, "state", Trace(sink=lambda _: None))  # not absorbed into a code default


def test_a_404_aborts_the_conversation_instead_of_switching_model(cards, tmp_path, monkeypatch):
    """MF5: no silent swap. Ultra answers 404; the tournament client must not try Super in its place."""
    asked = []

    def gone(req, timeout=None):
        asked.append(json.loads(req.data)["model"])
        raise urllib.error.HTTPError(req.full_url, 404, "gone", None, io.BytesIO(b'{"detail":"no such model"}'))

    monkeypatch.setenv("NEBIUS_API_KEY", "test-key")
    monkeypatch.setattr(tf.urllib.request, "urlopen", gone)
    m = MF.build("tournament/buyers.json", 200, 3, "all", M.QWEN3_30B, {}, {})
    assert m["platform"]["client"]["fallbacks"] == {}
    converse = RUN.live_conversations(m, tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "b2": 1.0}))
    channel = next(c for c in cards.values() if c.type == "channel")  # the shop speaks first: a label call on Ultra
    with pytest.raises(R.PlatformFailure):
        converse(channel, "ultra_only", 0)
    assert asked == [M.ULTRA]


@pytest.mark.parametrize("var", ["SA_ULTRA", "SA_BUYER", "TF_CAPS", "TF_LEDGER", "TF_BASE_URL"])
@pytest.mark.parametrize("cmd", ["full", "smoke"])
def test_live_commands_refuse_environment_overrides(monkeypatch, capsys, var, cmd):
    monkeypatch.setenv(var, "x")
    assert RUN.main([cmd, "--approve", "0" * 64]) == 2
    assert var in capsys.readouterr().out


def test_full_takes_no_run_parameters_from_the_command_line():
    with pytest.raises(SystemExit):
        RUN.main(["full", "--approve", "x", "--k", "1"])


def test_manifest_jobs_follow_the_manifest(cards):
    ids = list(cards)
    m = {"buyers": {"n": 200}, "policies": {p: {} for p in ALL}, "repeats": {"k": 2, "subsample": ids[:5],
                                                                            "repeated": ["routed"]}}
    jobs = RUN.manifest_jobs(m)
    assert len(jobs) == 200 * len(ALL) + 5 and {j[1] for j in jobs if j[2]} == {"routed"}


@pytest.fixture
def frozen(tmp_path):
    m = MF.build("tournament/buyers.json", 200, 3, sorted(list(B.load(MF.ROOT / "tournament/buyers.json"))[i].id
                                                           for i in range(0, 200, 2)), M.QWEN3_30B, {"note": "t"},
                 {"projected_usd": 1.0})
    path = tmp_path / "MANIFEST.json"
    return MF.write(m, path), path


def test_verify_rebuilds_every_section_from_the_live_code(frozen, monkeypatch):
    h, path = frozen
    assert MF.verify(path) == (h, [])
    monkeypatch.setitem(P.LLM_POLICIES, "ultra_only", replace(P.LLM_POLICIES["ultra_only"], chooser="super_think"))
    assert MF.verify(path)[1] == ["policy ultra_only differs from the live code (model, parameters or spec)"]


@pytest.mark.parametrize("attr,value", [("API", "https://elsewhere.example/v1"),
                                        ("DEFAULT_CAPS", {"total": 999.0, "b2": 999.0})])
def test_verify_catches_a_changed_endpoint_or_cap(frozen, monkeypatch, attr, value):
    h, path = frozen
    monkeypatch.setattr(tf, attr, value)
    assert MF.verify(path)[1] == ["platform differs from the live code"]


def test_an_env_model_swap_is_caught_in_a_fresh_process(frozen):
    """The acceptance probe: SA_ULTRA pointing at Super passed the old verify with no problems."""
    _, path = frozen
    env = dict(os.environ, SA_ULTRA=M.SUPER, PYTHONPATH=str(MF.ROOT))
    code = "import sys, pathlib; from second_ask.tournament import manifest as MF; print(MF.verify(pathlib.Path(sys.argv[1]))[1])"
    out = subprocess.run([sys.executable, "-c", code, str(path)], env=env, cwd=MF.ROOT, capture_output=True, text=True,
                         check=True).stdout
    assert "policy ultra_only differs" in out


def test_results_are_computed_from_rows_only(cards, tmp_path):
    ids = list(cards)[:28]
    jobs = R.plan(ids, list(ALL), list(REPEATED), 2, ids[:14])

    def converse(card, policy, rep):
        fc = FakeSimClient(seed=zlib.crc32(f"{policy}/{card.id}/{rep}".encode()))
        fc.card = card
        return E.run_one(card, make(policy, fc), E.TemplateWriter(), rep)

    assert R.run(jobs, cards, converse, tmp_path, concurrency=4, log=lambda m: None)["stop"] == "complete"
    rows, _ = R.read_lines(tmp_path / "rows.jsonl")
    res = RUN.results(rows, {"repeats": {"subsample": ids[:14]}}, cards, jobs)
    assert set(res["aggregate"]) == set(ALL) and res["kept"]["buyers_kept"] == 28
    by_hand = sum(sum(r["margin"] for r in rs) / len(rs) for rs in
                  [[r for r in rows if r["policy"] == "routed" and r["buyer"] == b] for b in ids])
    assert res["aggregate"]["routed"]["margin"] == pytest.approx(by_hand)
    assert "routed - smart_rules" in res["comparisons"] and "super_only - smart_rules" in res["comparisons"]
    assert res["aggregate"]["routed"]["band"]["n"] == 14 and res["aggregate"]["shop_rule"]["band"]["K"] == 1
    assert "label_default" in res and isinstance(res["losing_cells"], list)


def test_unsure_flags_are_counted_by_kind_on_kept_conversations():
    final = [{"buyer": "B1", "policy": "routed", "repeat": 0, "attempt": 2}]
    outs = [{"buyer": "B1", "policy": "routed", "repeat": 0, "attempt": 1, "flags": [{"flags": ["label failed x"]}]},
            {"buyer": "B1", "policy": "routed", "repeat": 0, "attempt": 2,
             "flags": [{"flags": ["label price with low confidence: x", "model's message replaced by the template: y"]},
                       {"flags": ["something new"]}]}]
    assert RUN.unsure_flags(outs, final) == {"routed": {"label with low confidence": 1,
                                                        "message replaced by the template": 1, "other": 1}}


def test_projection_uses_measured_costs_and_a_measured_proxy():
    dry = {p: {"seller_calls_per_conv": 4.0, "buyer_msgs_per_conv": 2.0} for p in ALL}
    step = {"step": "buyer", "model": M.QWEN3_30B, "cost": 0.0001}
    outs = [{"policy": "routed", "seller_cost": 0.002, "seller_calls": 10, "buyer_cost": 0.0003, "steps": [step]}] * 10
    outs += [{"policy": "super_only", "seller_cost": 0.003, "seller_calls": 6, "buyer_cost": 0.0002, "steps": [step]}]
    conv, _ = RUN.conversation_costs(outs, dry)
    assert conv["routed"]["usd"] == pytest.approx(0.0023) and "measured" in conv["routed"]["method"]
    assert conv["super_only"]["usd"] == pytest.approx(10 * 0.0005 + 0.0003)  # routed's 10 calls x super's $ per call
    assert conv["shop_rule"]["usd"] == pytest.approx(2.0 * 0.0001 * 1.1)    # no smoke: dry-run messages


def test_results_files_for_the_full_manifest_plan(cards, frozen, tmp_path):
    """End to end on the real plan (2,400 conversations, synthetic outcomes): write_results makes both files."""
    _, path = frozen
    m = json.loads(path.read_text())
    jobs = RUN.manifest_jobs(m)
    assert len(jobs) == 200 * len(ALL) + 2 * 100 * len(REPEATED)
    out = tmp_path / "full"
    R.run(jobs, cards, Fake(), out, concurrency=8, log=lambda msg: None)
    res = RUN.write_results(out, m)
    assert res["kept"]["buyers_kept"] == 200 and set(res["aggregate"]) == set(ALL)
    assert res["aggregate"]["routed"]["band"] == {**res["aggregate"]["routed"]["band"], "n": 100, "K": 3}
    text = (out / "RESULTS-tables.md").read_text()
    assert "| routed | 200 | 3 |" in text and "routed - smart_rules" in text
    assert json.loads((out / "results.json").read_text())["unsure_flags"] == {}


def test_project_reads_a_smoke_directory_given_as_a_relative_path(tmp_path, monkeypatch, capsys):
    step = {"step": "buyer", "model": M.QWEN3_30B, "cost": 0.00004}
    rec = {"policy": "routed", "seller_cost": 0.002, "seller_calls": 6, "buyer_cost": 0.0001, "steps": [step]}
    (tmp_path / "outcomes.jsonl").write_text("".join(json.dumps(rec) + "\n" for _ in range(10)))
    monkeypatch.chdir(tmp_path.parent)
    assert RUN.main(["project", "--smoke", tmp_path.name]) == 0
    proj = json.loads((tmp_path / "projection.json").read_text())
    assert "measured" in proj["per_conversation_usd"]["routed"]["method"] and proj["smoke"] == str(tmp_path.resolve())
