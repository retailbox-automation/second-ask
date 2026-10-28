"""v4 harness: freeze, the refusals of smoke/full, the row runner (resume, ceiling, re-runs, Ultra limit)."""
import json, os, shutil, subprocess, threading, time
from pathlib import Path

import pytest

from second_ask.tf_client import BudgetExceeded
from second_read import prereg as P, run as R
from second_read.contract import Facts

REPO = Path(__file__).resolve().parents[1]
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
CASE = {"id": "D0001", "split": "dev", "stratum": "S1", "card_id": "B001", "today": "2026-11-04", "weekday": "Wednesday",
        "quoted_slot": "2026-11-10", "text": "When can you come?", "gold": {"ready": True}}


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=GIT_ENV)


def commit_all(root, msg):
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", msg)


@pytest.fixture
def root(tmp_path):
    shutil.copytree(REPO / "second_read", tmp_path / "second_read", ignore=shutil.ignore_patterns("__pycache__", "rules.py"))
    (tmp_path / "second_read/readers/rules.py").write_text("class RuleReader:\n    name = 'rules'\n")
    (tmp_path / "tournament").mkdir()
    shutil.copy(REPO / "tournament/buyers.json", tmp_path / "tournament/buyers.json")
    (tmp_path / "data/second_read").mkdir(parents=True)
    (tmp_path / "data/second_read/dev.jsonl").write_text(json.dumps(CASE) + "\n")
    git(tmp_path, "init", "-q")
    commit_all(tmp_path, "readers")
    return tmp_path


def freeze_and_commit(root):
    d = R.cmd_freeze(root, log=lambda *_: None)
    commit_all(root, "freeze")
    return d


def test_freeze_records_everything_and_is_stable(root):
    rec = R.freeze_record(root)
    assert "second_read/readers/rules.py" in rec["files"] and "second_read/readers/nemotron.py" in rec["files"]
    assert "second_read/offer.py" in rec["files"] and not any("__pycache__" in f for f in rec["files"])
    assert rec["decision_rule"]["text"] == P.DECISION_RULE and rec["dev_data"]["lines"] == 1
    assert rec["test_split"] == {"seed": P.TEST_SEED, "n": P.TEST_N, "strata_weights": P.STRATA_WEIGHTS,
                                 "stop_share": P.STOP_SHARE}
    assert rec["spend"]["v4_ceiling_usd"] == 8.0 and rec["spend"]["ledger_tag"] == "b2"
    assert rec["arms"]["super"]["method"] == "tool" and rec["arms"]["ultra"]["params"]["tool_choice"] == "auto"
    assert set(rec["arms"]) == set(P.ARMS)
    assert R.freeze_record(root) == rec


def test_freeze_refuses_without_the_rule_reader_or_the_dev_split(root):
    (root / "second_read/readers/rules.py").unlink()
    with pytest.raises(R.Refused, match="rules.py"):
        R.freeze_record(root)
    (root / "second_read/readers/rules.py").write_text("")
    (root / "data/second_read/dev.jsonl").unlink()
    with pytest.raises(R.Refused, match="dev.jsonl"):
        R.freeze_record(root)


def test_verify_missing_uncommitted_committed(root):
    assert "does not exist" in R.verify_freeze(root)[1][0]
    R.cmd_freeze(root, log=lambda *_: None)
    _, problems = R.verify_freeze(root)
    assert problems == [f"{R.FREEZE_REL} is not committed (or differs from HEAD)"]
    commit_all(root, "freeze")
    assert R.verify_freeze(root)[1] == []
    assert R.guard(root, environ={}) is not None


@pytest.mark.parametrize("change", ["edit_reader", "edit_rules", "new_file", "edit_dev", "edit_prompt"])
def test_verify_refuses_any_change_after_the_freeze(root, change, monkeypatch):
    freeze_and_commit(root)
    if change == "edit_reader":
        p = root / "second_read/readers/nemotron.py"
        p.write_text(p.read_text() + "\n# x\n")
    elif change == "edit_rules":
        (root / "second_read/readers/rules.py").write_text("class RuleReader: pass\n")
    elif change == "new_file":
        (root / "second_read/readers/extra.py").write_text("")
    elif change == "edit_dev":
        (root / "data/second_read/dev.jsonl").write_text(json.dumps(dict(CASE, text="stop")) + "\n")
    else:
        monkeypatch.setattr(R.N, "SYSTEM_PROMPT", R.N.SYSTEM_PROMPT + " Be brief.")
    commit_all(root, "change")  # committed or not, a change after the freeze is refused
    _, problems = R.verify_freeze(root)
    assert problems and all(p.startswith("changed since the freeze") for p in problems)
    with pytest.raises(R.Refused, match="changed since the freeze"):
        R.guard(root, environ={})


