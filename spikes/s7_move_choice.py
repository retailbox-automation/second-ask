"""S7: move choice, Ultra vs Super vs Super-VL on the 20 states, K = 3 (pre-registered in PLAN.md).

The chooser gets each state's gold primary label (confidence "low" only for the ambiguous S20), so this measures the
move choice alone; label errors are measured separately in S3 and end to end in the tournament.
"""
import json, statistics

from common import EVIDENCE, run, spent
from states import all_states
from second_ask import chooser as CH, classifier as CL, menu as MN
from second_ask.classifier import Label

K = 3
CONFIGS = ["super_nothink", "super_think", "ultra_think", "super_vl_nothink", "super_vl_think"]
STATES = all_states()

jobs = []
for name in CONFIGS:
    model, params = CH.CONFIGS[name]
    for s in STATES:
        conv = s["conv"]
        last = next((e.text for e in reversed(conv.events) if e.who == "customer"), None)
        label = Label(s["gold_labels"][0], "low" if s["id"] == "S20" else "high",
                      last or f"no reply; estimate opened {conv.views} times", None, "gold", True)
        state_text = CL.render_state(conv.estimate, conv.events, s["now"], conv.views)
        messages, tools = CH.request(conv.estimate.shop, state_text, label, s["menu"], conv.estimate, conv.offer)
        for k in range(K):
            jobs.append({"id": f"{s['id']}#{k}", "model": model, "messages": messages,
                         "params": {**params, "tools": tools, "tool_choice": "auto"},
                         "meta": {"config": name, "state": s["id"], "k": k, "gold": s["gold_moves"]}})
recs = run("s7_move_choice", jobs, workers=8)
by_state = {s["id"]: s for s in STATES}


def score(rec):
    """(valid, correct, message problems) for one call."""
    if not rec["ok"]:
        return False, False, ["HTTP error"]
    calls = [tc for tc in rec["tool_calls"] or [] if tc["function"]["name"] == "choose_move"]
    try:
        args = json.loads(calls[0]["function"]["arguments"] or "{}") if calls else {}
    except json.JSONDecodeError:
        return False, False, ["arguments not JSON"]
    s = by_state[rec["meta"]["state"]]
    move = {m.id: m for m in s["menu"]}.get(args.get("move_id"))
    if move is None:
        return False, False, [f"invalid {args.get('move_id')!r}"]
    return True, move.id in rec["meta"]["gold"], MN.check_message(str(args.get("message") or ""), move, s["conv"].estimate, s["conv"].offer)


summary = {}
print("\n| Config | correct (mean of K) | per repeat | valid | message replaced | mean s | mean reasoning | $ per call |")
print("|---|---|---|---|---|---|---|---|")
for name in CONFIGS:
    rs = [r for r in recs if r["meta"]["config"] == name]
    sc = [score(r) for r in rs]
    per_k = [sum(c for r, (_, c, _) in zip(rs, sc) if r["meta"]["k"] == k) / len(STATES) for k in range(K)]
    ok = [r for r in rs if r["ok"]]
    summary[name] = statistics.mean(per_k)
    print(f"| {name} | {statistics.mean(per_k):.3f} | {', '.join(f'{x:.2f}' for x in per_k)} | "
          f"{sum(v for v, _, _ in sc)}/{len(rs)} | {sum(bool(p) for v, _, p in sc if v)} | "
          f"{sum(r['seconds'] for r in ok) / max(1, len(ok)):.2f} | "
          f"{sum(r['reasoning_tokens'] for r in ok) / max(1, len(ok)):.0f} | {sum(r['cost'] for r in ok) / max(1, len(ok)):.5f} |")

print("\nPer state (correct of K):")
for s in STATES:
    cells = []
    for name in CONFIGS:
        rs = [r for r in recs if r["meta"]["config"] == name and r["meta"]["state"] == s["id"]]
        picks = []
        for r in rs:
            calls = [tc for tc in r.get("tool_calls") or [] if tc["function"]["name"] == "choose_move"]
            try:
                picks.append(json.loads(calls[0]["function"]["arguments"]).get("move_id") if calls else "-")
            except json.JSONDecodeError:
                picks.append("?")
        cells.append(f"{name}={sum(score(r)[1] for r in rs)} {picks}")
    print(f"  {s['id']} gold={s['gold_moves']}: " + " | ".join(cells))

rule1 = "super_think" if summary["super_think"] - summary["super_nothink"] > 0.05 else "super_nothink"
rule2 = "ultra_think" if summary["ultra_think"] - summary[rule1] > 0.05 else rule1
rule3 = "super_vl_think" if summary["super_vl_think"] - summary["super_vl_nothink"] > 0.05 else "super_vl_nothink"
decision = {"means": summary, "super_config": rule1, "price_move_model": rule2, "super_vl_config": rule3}
(EVIDENCE / "s7_move_choice" / "decision.json").write_text(json.dumps(decision, indent=1))
print(f"\nRule 1 (Super config): {rule1}\nRule 2 (price move): {rule2}\nRule 3 (Super-VL config): {rule3}")
print(spent())
