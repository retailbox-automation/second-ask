"""Pre-registered analysis on synthetic rows: each verdict, the missed-stop condition, withheld verdicts, the report."""
import json, random

import pytest

from second_read import analysis as A, prereg as P, run as R

MONEY = {"booked": False, "margin": 0.0, "given_away": 0.0, "gained": 0.0, "lost": 0.0, "message_after_stop": False}


def make(n, super_hits, rules_hits, stops=(), super_missed=(), rules_missed=(), strata=("S1", "S2", "S3", "S4"),
         fidelity=None):
    """n lines; *_hits: ids (ints) where that arm matches; stops: gold-stop lines; *_missed: stop lines read as no stop."""
    cases, rows = [], []
    for i in range(n):
        cid = f"T{i:04d}"
        c = {"id": cid, "stratum": strata[i % len(strata)], "card_id": "B001", "today": "2026-11-04",
             "gold": {"stop": i in stops}}
        if fidelity is not None:
            c["fidelity"] = {"agree": fidelity(i)}
        cases.append(c)
        for arm, hits, missed in (("super", super_hits, super_missed), ("rules", rules_hits, rules_missed),
                                  ("lightning", rules_hits, ())):
            gold_stop = i in stops
            rows.append({"id": cid, "arm": arm, "stratum": c["stratum"], "match": i in hits, "gold_stop": gold_stop,
                         "read_stop": gold_stop and i not in missed, "failure": None, "money": dict(MONEY),
                         "usd": 0.001 if arm != "rules" else 0.0, "seconds": 0.4})
    return cases, rows


def test_verdict_function_boundaries():
    assert A.verdict(10.0, 0.1, 20, 0, 0) == P.BEATS
    assert A.verdict(9.99, 0.1, 20, 0, 0) == P.UNCLEAR
    assert A.verdict(15, 0.0, 30, 0, 0) == P.UNCLEAR
    assert A.verdict(15, 2, 30, 2, 1) == P.UNCLEAR
    assert A.verdict(-3, -6, -0.1, 0, 0) == P.BEHIND
    assert A.verdict(-3, -6, 0.0, 0, 0) == P.UNCLEAR


def test_beats():
    cases, rows = make(600, set(range(480)), set(range(360)))
    res = A.analyze(cases, rows, "test")
    p = res["primary"]
    assert p["diff_pp"] == 20.0 and p["lo_pp"] > 0 and p["complete"] and p["n"] == 600
    assert res["verdict"] == P.BEATS


def test_behind():
    cases, rows = make(600, set(range(240)), set(range(420)))
    res = A.analyze(cases, rows, "test")
    assert res["primary"]["hi_pp"] < 0 and res["verdict"] == P.BEHIND


def test_no_clear_difference_small_gain():
    rng = random.Random(1)
    base = set(rng.sample(range(600), 360))
    sup = base | set(rng.sample(sorted(set(range(600)) - base), 30))  # +5 pp
    res = A.analyze(*make(600, sup, base), "test")
    assert res["primary"]["diff_pp"] == 5.0 and res["primary"]["lo_pp"] > 0 and res["verdict"] == P.UNCLEAR


def test_no_clear_difference_large_but_noisy():
    rng = random.Random(2)
    sup = set(rng.sample(range(40), 26))
    rules = set(rng.sample(range(40), 21))
    res = A.analyze(*make(40, sup, rules), "test")
    assert res["primary"]["diff_pp"] >= 10 and res["primary"]["lo_pp"] <= 0 and res["verdict"] == P.UNCLEAR


def test_missed_stop_excess_blocks_beats():
    stops = set(range(560, 600))
    cases, rows = make(600, set(range(480)), set(range(360)), stops=stops, super_missed={560, 561, 562},
                       rules_missed={570, 571})
    res = A.analyze(cases, rows, "test")
    assert res["primary"]["diff_pp"] == 20.0 and res["primary"]["lo_pp"] > 0
    assert (res["arms"]["super"]["missed_stops"], res["arms"]["rules"]["missed_stops"]) == (3, 2)
    assert res["verdict"] == P.UNCLEAR
    cases, rows = make(600, set(range(480)), set(range(360)), stops=stops, super_missed={560, 561},
                       rules_missed={570, 571})
    assert A.analyze(cases, rows, "test")["verdict"] == P.BEATS  # equal counts do not block


def test_false_stops_and_failures_counted():
    cases, rows = make(10, set(), set())
    rows[0].update(read_stop=True)  # super, line 0, gold no stop
    rows[3].update(failure="invalid")  # super line 1
    res = A.analyze(cases, rows, "test")
    assert res["arms"]["super"]["false_stops"] == 1 and res["arms"]["super"]["reader_failures"] == 1
    assert res["arms"]["super"]["failures_by_kind"] == {"invalid": 1, "platform": 0}


def test_incomplete_run_withholds_the_verdict():
    cases, rows = make(600, set(range(480)), set(range(360)))
    rows = [r for r in rows if not (r["arm"] == "super" and r["id"] == "T0007")]
    res = A.analyze(cases, rows, "test")
    assert res["verdict"] is None and "incomplete" in res["verdict_withheld"] and res["arms"]["super"]["missing_lines"] == 1


def test_per_stratum_cost_latency_fidelity_and_sensitivity():
    cases, rows = make(400, set(range(0, 400, 2)) | set(range(1, 400, 4)), set(range(0, 400, 2)),
                       fidelity=lambda i: i % 5 != 0)
    res = A.analyze(cases, rows, "test")
    assert set(res["per_stratum"]) == {"S1", "S2", "S3", "S4"} and all(v["n"] == 100 for v in res["per_stratum"].values())
    assert res["arms"]["super"]["usd_per_1000"] == 1.0 and res["arms"]["super"]["p50_seconds"] == 0.4
    assert res["fidelity"]["agreement_pct"] == 80.0 and res["fidelity"]["lines_with_judge"] == 400
    assert res["sensitivity_judge_agreed"]["n"] == 320


def test_fidelity_from_judge_facts():
    case = {"id": "x", "card_id": "B001", "today": "2026-11-04", "gold": {"ready": True},
            "fidelity": {"facts": {"ready": True, "decider": "my wife"}}}
    assert A.fidelity_agree(case) is True  # same offer (ready beats decider)
    assert A.fidelity_agree(dict(case, fidelity={"facts": {"wait_weeks": 2}})) is False
    assert A.fidelity_agree(dict(case, fidelity={"facts": {"stop": "maybe"}})) is False
    assert A.fidelity_agree(dict(case, fidelity={})) is None


def test_cmd_analyze_writes_report_and_json(tmp_path):
    cases, rows = make(200, set(range(150)), set(range(100)))
    d = tmp_path / R.DATA_REL
    (d / "test").mkdir(parents=True)
    (d / "test.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases))
    (d / "test/rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    res = R.cmd_analyze(tmp_path, "test", log=lambda *_: None)
    md = (d / "test/V4-RESULTS.md").read_text()
    assert res["verdict"] == P.BEATS and "Verdict: beats" in md and P.DECISION_RULE in md
    assert json.loads((d / "test/results.json").read_text())["primary"]["diff_pp"] == 25.0


def test_cmd_analyze_refuses_without_rows(tmp_path):
    (tmp_path / R.DATA_REL).mkdir(parents=True)
    (tmp_path / R.DATA_REL / "test.jsonl").write_text("")
    with pytest.raises(R.Refused):
        R.cmd_analyze(tmp_path, "test", log=lambda *_: None)
