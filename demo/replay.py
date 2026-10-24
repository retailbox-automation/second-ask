"""REPLAY: real recorded tournament conversations, picked by a rule written here, served byte for byte.

    python3 -m demo.replay build      tournament/full/outcomes.jsonl -> demo/data/replay.json
    python3 -m demo.replay check      the stored set equals what the rule picks from the tournament files

The rule (no hand-picking), all from the v1 run (manifest 1c231403), repeat 0:
1. For each objection type except opt_out, the first customer in tournament/buyers.json order whose super_only
   conversation (the product as shipped: Super labels and chooses) has at least one model step and one shop message.
   Opt-out customers are left out: their words come from code and no model is called.
2. The customer where super_only's net falls furthest below smart_rules' net (a loss of the product against the
   rulebook), ties by customer id; super_only. If that customer is already picked under 1, the next one.
3. B139, routed: the published false opt-out (the Lightning label read "I'll think about it and get back to you." as a
   request to stop).
Each scenario keeps the conversation exactly as recorded (transcript and steps: text, model, served_model, seconds,
cost, response_id), plus the scored row (booked, price, net, oracle, end) and the estimate the customer saw.
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from second_ask.tournament import buyers as B, runner as R  # noqa: E402

OUTCOMES = ROOT / "tournament" / "full" / "outcomes.jsonl"
ROWS = ROOT / "tournament" / "full" / "rows.jsonl"
BUYERS = ROOT / "tournament" / "buyers.json"
STORE = Path(__file__).resolve().parent / "data" / "replay.json"
TYPES = ("price", "timing_pause", "timing_slot", "family", "channel", "ready")
PRODUCT, FALSE_OPT_OUT = "super_only", ("B139", "routed")


def _clean_first(records):
    """{(buyer, policy, repeat): first clean record} (rows) or the outcome of that attempt (outcomes)."""
    out = {}
    for r in records:
        k = (r["buyer"], r["policy"], r["repeat"])
        if k not in out and (r.get("status", "ok") == "ok") and not r.get("platform_failures") and not r.get("off_arm_calls"):
            out[k] = r
    return out


def pick(cards, outcomes, rows):
    """The scenario keys (buyer, policy, why), by the rule in the module docstring."""
    outs, scored = _clean_first(outcomes), _clean_first(rows)
    picked = []
    for typ in TYPES:
        for c in (c for c in cards if c.type == typ):
            o = outs.get((c.id, PRODUCT, 0))
            if o and any(s["model"] for s in o["steps"]) and any(e["who"] == "shop" for e in o["transcript"]):
                picked.append((c.id, PRODUCT, f"first {typ} customer with a model step and a shop message"))
                break
    gaps = sorted(((scored[(c.id, "smart_rules", 0)]["net"] - scored[(c.id, PRODUCT, 0)]["net"], c.id) for c in cards
                   if (c.id, PRODUCT, 0) in scored and (c.id, "smart_rules", 0) in scored), key=lambda x: (-x[0], x[1]))
    taken = {b for b, _, _ in picked}
    gap, loser = next((g, b) for g, b in gaps if b not in taken)
    picked.append((loser, PRODUCT, f"largest shortfall of super_only against smart_rules (net ${gap:,.2f})"))
    picked.append((*FALSE_OPT_OUT, "the published false opt-out"))
    return picked


def build(cards=None, outcomes=None, rows=None):
    cards = cards or B.load(BUYERS)
    outcomes = outcomes if outcomes is not None else R.read_lines(OUTCOMES)[0]
    rows = rows if rows is not None else R.read_lines(ROWS)[0]
    outs, scored, by_id = _clean_first(outcomes), _clean_first(rows), {c.id: c for c in cards}
    scenarios = []
    for buyer, policy, why in pick(cards, outcomes, rows):
        o, r, c = outs[(buyer, policy, 0)], scored[(buyer, policy, 0)], by_id[buyer]
        est = c.estimate()
        scenarios.append({
            "id": f"{buyer}-{policy}", "buyer": buyer, "policy": policy, "type": c.type, "trade": c.trade, "why": why,
            "estimate": {"shop": est.shop, "title": est.title, "sent_by": est.channel,
                         "items": [{"name": i.name, "price": i.price, "optional": i.optional} for i in est.items],
                         "total": est.list_price()},
            "result": {k: r[k] for k in ("booked", "price", "end", "margin", "net", "oracle", "model_usd", "buyer_usd")},
            "transcript": o["transcript"], "steps": o["steps"], "flags": o["flags"]})
    return {"source": {"outcomes": str(OUTCOMES.relative_to(ROOT)), "rows": str(ROWS.relative_to(ROOT)),
                       "manifest_sha256": "1c231403c8fd5e1109ad1bad04ad411e0234be49e6f5c04a129260347c233a1e"},
            "rule": __doc__.split("The rule", 1)[1].split("Each scenario", 1)[0].strip(), "scenarios": scenarios}


def load(path=STORE):
    return json.loads(Path(path).read_text())


def decisions(scenario):
    """The recorded shop-side steps grouped per decision (classify attempts, then choose attempts), in order. Grouping
    only: flattening the groups gives the recorded steps back unchanged."""
    groups, cur = [], []
    for s in scenario["steps"]:
        if s["step"] == "buyer":
            continue
        if s["step"] == "classify" and cur and cur[-1]["step"] != "classify":
            groups.append(cur)
            cur = []
        cur.append(s)
    return groups + ([cur] if cur else [])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    data = build()
    if cmd == "build":
        STORE.write_text(json.dumps(data, indent=1) + "\n")
        print(f"wrote {STORE.relative_to(ROOT)}: " + ", ".join(f"{s['id']} ({s['why']})" for s in data["scenarios"]))
    else:
        same = load() == json.loads(json.dumps(data))
        print("stored replay set matches the rule:", same)
        sys.exit(0 if same else 1)