def test_uncommitted_freeze_edit_is_refused(root):
    freeze_and_commit(root)
    fz = root / R.FREEZE_REL
    fz.write_text(fz.read_text().replace('"n": 600', '"n": 601'))
    _, problems = R.verify_freeze(root)
    assert any("not committed" in p for p in problems) and any("test_split.n" in p for p in problems)


@pytest.mark.parametrize("var", ["SA_SUPER", "SA_ANYTHING", "TF_BASE_URL", "TF_CAPS", "TF_LEDGER"])
def test_guard_refuses_env_overrides(root, var):
    freeze_and_commit(root)
    with pytest.raises(R.Refused, match=var):
        R.guard(root, environ={var: "x"})


@pytest.mark.parametrize("cmd", [["full"], ["full", "--split", "dev"], ["smoke"], ["smoke", "--n", "5", "--split", "dev"]])
def test_smoke_and_full_refuse_without_a_freeze(root, cmd, capsys):
    assert R.main(cmd, root=root, environ={}) == 2
    assert "FREEZE.json does not exist" in capsys.readouterr().err


def test_full_refuses_with_env_override_even_when_frozen(root, capsys):
    freeze_and_commit(root)
    assert R.main(["full", "--split", "dev"], root=root, environ={"TF_LEDGER": "/tmp/x"}) == 2
    assert "TF_LEDGER" in capsys.readouterr().err


def test_test_split_must_be_committed_after_the_freeze(root):
    test = root / "data/second_read/test.jsonl"
    test.write_text(json.dumps(dict(CASE, id="T0001", split="test")) + "\n")
    commit_all(root, "test split before the freeze")
    freeze_and_commit(root)
    with pytest.raises(R.Refused, match="not added after the freeze"):
        R.guard(root, environ={}, split="test")


def test_test_split_in_the_freeze_commit_is_refused_and_after_it_accepted(root):
    R.cmd_freeze(root, log=lambda *_: None)
    test = root / "data/second_read/test.jsonl"
    test.write_text(json.dumps(dict(CASE, id="T0001", split="test")) + "\n")
    commit_all(root, "freeze and test together")
    with pytest.raises(R.Refused, match="not added after the freeze"):
        R.guard(root, environ={}, split="test")


def test_test_split_accepted_after_the_freeze_but_not_uncommitted(root):
    freeze_and_commit(root)
    test = root / "data/second_read/test.jsonl"
    test.write_text(json.dumps(dict(CASE, id="T0001", split="test")) + "\n")
    with pytest.raises(R.Refused, match="not committed"):
        R.guard(root, environ={}, split="test")
    commit_all(root, "test split")
    assert R.guard(root, environ={}, split="test") is not None


def test_live_refuses_without_api_key(root, monkeypatch):
    freeze_and_commit(root)
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    with pytest.raises(R.Refused, match="NEBIUS_API_KEY"):
        R.live(root, "dev", "dev", environ={})


def test_decision_rule_is_verbatim_from_the_design():
    for base in Path(__file__).resolve().parents:
        f = base / "docs/2026-10-28-b2-rework/V4-DESIGN.md"
        if f.exists():
            text = f.read_text().split("## 6. Decision rule (pre-registered)")[1].split("\n## ")[0]
            assert " ".join(text.split()) == " ".join(P.DECISION_RULE.split())
            return
    pytest.skip("V4-DESIGN.md is not next to this checkout")


# ------------------------------------------------------------------ rows

class ScriptedReader:
    name = "fake"

    def __init__(self, results):
        self.results, self.calls = list(results), 0

    def read(self, case):
        self.calls += 1
        return self.results.pop(0)


def meta(kind=None, usd=0.001):
    return {"reader": "fake", "model": "m", "seconds": 0.5, "usd": usd, "attempts": [], "error": kind and "boom",
            "error_kind": kind}


def test_read_row_success_match_and_money():
    row = R.read_row(CASE, "super", ScriptedReader([(Facts(ready=True), meta())]), "dev")
    assert row["match"] and row["failure"] is None and row["runs"] == 1 and row["money"]["booked"]
    assert row["offer"] == row["gold_offer"] and row["facts"]["ready"] is True
    json.dumps(row)


def test_read_row_platform_error_rerun_twice_then_failure():
    rd = ScriptedReader([(None, meta("platform"))] * 3)
    row = R.read_row(CASE, "super", rd, "dev")
    assert rd.calls == 1 + P.PLATFORM_RERUNS and row["failure"] == "platform" and not row["match"]
    assert row["offer"] is None and row["usd"] == pytest.approx(0.003)


def test_read_row_platform_error_then_success():
    rd = ScriptedReader([(None, meta("platform")), (Facts(ready=True), meta())])
    row = R.read_row(CASE, "super", rd, "dev")
    assert rd.calls == 2 and row["match"] and row["runs"] == 2


