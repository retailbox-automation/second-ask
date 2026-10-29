"""tools/record_read_first.py offline: fake client, ceiling, ledger tag, output shape."""
import importlib.util, json

import pytest

from fakes import FakeClient, call, raw
from read_first_fakes import facts, text

from second_ask import models as M, tf_client as tf

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("record_read_first", ROOT / "tools" / "record_read_first.py")
REC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REC)


def test_records_reply_silence_and_card_runs_with_every_step(tmp_path):
    fc = FakeClient([facts(), text("The $10,400 covers the new AC and air handler, the thermostat and the purifier."),
                     facts(ready=True), text("Great, we can book you.")])
    out = tmp_path / "runs.json"
    data = REC.main(["--ceiling", "1", "--scenario", "B003-super_only", "--scenario", "B011-super_only",
                     "--card", "B002=This looks good. When can you come?", "--out", str(out)], client=fc)
    assert json.loads(out.read_text()) == json.loads(json.dumps(data))
    assert data["ledger_tag"] == "b2" and data["stopped"] is None and len(data["runs"]) == 3
    price, silence, ready = data["runs"]
    assert price["reply"] == "This is more than I expected. Thanks for the estimate." and price["path"] == "reply"
    assert price["facts"]["ceiling_usd"] is None and price["offer"]["price"] == 10400.0 and price["move"]["id"] == "answer"
    assert [s["stage"] for s in price["stages"]] == ["read", "decide", "write", "owner"]
    assert [(s["step"], s["model"]) for s in price["steps"]] == [("read", M.SUPER), ("decide", None), ("write", M.SUPER)]
    assert all(s["seconds"] == 0.5 and s["usd"] > 0 for s in price["stages"] if s["model"])
    assert price["gate"]["ok"] and price["sent"] and price["usd"] == round(sum(s["cost"] for s in price["steps"]), 6)
    assert silence["path"] == "silence" and silence["reply"] is None and silence["usd"] == 0
    assert [s["model"] for s in silence["steps"]] == [None] and silence["move"]["kind"] == "resend"
    assert ready["buyer"] == "B002" and ready["move"]["kind"] in ("offer_slot", "earlier_slot")
    assert ready["offer"]["timing"]["kind"] == "slot" and ready["move"]["slot"].startswith(ready["offer"]["timing"]["day"])
    assert len(fc.calls) == 4 and data["spent_usd"] == round(price["usd"] + ready["usd"], 6)


def test_ceiling_below_one_runs_worst_case_starts_nothing(tmp_path):
    fc = FakeClient([])
    data = REC.main(["--ceiling", "0.001", "--scenario", "B003-super_only", "--out", str(tmp_path / "r.json")], client=fc)
    assert data["runs"] == [] and data["stopped"].startswith("ceiling") and fc.calls == []


def test_ceiling_is_checked_against_the_spend_before_each_run():
    items = REC.items_from(["B003-super_only", "B003-super_only"])
    worst = REC.worst_case_usd()
    spent = iter([0.0, worst * 1.5, worst * 1.5])
    fc = FakeClient([facts(), text("The $10,400 covers it.")])
    data = REC.run(items, fc, worst * 2, spent=lambda: next(spent))
    assert len(data["runs"]) == 1 and "1 run(s) not started" in data["stopped"]


def test_ledger_cap_stops_the_recording():
    fc = FakeClient([tf.BudgetExceeded("[b2] cap")])
    data = REC.run(REC.items_from(["B003-super_only"]), fc, 1.0)
    assert data["runs"] == [] and data["stopped"].startswith("ledger cap")


def test_live_client_uses_the_b2_ledger_tag(tmp_path, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "test-not-a-key")
    c = REC.make_client(tf.Ledger(tmp_path / "ledger.jsonl", caps={"total": 1.0, "b2": 0.5}))
    assert c.tag == "b2" and c.ledger.caps["b2"] == 0.5 and c.fallbacks == {}


def test_book_runs_the_super_tool_loop_after_yes(tmp_path):
    probe = REC.run(REC.items_from(cards=["B002=When can you come?"]), FakeClient([facts(ready=True), text("We can book you.")]), 1.0)
    slot = probe["runs"][0]["move"]["slot"]
    from second_ask.tournament import buyers as B
    card = {c.id: c for c in B.load(REC.BUYERS)}["B002"]
    slot_id = next(s.id for s in card.open_slots() if s.start.isoformat() == slot)
    fc = FakeClient([facts(ready=True), text("We can book you."),
                     raw(tool_calls=[call("book_job", {"slot_id": slot_id, "estimate_id": "B002", "price": card.estimate().list_price()})]),
                     raw(tool_calls=[call("record_job", {"job_id": "J-B002-1", "note": "booked"})])])
    data = REC.main(["--ceiling", "1", "--card", "B002=When can you come?", "--book", "--out", str(tmp_path / "b.json")], client=fc)
    run = data["runs"][0]
    assert run["job"]["recorded"] and run["job"]["slot_id"] == slot_id
    assert [s["stage"] for s in run["stages"]] == ["read", "decide", "write", "owner", "book"]
    assert data["models"]["book"] == M.SUPER and data["worst_case_per_run_usd"] > REC.worst_case_usd()


def test_bad_arguments_are_refused(tmp_path):
    with pytest.raises(SystemExit):
        REC.main(["--ceiling", "1", "--scenario", "nope", "--out", str(tmp_path / "x.json")], client=FakeClient([]))
    with pytest.raises(SystemExit):
        REC.main(["--ceiling", "0", "--card", "B002=hi", "--out", str(tmp_path / "x.json")], client=FakeClient([]))
    with pytest.raises(SystemExit):
        REC.main(["--ceiling", "1", "--card", "B002", "--out", str(tmp_path / "x.json")], client=FakeClient([]))
