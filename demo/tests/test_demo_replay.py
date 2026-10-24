import json

from demo import replay
from second_ask.tournament import buyers as B, runner as R


def _raw_outcomes():
    """Every outcome line exactly as written by the tournament runner."""
    return [json.loads(l) for l in replay.OUTCOMES.read_text().splitlines() if l.strip()]


def test_stored_set_is_what_the_rule_picks():
    assert replay.load() == json.loads(json.dumps(replay.build()))


def test_each_replay_step_is_the_recorded_step_byte_for_byte():
    recorded = {(o["buyer"], o["policy"], o["repeat"]): o for o in _raw_outcomes()}
    for sc in replay.load()["scenarios"]:
        o = recorded[(sc["buyer"], sc["policy"], 0)]
        for field in ("transcript", "steps", "flags"):
            assert json.dumps(sc[field], sort_keys=True) == json.dumps(o[field], sort_keys=True), (sc["id"], field)
        assert all(s.get("response_id") for s in sc["steps"] if s["model"] and not s.get("platform_error"))


def test_the_rule():
    data = replay.load()
    scs = data["scenarios"]
    assert 6 <= len(scs) <= 8 and len({s["id"] for s in scs}) == len(scs)
    assert [s["policy"] for s in scs].count("super_only") == len(scs) - 1 and scs[-1]["id"] == "B139-routed"
    cards = B.load(replay.BUYERS)
    firsts = {s["type"]: s["buyer"] for s in scs[: len(replay.TYPES)]}
    assert set(firsts) == set(replay.TYPES)
    outs = {(o["buyer"], o["policy"]): o for o in _raw_outcomes() if o["repeat"] == 0}
    for typ, buyer in firsts.items():  # nothing earlier in buyers.json qualified
        for c in cards:
            if c.id == buyer:
                break
            if c.type == typ:
                o = outs[(c.id, "super_only")]
                assert not (any(s["model"] for s in o["steps"]) and any(e["who"] == "shop" for e in o["transcript"]))
    rows = {(r["buyer"], r["policy"]): r for r in R.read_lines(replay.ROWS)[0] if r["repeat"] == 0 and R.is_clean(r)}
    loss = scs[len(replay.TYPES)]
    gap = lambda b: rows[(b, "smart_rules")]["net"] - rows[(b, "super_only")]["net"]  # noqa: E731
    assert gap(loss["buyer"]) == max(gap(c.id) for c in cards if c.id not in firsts.values())
    b139 = scs[-1]
    assert "label model read an opt-out" in b139["result"]["end"] and b139["type"] != "opt_out"


def test_decision_groups_only_regroup_the_recorded_steps():
    for sc in replay.load()["scenarios"]:
        groups = replay.decisions(sc)
        assert [s for g in groups for s in g] == [s for s in sc["steps"] if s["step"] != "buyer"]
        assert all(g[0]["step"] == "classify" for g in groups)
