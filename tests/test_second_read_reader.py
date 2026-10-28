"""Nemotron reader: one prompt, the same input as the rule reader, contract-validated output, one retry, no repair."""
import json

import pytest

from second_ask import models as M
from second_ask.tf_client import TFError
from second_read.contract import Facts
from second_read.readers import reader_input, render
from second_read.readers import nemotron as N
from tests.fakes import FakeClient, call, raw

# B001: AC capacitor and contactor replacement $890 + optional Surge protector $190; quoted slot Tue 2026-11-10
CASE = {"id": "D0001", "split": "dev", "stratum": "S2", "card_id": "B001", "today": "2026-11-04", "weekday": "Wednesday",
        "quoted_slot": "2026-11-10", "text": "Skip the surge thing and I'm in, but I need it done before Friday.",
        "gold": {}}
GOOD = {"stop": False, "ceiling_usd": None, "drop_items": ["surge protector"], "need_by": "2026-11-05",
        "wait_weeks": None, "decider": None, "ready": True}


def facts_call(obj):
    return raw(tool_calls=[call(N.TOOL_NAME, obj)])


def test_input_has_everything_and_only_the_contract_input():
    inp = reader_input(CASE)
    assert inp["items"] == [{"name": "AC capacitor and contactor replacement", "price": 890.0, "optional": False},
                            {"name": "Surge protector", "price": 190.0, "optional": True}]
    assert (inp["total"], inp["weekday"], inp["quoted_slot"], inp["quoted_weekday"]) == (1080.0, "Wednesday", "2026-11-10", "Tuesday")
    text = render(inp)
    for s in ("AC capacitor and contactor replacement: $890.00", "Surge protector: $190.00 (optional)", "Total: $1,080.00",
              "Today: Wednesday, 2026-11-04", "Quoted slot: Tuesday, 2026-11-10", CASE["text"]):
        assert s in text
    assert "gold" not in text.lower() and "S2" not in text


def test_input_refuses_inconsistent_case_data():
    with pytest.raises(ValueError, match="weekday"):
        reader_input(dict(CASE, weekday="Monday"))
    with pytest.raises(ValueError, match="quoted_slot"):
        reader_input(dict(CASE, quoted_slot="2026-11-09"))


def test_super_named_tool_reasoning_off_and_valid_facts():
    fc = FakeClient([facts_call(GOOD)])
    facts, meta = N.NemotronReader(M.SUPER, "tool", client=fc, name="super").read(CASE)
    assert facts == Facts(False, None, ("Surge protector",), "2026-11-05", None, None, True)
    p = fc.calls[0]["params"]
    assert p["tool_choice"] == {"type": "function", "function": {"name": "record_facts"}}
    assert p["reasoning_effort"] == "none" and p["tools"][0]["function"]["parameters"]["required"]
    assert fc.calls[0]["messages"][0]["content"].startswith(N.SYSTEM_PROMPT)
    assert fc.calls[0]["messages"][1]["content"] == render(reader_input(CASE))
    assert meta["error"] is None and meta["error_kind"] is None and len(meta["attempts"]) == 1 and meta["usd"] > 0
    assert meta["reader"] == "super" and meta["served_model"] == M.SUPER


def test_one_retry_on_invalid_output_then_success():
    fc = FakeClient([facts_call(dict(GOOD, need_by="Friday")), facts_call(GOOD)])
    facts, meta = N.NemotronReader(M.SUPER, "tool", client=fc).read(CASE)
    assert facts is not None and len(fc.calls) == 2
    assert meta["attempts"][0]["problem"].startswith("contract") and meta["attempts"][1]["problem"] is None


@pytest.mark.parametrize("bad", [
    facts_call(dict(GOOD, extra=1)),                       # unknown key
    facts_call(dict(GOOD, ceiling_usd="1,000")),           # wrong type
    raw(tool_calls=[call(N.TOOL_NAME, "{not json")]),      # arguments not JSON
    raw(content="sure, here are the facts"),               # no tool call
    raw(tool_calls=[call(N.TOOL_NAME, GOOD), call(N.TOOL_NAME, GOOD, cid="c2")]),  # two calls
])
def test_two_invalid_outputs_fail_the_line_without_repair(bad):
    fc = FakeClient([bad, bad])
    facts, meta = N.NemotronReader(M.SUPER, "tool", client=fc).read(CASE)
    assert facts is None and meta["error_kind"] == "invalid" and len(fc.calls) == 2 and meta["error"]


def test_platform_error_returns_at_once_for_the_harness_to_rerun():
    fc = FakeClient([TFError(503, "busy"), facts_call(GOOD)])
    facts, meta = N.NemotronReader(M.SUPER, "tool", client=fc).read(CASE)
    assert facts is None and meta["error_kind"] == "platform" and len(fc.calls) == 1


def test_lightning_json_object_with_fences():
    fc = FakeClient([raw(content="```json\n" + json.dumps(GOOD) + "\n```")])
    facts, _ = N.NemotronReader(M.LIGHTNING, "json_object", client=fc).read(CASE)
    assert facts.drop_items == ("Surge protector",)
    p = fc.calls[0]["params"]
    assert p["response_format"] == {"type": "json_object"} and "tools" not in p and p["reasoning_effort"] == "none"
    assert "exactly these keys" in fc.calls[0]["messages"][0]["content"]


def test_ultra_tools_with_reasoning_on():
    fc = FakeClient([facts_call(GOOD)])
    facts, _ = N.NemotronReader(M.ULTRA, "think_tools", client=fc).read(CASE)
    p = fc.calls[0]["params"]
    assert facts is not None and p["tool_choice"] == "auto"
    assert p["chat_template_kwargs"] == M.THINK_TOOLS["chat_template_kwargs"] and "reasoning_effort" not in p


def test_unknown_method_refused():
    with pytest.raises(ValueError):
        N.NemotronReader(M.SUPER, "json_schema")
