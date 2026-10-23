"""S2: the 14.10 write_fact spike, re-measured at NVIDIA's recommended parameters (pre-registered in PLAN.md)."""
import json
from common import M, run, spent

TOOLS = [{"type": "function", "function": {
    "name": "write_fact", "description": "Propose a fact for the user's memory graph. Must include a source.",
    "parameters": {"type": "object", "properties": {"subject": {"type": "string"}, "relation": {"type": "string"},
                                                     "object": {"type": "string"}, "source": {"type": "string"}},
                   "required": ["subject", "relation", "object", "source"]}}}]
MSG = [{"role": "system", "content": "You extract durable personal facts from the user's message and call write_fact for "
                                     "each. Use source='user message 2026-10-14'."},
       {"role": "user", "content": "I moved to Austin last week, and my dentist is now Dr. Lee."}]
CONFIGS = {
    "super_think": (M.SUPER, {"max_tokens": 12000, **M.NONEMPTY}),
    "lightning_think": (M.LIGHTNING, {"max_tokens": 16000, **M.NONEMPTY}),
    "lightning_off": (M.LIGHTNING, {"max_tokens": 1024, **M.REASONING_OFF}),
}
jobs = [{"id": f"{name}#{rep}", "model": model, "messages": MSG, "meta": {"config": name},
         "params": {**M.SAMPLING, "tools": TOOLS, "tool_choice": "auto", **extra}}
        for name, (model, extra) in CONFIGS.items() for rep in range(5)]


def facts(rec):
    found = set()
    for tc in rec.get("tool_calls") or []:
        if tc["function"]["name"] != "write_fact":
            continue
        try:
            obj = str(json.loads(tc["function"]["arguments"] or "{}").get("object", "")).lower()
        except json.JSONDecodeError:
            continue
        found |= {k for k in ("austin", "lee") if k in obj}
    return len(found)


recs = run("s2_remeasure_1410", jobs, workers=5)
print("\n| Config | Model | facts (of 10) | per call | mean s | mean reasoning tokens | $ total |\n|---|---|---|---|---|---|---|")
for name, (model, _) in CONFIGS.items():
    rs = [r for r in recs if r["meta"]["config"] == name]
    ok = [r for r in rs if r["ok"]]
    print(f"| {name} | `{model}` | {sum(facts(r) for r in rs)}/10 | {[facts(r) for r in rs]} | "
          f"{sum(r['seconds'] for r in ok) / max(1, len(ok)):.2f} | "
          f"{sum(r['reasoning_tokens'] for r in ok) / max(1, len(ok)):.0f} | {sum(r['cost'] for r in ok):.5f} |")
print(spent())
