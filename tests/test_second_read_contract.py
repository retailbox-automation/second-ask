"""The Facts contract both readers must satisfy."""
import pytest

from second_read.contract import SCHEMA, Facts, FactsError, parse

OPT = ["Surge protector", "Smart thermostat"]


def test_parse_valid_and_normalises_items():
    f = parse({"stop": False, "ceiling_usd": 500, "drop_items": ["surge PROTECTOR", "Not an item"], "need_by": "2026-11-06",
               "wait_weeks": None, "decider": " my wife ", "ready": False}, OPT)
    assert f == Facts(False, 500.0, ("Surge protector",), "2026-11-06", None, "my wife", False)
    assert f.to_dict()["drop_items"] == ["Surge protector"]


@pytest.mark.parametrize("bad", [
    {"stop": "yes"}, {"ceiling_usd": -5}, {"ceiling_usd": True}, {"need_by": "Nov 6"}, {"need_by": "2026-02-30"},
    {"wait_weeks": 0}, {"wait_weeks": 2.5}, {"decider": ""}, {"drop_items": "surge"}, {"price": 1}, [],
])
def test_parse_rejects(bad):
    with pytest.raises(FactsError):
        parse(bad, OPT)


def test_schema_matches_fields():
    assert set(SCHEMA["required"]) == set(SCHEMA["properties"]) == set(Facts.__dataclass_fields__)
