"""Second Read (v4) contract: the Facts a reader extracts from one customer reply.

Both readers (the hardened rulebook and Nemotron) get the same input and must return Facts in exactly this shape; the
shop's code then turns Facts into an offer (`offer.py`). The decision code is identical in both arms, so any difference
in the offer comes from reading alone. Design: docs/2026-10-28-b2-rework/V4-DESIGN.md in the campaign repo.
"""
import re
from dataclasses import asdict, dataclass, field
from datetime import date

FIELDS = ("stop", "ceiling_usd", "drop_items", "need_by", "wait_weeks", "decider", "ready")

SCHEMA = {
    "type": "object",
    "properties": {
        "stop": {"type": "boolean", "description": "The customer asks the shop to stop contacting them, in any words or language."},
        "ceiling_usd": {"type": ["number", "null"], "description": "The most the customer says they will pay, including a "
                        "competitor's all-in price they would book at; null if they name no amount."},
        "drop_items": {"type": "array", "items": {"type": "string"}, "description": "Exact names of OPTIONAL line items of this "
                       "estimate that the customer says they do not want or need."},
        "need_by": {"type": ["string", "null"], "description": "YYYY-MM-DD: the latest day the job must be done; null if none."},
        "wait_weeks": {"type": ["integer", "null"], "description": "They want the job, but about this many weeks from now; null if none."},
        "decider": {"type": ["string", "null"], "description": "Someone else must agree first (wife, husband, partner, landlord, "
                    "HOA, parent); null if none."},
        "ready": {"type": "boolean", "description": "They want to book now or ask when the crew can come."},
    },
    "required": list(FIELDS),
    "additionalProperties": False,
}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Facts:
    stop: bool = False
    ceiling_usd: float | None = None
    drop_items: tuple = field(default_factory=tuple)
    need_by: str | None = None
    wait_weeks: int | None = None
    decider: str | None = None
    ready: bool = False

    def to_dict(self):
        d = asdict(self)
        d["drop_items"] = list(self.drop_items)
        return d


class FactsError(ValueError):
    pass


def parse(obj, optional_items=None):
    """Validated Facts from a reader's raw dict. Unknown keys, wrong types or a bad date raise FactsError (a reader whose
    output cannot be validated has failed that line; it is never repaired silently). Drop items are kept only when they
    are optional items of this estimate (matched case-insensitively, returned with the estimate's spelling)."""
    if not isinstance(obj, dict):
        raise FactsError("not an object")
    extra = set(obj) - set(FIELDS)
    if extra:
        raise FactsError(f"unknown keys {sorted(extra)}")
    stop, ready = obj.get("stop", False), obj.get("ready", False)
    if not isinstance(stop, bool) or not isinstance(ready, bool):
        raise FactsError("stop and ready must be booleans")
    ceiling = obj.get("ceiling_usd")
    if ceiling is not None:
        if isinstance(ceiling, bool) or not isinstance(ceiling, (int, float)) or ceiling <= 0:
            raise FactsError(f"ceiling_usd {ceiling!r}")
        ceiling = float(ceiling)
    need_by = obj.get("need_by")
    if need_by is not None:
        if not isinstance(need_by, str) or not _DATE.match(need_by):
            raise FactsError(f"need_by {need_by!r}")
        try:
            date.fromisoformat(need_by)
        except ValueError as e:
            raise FactsError(f"need_by {need_by!r}") from e
    wait = obj.get("wait_weeks")
    if wait is not None and (isinstance(wait, bool) or not isinstance(wait, int) or wait < 1):
        raise FactsError(f"wait_weeks {wait!r}")
    decider = obj.get("decider")
    if decider is not None and (not isinstance(decider, str) or not decider.strip()):
        raise FactsError(f"decider {decider!r}")
    items = obj.get("drop_items") or []
    if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
        raise FactsError("drop_items must be a list of strings")
    if optional_items is not None:
        by_low = {n.lower(): n for n in optional_items}
        items = [by_low[x.strip().lower()] for x in items if x.strip().lower() in by_low]
    return Facts(stop, ceiling, tuple(sorted(set(items))), need_by, wait, decider.strip() if decider else None, ready)
