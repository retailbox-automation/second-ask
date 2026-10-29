"""Record the read-first path (code decides the price, Nemotron reads the customer) on demo estimates, live.

    python3.14 tools/record_read_first.py --ceiling 0.50 \
        --scenario B003-super_only --scenario "B002-super_only=Looks good, when can you come?" \
        --card "B007=The most I can pay is $360 and my landlord has to ok it" [--book] [--out demo/data/read_first_runs.json]

--scenario ID[=TEXT]  an estimate from demo/data/replay.json; without TEXT, the customer's first recorded message (and
                      its day); a scenario whose customer never wrote is recorded as a silence (code only).
--card ID=TEXT        any buyer card of tournament/buyers.json; an empty TEXT is a silence.
--ceiling USD         required: no run starts once the spend so far plus that run's worst case would pass it.
--book                after a sent offer that proposes booking, the customer answers YES and the Super tool loop books.

Spend goes through the shared ledger with the tag "b2" (second_ask.tf_client: its own cap holds as well). Every step is
written: model, seconds, $ (from the reply), the read Facts, the offer, the move, the message and the gate result.
The owner's tap is the stub (approves any concession at or above the floor), as in the demo.
"""
import argparse, json, sys, time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from second_ask.agent import Conversation, Event, OwnerTapStub  # noqa: E402
from second_ask.compliance import Recipient  # noqa: E402
from second_ask.tf_client import PRICES, BudgetExceeded, Client, Ledger  # noqa: E402
from second_ask.trace import Trace  # noqa: E402
from second_ask.tournament import buyers as B  # noqa: E402
from read_first.agent import ReadFirstAgent, ReadFirstConfig  # noqa: E402
from read_first import writer as W  # noqa: E402

TAG = "b2"
REPLAY = ROOT / "demo" / "data" / "replay.json"
BUYERS = ROOT / "tournament" / "buyers.json"
OUT = ROOT / "demo" / "data" / "read_first_runs.json"
DEFAULT_DAY = 1 + 1 / 24  # the next morning after the estimate (09:00 + 1 h New York), inside messaging hours
BOOK_ROUNDS = 8


def _call_usd(model, input_tokens, max_tokens):
    p_in, p_out = PRICES[model]
    return (input_tokens * p_in + max_tokens * p_out) / 1e6


def worst_case_usd(cfg=ReadFirstConfig(), book=False):
    """A generous bound on one run: two read calls (the reader's one retry), one write call, and the booking loop."""
    usd = 2 * _call_usd(cfg.reader_model, 4000, 1024) + _call_usd(cfg.writer_model, 4000, W.PARAMS["max_tokens"])
    if book:
        usd += BOOK_ROUNDS * _call_usd(cfg.booker_model, 6000, 12000)
    return round(usd, 6)


def items_from(scenarios=(), cards=(), replay_path=REPLAY):
    """[{id, buyer, text, day}] from --scenario and --card arguments."""
    replay = {s["id"]: s for s in json.loads(Path(replay_path).read_text())["scenarios"]}
    out = []
    for arg in scenarios:
        sid, _, text = arg.partition("=")
        if sid not in replay:
            raise SystemExit(f"no scenario {sid!r} in {replay_path}")
        sc = replay[sid]
        first = next((e for e in sc["transcript"] if e["who"] == "customer"), None)
        if text:
            out.append({"id": sid, "buyer": sc["buyer"], "text": text, "day": first["day"] if first else DEFAULT_DAY})
        else:
            out.append({"id": sid, "buyer": sc["buyer"], "text": first["text"] if first else "",
                        "day": first["day"] if first else DEFAULT_DAY})
    for arg in cards:
        cid, sep, text = arg.partition("=")
        if not sep:
            raise SystemExit(f"--card needs ID=TEXT, got {arg!r}")
        out.append({"id": cid, "buyer": cid, "text": text, "day": DEFAULT_DAY})
    return out


