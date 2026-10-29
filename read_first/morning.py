"""The shop owner's morning: eight customer replies that came in overnight, from the frozen v4 test split.

Every reply is one of the 600 frozen v4 test lines (data/second_read/test.jsonl); the comparison with the two v4 reads
comes from that run's rows (data/second_read/test/rows.jsonl). Each conversation is the line's buyer card: its estimate
was sent at the cards' common start (Mon 2026-11-02, 09:00 New York), some days before the line's `today`; the reply
arrived the evening before at 23:00 New York and the owner opens the inbox at 07:00 New York on `today`.

The rule (applied by `pick`, from the two files alone; nothing is hand-picked):
1. the first line by id where the frozen v4 Super read missed (rows: arm "super", match false);
2. the first line by id, not yet picked, where the v4 rulebook read missed (arm "rules", match false);
3. the first line by id, not yet picked, that asks the shop to stop (gold stop) without the word "stop";
4. five more lines in line-id order, not yet picked: first, for each stratum S1-S4 (in that order) that the picks do
   not cover yet, the first line of that stratum; then the next lines by id until there are eight.
Each pick carries the reason that put it there.
"""
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from second_ask.agent import Conversation, Event
from second_ask.compliance import Recipient
from second_ask.trace import Trace
from second_read import cases as C

ROOT = Path(__file__).resolve().parents[1]
TEST_SPLIT = ROOT / "data" / "second_read" / "test.jsonl"
ROWS = ROOT / "data" / "second_read" / "test" / "rows.jsonl"
N = 8
STRATA = {"S1": "one fact in plain English", "S2": "two or three facts", "S3": "paraphrase (item without its name, "
          "amounts in words, relative dates)", "S4": "Spanish or Spanglish"}
REPLY_UTC = time(4, 0)     # 23:00 New York (EST) the evening before
MORNING_UTC = time(12, 0)  # 07:00 New York (EST): the owner opens the inbox


def load_rows(path=ROWS):
    """{(line id, arm): row}. A second row for the same pair would make the comparison ambiguous: refused."""
    out = {}
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        r = json.loads(line)
        key = (r["id"], r["arm"])
        if key in out:
            raise ValueError(f"{path}:{n}: second row for {key}")
        out[key] = r
    return out


def _first(cases, taken, test):
    return next((c for c in cases if c["id"] not in taken and test(c)), None)


def pick(cases, rows):
    """[(case, reason)] by the rule in the module docstring."""
    cases = sorted(cases, key=lambda c: c["id"])
    picks, taken = [], set()

    def add(case, reason):
        if case is not None:
            picks.append((case, reason))
            taken.add(case["id"])

    def missed(arm):
        return lambda c: (c["id"], arm) in rows and rows[(c["id"], arm)]["match"] is False

    n_super = sum(1 for c in cases if missed("super")(c))
    add(_first(cases, taken, missed("super")),
        f"Nemotron 3 Super's frozen v4 read missed this reply (first by line id; Super missed {n_super} of {len(cases)})")
    n_rules = sum(1 for c in cases if missed("rules")(c))
    add(_first(cases, taken, missed("rules")),
        f"the v4 rulebook's read missed this reply (first by line id; the rulebook missed {n_rules} of {len(cases)})")
    no_word = lambda c: c["gold"]["stop"] and "stop" not in c["text"].lower()  # noqa: E731
    n_stop = sum(1 for c in cases if no_word(c))
    add(_first(cases, taken, no_word),
        f"asks the shop to stop without the word \"stop\" (first by line id; {n_stop} such replies in the split)")
    rest = []
    for s in STRATA:
        if not any(c["stratum"] == s for c, _ in picks + rest):
            c = _first(cases, taken | {x["id"] for x, _ in rest}, lambda c, s=s: c["stratum"] == s)
            if c is not None:
                rest.append((c, f"first {s} line by id ({STRATA[s]}), so every stratum is in the morning"))
    while len(picks) + len(rest) < N:
        c = _first(cases, taken | {x["id"] for x, _ in rest}, lambda c: True)
        if c is None:
            break
        rest.append((c, f"next line by id ({c['stratum']}: {STRATA[c['stratum']]})"))
    for c, reason in sorted(rest, key=lambda x: x[0]["id"]):
        add(c, reason)
    return picks


def conversation(case, card=None):
    """(Conversation, now) for one line: the card's estimate, the reply as the customer's latest message."""
    card = card or C.card_for(case)
    est = card.estimate()
    day = datetime.fromisoformat(case["today"]).date()
    now = datetime.combine(day, MORNING_UTC, tzinfo=timezone.utc)
    reply_at = datetime.combine(day, REPLY_UTC, tzinfo=timezone.utc)
    if not est.sent_at < reply_at < now:
        raise ValueError(f"{case['id']}: estimate sent {est.sent_at}, reply {reply_at}, morning {now}")
    conv = Conversation(est, Recipient(card.tz, card.state), card.open_slots(), trace=Trace(sink=lambda _: None))
    conv.events.append(Event(reply_at, "customer", est.channel, case["text"]))
    return conv, now


def v4_reads(case, rows):
    """The two frozen v4 reads of this line and the offer a perfect read gives, for the comparison panel."""
    out = {}
    for arm in ("rules", "super"):
        r = rows[(case["id"], arm)]
        out[arm] = {"facts": r["facts"], "offer": r["offer"], "match": r["match"], "model": r["meta"].get("model"),
                    "seconds": r["seconds"], "usd": r["usd"], "failure": r["failure"]}
    out["perfect_read_offer"] = rows[(case["id"], "super")]["gold_offer"]
    out["true_facts"] = case["gold"]
    return out


def inbox(split=TEST_SPLIT, rows_path=ROWS):
    """[{id, reason, stratum, case, conv, now, v4}] for the morning, in pick order."""
    cases, rows = C.load(split), load_rows(rows_path)
    out = []
    for case, reason in pick(cases, rows):
        conv, now = conversation(case)
        out.append({"id": case["id"], "reason": reason, "stratum": case["stratum"], "case": case, "conv": conv,
                    "now": now, "v4": v4_reads(case, rows)})
    return out


def summary(runs):
    """Counts from the recorded runs only."""
    offers = [r for r in runs if r.get("move") and r.get("message")]
    return {"replies_overnight": len(runs),
            "offers_ready": len(offers),
            "waiting_for_owner_tap": sum(1 for r in offers if r["needs_owner"]),
            "ready_to_send_without_tap": sum(1 for r in offers if not r["needs_owner"]),
            "stop_requests_honoured": sum(1 for r in runs if r.get("opted_out") and not r.get("move")),
            "blocked_by_compliance": sum(1 for r in runs if r.get("move") and not r.get("message")),
            "messages_from_template": sum(1 for r in runs if (r.get("writer") or {}).get("template_used")),
            "total_usd": round(sum(r["usd"] for r in runs), 6),
            "total_seconds": round(sum(r["seconds"] for r in runs), 3)}