def test_read_row_invalid_output_is_not_rerun():
    rd = ScriptedReader([(None, meta("invalid")), (Facts(ready=True), meta())])
    row = R.read_row(CASE, "super", rd, "dev")
    assert rd.calls == 1 and row["failure"] == "invalid" and not row["match"]


def test_read_row_failure_on_a_stop_line_is_a_missed_stop():
    row = R.read_row(dict(CASE, gold={"stop": True}), "super", ScriptedReader([(None, meta("invalid"))]), "dev")
    assert row["gold_stop"] and not row["read_stop"]


def fake_row(case, arm):
    return {"id": case["id"], "arm": arm, "failure": None}


def cases(n):
    return [dict(CASE, id=f"D{i:04d}") for i in range(n)]


def test_run_lines_writes_one_row_per_pair_and_resumes(tmp_path):
    jobs = [(c, a) for c in cases(5) for a in P.ARMS]
    rep = R.run_lines(jobs, fake_row, tmp_path, log=lambda *_: None)
    assert rep["stop"] == "complete" and rep["written"] == 25
    rows = R.read_rows(tmp_path / "rows.jsonl")
    assert len({(r["id"], r["arm"]) for r in rows}) == len(rows) == 25
    calls = []
    rep = R.run_lines(jobs, lambda c, a: calls.append(1) or fake_row(c, a), tmp_path, log=lambda *_: None)
    assert rep["written"] == 0 and rep["already_done"] == 25 and not calls


def test_run_lines_stops_starting_at_the_ceiling(tmp_path):
    spent = {"usd": 0.0}

    def read_one(c, a):
        spent["usd"] += 1.0
        return fake_row(c, a)

    jobs = [(c, "super") for c in cases(20)]
    rep = R.run_lines(jobs, read_one, tmp_path, concurrency=1, can_start=lambda arm: spent["usd"] + 1.0 <= 5.0,
                      log=lambda *_: None)
    assert rep["stop"] == "ceiling" and rep["written"] == 5


def test_run_lines_stops_on_budget_exceeded(tmp_path):
    def read_one(c, a):
        if c["id"] == "D0003":
            raise BudgetExceeded("cap")
        return fake_row(c, a)

    rep = R.run_lines([(c, "super") for c in cases(10)], read_one, tmp_path, concurrency=1, log=lambda *_: None)
    assert rep["stop"].startswith("budget") and rep["written"] == 3


def test_run_lines_reraises_a_bug_after_keeping_written_rows(tmp_path):
    def read_one(c, a):
        if c["id"] == "D0002":
            raise KeyError("bad data")
        return fake_row(c, a)

    with pytest.raises(KeyError):
        R.run_lines([(c, "super") for c in cases(10)], read_one, tmp_path, concurrency=1, log=lambda *_: None)
    assert len(R.read_rows(tmp_path / "rows.jsonl")) == 2


def test_run_lines_limits_ultra_concurrency(tmp_path):
    live = {"ultra": 0, "max_ultra": 0, "all": 0, "max_all": 0}
    lock = threading.Lock()

    def read_one(c, a):
        with lock:
            live["all"] += 1
            live["max_all"] = max(live["max_all"], live["all"])
            if a == "ultra":
                live["ultra"] += 1
                live["max_ultra"] = max(live["max_ultra"], live["ultra"])
        time.sleep(0.01)
        with lock:
            live["all"] -= 1
            if a == "ultra":
                live["ultra"] -= 1
        return fake_row(c, a)

    jobs = [(c, a) for c in cases(12) for a in ("ultra", "ultra", "super") if True]
    jobs = [(dict(c, id=f"{c['id']}-{k}"), a) for k, (c, a) in enumerate(jobs)]
    R.run_lines(jobs, read_one, tmp_path, concurrency=8, ultra_concurrency=3, log=lambda *_: None)
    assert live["max_ultra"] <= 3 and live["max_all"] <= 8 and live["max_all"] > 3


def test_spend_tracker_keeps_the_first_start(tmp_path):
    class L:
        path = "ledger"
        v = 2.0

        def spent(self):
            return 0.0, {"b2": self.v}

    led = L()
    s = R.spend_tracker(tmp_path, led)
    led.v = 2.5
    assert s() == pytest.approx(0.5)
    led.v = 3.0
    assert R.spend_tracker(tmp_path, led)() == pytest.approx(1.0)  # a later start still counts from the first


def test_worst_case_is_bounded_and_zero_for_rules():
    assert R.worst_case_usd("rules") == 0
    assert 0 < R.worst_case_usd("super") < R.worst_case_usd("ultra") < 0.1


def test_smoke_summary_has_no_match_rates():
    rows = [{"arm": "super", "failure": None, "usd": 0.001, "match": True}, {"arm": "rules", "failure": "invalid", "usd": 0}]
    out = R.mechanics(rows)
    assert out == {"super": {"rows": 1, "reader_failures": 0, "usd": 0.001}, "rules": {"rows": 1, "reader_failures": 1, "usd": 0.0}}
