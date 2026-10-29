"""The owner's morning: picks pinned, conversations built from the frozen v4 lines, offline recording with fakes."""
import importlib.util, json

import pytest

from fakes import FakeClient
from read_first_fakes import facts, text

from second_ask import models as M
from second_ask.compliance import opt_out_status
from read_first import morning as MO

ROOT = MO.ROOT
spec = importlib.util.spec_from_file_location("record_read_first_m", ROOT / "tools" / "record_read_first.py")
REC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REC)

PINNED = [
    ("test-0168", "Nemotron 3 Super's frozen v4 read missed this reply (first by line id; Super missed 6 of 600)"),
    ("test-0248", "the v4 rulebook's read missed this reply (first by line id; the rulebook missed 4 of 600)"),
    ("test-0027", 'asks the shop to stop without the word "stop" (first by line id; 24 such replies in the split)'),
    ("test-0001", "next line by id (S4: Spanish or Spanglish)"),
    ("test-0002", "next line by id (S1: one fact in plain English)"),
    ("test-0003", "next line by id (S1: one fact in plain English)"),
    ("test-0004", "next line by id (S2: two or three facts)"),
    ("test-0006", "first S3 line by id (paraphrase (item without its name, amounts in words, relative dates)), so "
                  "every stratum is in the morning"),
]


def test_the_eight_picks_and_their_reasons_are_pinned():
    inbox = MO.inbox()
    assert [(x["id"], x["reason"]) for x in inbox] == PINNED
    assert {x["stratum"] for x in inbox} == {"S1", "S2", "S3", "S4"}
    assert inbox[0]["v4"]["super"]["match"] is False and inbox[1]["v4"]["rules"]["match"] is False
    assert inbox[2]["case"]["gold"]["stop"] and "stop" not in inbox[2]["case"]["text"].lower()


def test_each_conversation_is_the_cards_estimate_and_the_reply_as_the_latest_message():
    for x in MO.inbox():
        conv, now, case = x["conv"], x["now"], x["case"]
        assert conv.estimate.id == case["card_id"] and conv.last_customer_text() == case["text"]
        assert now.date().isoformat() == case["today"] and (now.date() - conv.estimate.sent_at.date()).days >= 1
        assert conv.events[-1].at < now and conv.offer.price == conv.estimate.list_price()
        assert x["v4"]["perfect_read_offer"] == x["v4"]["super"]["offer"] or not x["v4"]["super"]["match"]


def _case(i, stratum, stop=False, text="ok"):
    return {"id": f"t-{i:02d}", "stratum": stratum, "text": text, "gold": {"stop": stop}}


def test_the_rule_on_synthetic_lines():
    cases = [_case(1, "S1"), _case(2, "S1", True, "please STOP"), _case(3, "S2", True, "no more texts"),
             _case(4, "S1"), _case(5, "S1"), _case(6, "S1"), _case(7, "S1"), _case(8, "S1"), _case(9, "S3"),
             _case(10, "S1"), _case(11, "S4")]
    rows = {(c["id"], arm): {"match": not ((c["id"], arm) in {("t-05", "super"), ("t-05", "rules"), ("t-07", "rules")})}
            for c in cases for arm in ("super", "rules")}
    picks = MO.pick(cases, rows)
    assert [c["id"] for c, _ in picks] == ["t-05", "t-07", "t-03", "t-01", "t-02", "t-04", "t-09", "t-11"]
    assert picks[0][1].startswith("Nemotron 3 Super's") and picks[1][1].startswith("the v4 rulebook's")
    assert picks[6][1].startswith("first S3") and picks[7][1].startswith("first S4")


def test_duplicate_rows_are_refused(tmp_path):
    p = tmp_path / "rows.jsonl"
    p.write_text('{"id": "a", "arm": "super"}\n{"id": "a", "arm": "super"}\n')
    with pytest.raises(ValueError, match="second row"):
        MO.load_rows(p)


def script_for(inbox):
    """Fake replies: the gold facts for each line the reader sees, then a plain text for the writer."""
    out = []
    for x in inbox:
        case = x["case"]
        if opt_out_status(case["text"]) == "opt_out":
            continue  # stopped by code before any read
        if case["gold"]["stop"]:
            out.append(facts(stop=True))
            continue
        out += [facts(**case["gold"]), text("Thanks for writing back. Here is what we can do.")]
    return out


def test_morning_recording_offline(tmp_path):
    inbox = MO.inbox()
    fc = FakeClient(script_for(inbox))
    out = tmp_path / "morning.json"
    data = REC.main(["--morning", "--ceiling", "0.10"], client=fc, morning_out=out)
    assert json.loads(out.read_text()) == json.loads(json.dumps(data))
    runs = data["conversations"]
    assert data["ledger_tag"] == "b2" and data["stopped"] is None and [r["id"] for r in runs] == [i for i, _ in PINNED]
    assert [r["pick_reason"] for r in runs] == [r for _, r in PINNED]
    for r in runs:
        for k in ("id", "pick_reason", "estimate", "reply", "facts", "offer", "message", "gate", "needs_owner", "steps",
                  "stages", "v4", "usd", "seconds"):
            assert k in r or (k in ("message", "gate") and r["move"] is None if "move" in r else r["path"] == "stopped"), (r["id"], k)
        assert set(r["v4"]) == {"rules", "super", "perfect_read_offer", "true_facts"}
        assert all(set(s) >= {"step", "model", "seconds", "cost"} for s in r["steps"])
        assert r["sent"] is False  # the morning sends nothing and taps nothing
    by = {r["id"]: r for r in runs}
    assert by["test-0027"]["path"] == "stopped" and by["test-0027"]["steps"] == []          # code gate, no model
    assert by["test-0248"]["facts"]["stop"] and [s["step"] for s in by["test-0248"]["steps"]] == ["read", "decide"]
    assert by["test-0168"]["v4"]["super"]["match"] is False and by["test-0168"]["move"]["kind"] in ("offer_slot", "earlier_slot")
    assert by["test-0001"]["offer"]["price"] < by["test-0001"]["estimate"]["total"] and by["test-0001"]["needs_owner"]
    assert by["test-0003"]["move"]["id"] == "checkin_30" and by["test-0003"]["writer"]["template_used"] is False
    assert [s["model"] for s in by["test-0002"]["steps"]] == [M.SUPER, None, M.SUPER]
    s = data["summary"]
    offers = [r for r in runs if r.get("move") and r.get("message")]
    assert s == {"replies_overnight": 8, "offers_ready": len(offers),
                 "waiting_for_owner_tap": sum(r["needs_owner"] for r in offers),
                 "ready_to_send_without_tap": sum(not r["needs_owner"] for r in offers),
                 "stop_requests_honoured": 2, "blocked_by_compliance": 0, "messages_from_template": 0,
                 "total_usd": round(sum(r["usd"] for r in runs), 6), "total_seconds": round(sum(r["seconds"] for r in runs), 3)}
    assert s["offers_ready"] == 6 and s["total_usd"] == data["spent_usd"] > 0 and len(fc.calls) == 13


def test_morning_ceiling_and_arguments(tmp_path):
    fc = FakeClient([])
    data = REC.main(["--morning", "--ceiling", "0.001"], client=fc, morning_out=tmp_path / "m.json")
    assert data["conversations"] == [] and data["stopped"].startswith("ceiling") and fc.calls == []
    assert data["summary"]["replies_overnight"] == 0
    with pytest.raises(SystemExit):
        REC.main(["--morning", "--ceiling", "1", "--card", "B002=hi"], client=FakeClient([]), morning_out=tmp_path / "x.json")
