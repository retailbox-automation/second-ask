"""S4: first step of the booking loop with reasoning on, 20 calls per model (pre-registered in PLAN.md)."""
import json
from common import M, run, spent

TOOLS = [
    {"type": "function", "function": {"name": "get_shop_hours", "description": "The shop's working hours and time zone.",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "find_slots", "description": "Open crew slots on or after a date.",
        "parameters": {"type": "object", "properties": {
            "earliest_date": {"type": "string", "description": "YYYY-MM-DD"},
            "duration_hours": {"type": "number"}}, "required": ["earliest_date", "duration_hours"]}}},
    {"type": "function", "function": {
        "name": "book_job", "description": "Book a crew slot for an accepted estimate.",
        "parameters": {"type": "object", "properties": {
            "slot_id": {"type": "string"}, "estimate_id": {"type": "string"}, "price": {"type": "number"}},
            "required": ["slot_id", "estimate_id", "price"]}}},
    {"type": "function", "function": {
        "name": "record_job", "description": "Write the booked job to the shop's job log.",
        "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}, "note": {"type": "string"}},
                       "required": ["job_id", "note"]}}},
]
REQUIRED = {t["function"]["name"]: set(t["function"]["parameters"].get("required", [])) for t in TOOLS}
MSG = [{"role": "system", "content": "You are the booking assistant of an HVAC shop. Use the tools to book accepted "
                                     "estimates: check hours if needed, find a slot, book it at the agreed price, then "
                                     "record the job. Today is 2026-10-23."},
       {"role": "user", "content": "Estimate E-1042 (AC capacitor and contactor replacement, about 2 hours) was accepted "
                                   "at $412. The customer wants the earliest slot from Monday 2026-10-26."}]
CONFIGS = {
    "super_think": (M.SUPER, {"max_tokens": 12000, **M.NONEMPTY}),
    "ultra_think": (M.ULTRA, {"max_tokens": 16000, **M.THINK_TOOLS}),
    "super_vl_think": (M.SUPER_VL, {"max_tokens": 12000, **M.NONEMPTY}),
}
jobs = [{"id": f"{name}#{rep}", "model": model, "messages": MSG, "meta": {"config": name},
         "params": {**M.SAMPLING, "tools": TOOLS, "tool_choice": "auto", **extra}}
        for name, (model, extra) in CONFIGS.items() for rep in range(20)]


def parsed(rec):
    """True when at least one tool call names a known tool and its arguments hold that tool's required fields."""
    for tc in rec.get("tool_calls") or []:
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            continue
        if name in REQUIRED and isinstance(args, dict) and REQUIRED[name] <= set(args):
            return True
    return False


def empty_args(rec):
    raw_calls = (((rec.get("raw") or {}).get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
    return sum(1 for tc in raw_calls if tc["function"].get("arguments") == "")


recs = run("s4_tools_reasoning_on", jobs, workers=8)
print('\n| Config | Model | parsed tool calls | first tool names | calls with arguments "" | mean s | mean reasoning | $ total |')
print("|---|---|---|---|---|---|---|---|")
for name, (model, _) in CONFIGS.items():
    rs = [r for r in recs if r["meta"]["config"] == name]
    ok = [r for r in rs if r["ok"]]
    names = {}
    for r in ok:
        first = (r["tool_calls"] or [{"function": {"name": "(none)"}}])[0]["function"]["name"]
        names[first] = names.get(first, 0) + 1
    print(f"| {name} | `{model}` | {sum(parsed(r) for r in rs)}/{len(rs)} | {names} | {sum(empty_args(r) for r in ok)} | "
          f"{sum(r['seconds'] for r in ok) / max(1, len(ok)):.2f} | "
          f"{sum(r['reasoning_tokens'] for r in ok) / max(1, len(ok)):.0f} | {sum(r['cost'] for r in ok):.4f} |")
print(spent())