def run_one(agent, card, item, book=False):
    est = card.estimate()
    conv = Conversation(est, Recipient(card.tz, card.state), card.open_slots(), trace=Trace(sink=lambda _: None))
    at = est.sent_at + timedelta(days=item["day"])
    if item["text"]:
        conv.events.append(Event(at, "customer", est.channel, item["text"]))
    now = at + timedelta(minutes=10)
    rd = agent.decide(conv, now)
    sent = agent.apply(conv, rd, OwnerTapStub())
    job = None
    if book and sent and rd.move.proposes_booking:
        later = now + timedelta(hours=1)
        conv.events.append(Event(later, "customer", rd.move.channel, "YES"))
        job = agent.book(conv, later, rd)
    rec = {"id": item["id"], "buyer": card.id, "reply": item["text"] or None, "now": now.isoformat(),
           "estimate": {"title": est.title, "items": [{"name": i.name, "price": i.price, "optional": i.optional}
                                                      for i in est.items],
                        "total": est.list_price(), "floor": est.floor(), "sent_by": est.channel},
           **rd.to_dict(), "sent": sent, "job": job, "offer_after": {"price": conv.offer.price,
                                                                      "dropped": sorted(conv.offer.dropped)},
           "steps": [asdict(s) for s in conv.trace.steps]}
    rec["seconds"] = round(sum(s.seconds for s in rd.stages), 3)
    rec["usd"] = round(sum(s.cost for s in conv.trace.steps), 6)
    return rec


def run(items, client, ceiling, book=False, cards=None, spent=None, cfg=ReadFirstConfig()):
    """Every item through the read-first path, stopping before a run whose worst case would pass `ceiling`.
    `spent()` returns the real spend so far on the ledger tag since the start (None: the sum of the recorded runs)."""
    cards = cards or {c.id: c for c in B.load(BUYERS)}
    agent = ReadFirstAgent(client, cfg)
    worst = worst_case_usd(cfg, book)
    runs, stopped = [], None
    for item in items:
        so_far = spent() if spent else sum(r["usd"] for r in runs)
        if so_far + worst > ceiling:
            stopped = (f"ceiling: ${so_far:.4f} spent + ${worst:.4f} worst case for the next run > ${ceiling:.2f}; "
                       f"{len(items) - len(runs)} run(s) not started")
            break
        if item["buyer"] not in cards:
            raise SystemExit(f"no buyer card {item['buyer']!r}")
        try:
            runs.append(run_one(agent, cards[item["buyer"]], item, book))
        except BudgetExceeded as e:
            stopped = f"ledger cap: {e}"
            break
    total = spent() if spent else sum(r["usd"] for r in runs)
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ledger_tag": TAG,
            "ceiling_usd": ceiling, "worst_case_per_run_usd": worst, "spent_usd": round(total, 6),
            "stopped": stopped, "models": {"read": cfg.reader_model, "write": cfg.writer_model,
                                           "book": cfg.booker_model if book else None},
            "owner": "OwnerTapStub: approves any concession at or above the floor (as in the demo)", "runs": runs}


def make_client(ledger=None):
    return Client(TAG, ledger=ledger or Ledger(), max_attempts=4, fallbacks={})


def main(argv=None, client=None, out=OUT, replay_path=REPLAY):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scenario", action="append", default=[])
    ap.add_argument("--card", action="append", default=[])
    ap.add_argument("--ceiling", type=float, required=True, help="USD for this recording")
    ap.add_argument("--book", action="store_true")
    ap.add_argument("--out", default=str(out))
    a = ap.parse_args(argv)
    if a.ceiling <= 0:
        raise SystemExit("--ceiling must be above 0")
    items = items_from(a.scenario, a.card, replay_path)
    if not items:
        raise SystemExit("nothing to record: give --scenario and/or --card")
    spent = None
    if client is None:
        client = make_client()
        start = client.ledger.spent()[1].get(TAG, 0.0)
        spent = lambda: client.ledger.spent()[1].get(TAG, 0.0) - start  # noqa: E731
    t0 = time.time()
    data = run(items, client, a.ceiling, a.book, spent=spent)
    data["wall_seconds"] = round(time.time() - t0, 1)
    Path(a.out).write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {a.out}: {len(data['runs'])} run(s), ${data['spent_usd']:.4f} on {TAG}"
          + (f"; stopped: {data['stopped']}" if data["stopped"] else ""))
    return data


if __name__ == "__main__":
    main()
