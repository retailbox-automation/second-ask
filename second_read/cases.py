"""Second Read (v4) lines ("cases") and the buyer cards they point to.

A case is one JSON line of data/second_read/<split>.jsonl:
  {"id", "split", "stratum": "S1|S2|S3|S4", "card_id", "today": "YYYY-MM-DD", "weekday", "quoted_slot": "YYYY-MM-DD",
   "text", "gold": {Facts dict}, "writer": {...}, "fidelity": {...}}
The estimate itself comes from tournament/buyers.json via card_id (Card.estimate(), Card.open_slots()).
"""
import json
from functools import lru_cache
from pathlib import Path

from second_ask.tournament import buyers as B

ROOT = Path(__file__).resolve().parents[1]
CARDS_FILE = ROOT / "tournament" / "buyers.json"


@lru_cache(maxsize=4)
def _cards(path):
    return {c.id: c for c in B.load(path)}


def cards(path=CARDS_FILE):
    return _cards(str(path))


def card_for(case, path=CARDS_FILE):
    try:
        return cards(path)[case["card_id"]]
    except KeyError as e:
        raise KeyError(f"case {case.get('id')}: unknown card {case.get('card_id')!r}") from e


def load(path):
    """Cases of one split file, in file order. A line that is not JSON raises: a split file is never partly read."""
    out = []
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{n}: not JSON ({e})") from e
    ids = [c["id"] for c in out]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate line ids")
    return out
