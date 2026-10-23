"""S3: structured output for the "why is the customer quiet" label on the 20 states (pre-registered in PLAN.md)."""
from types import SimpleNamespace

from common import M, run, spent
from states import all_states
from second_ask import classifier as CL

CELLS = [(M.LIGHTNING, "tool"), (M.LIGHTNING, "json_object"), (M.LIGHTNING, "json_schema"), (M.SUPER, "tool"),
         (M.SUPER, "json_object"), (M.ULTRA, "json_object"), (M.SUPER_VL, "tool")]
STATES = all_states()

jobs = []
for model, method in CELLS:
    for s in STATES:
        messages, params = CL.request(model, CL.render_state(s["conv"].estimate, s["conv"].events, s["now"], s["conv"].views), method)
        jobs.append({"id": f"{s['id']}", "model": model, "messages": messages, "params": params,
                     "meta": {"method": method, "state": s["id"], "gold": s["gold_labels"]}})
recs = run("s3_label_structure", jobs, workers=8)


def parsed(rec):
    if not rec["ok"]:
        return None, f"HTTP {rec['status']}"
    text = rec["content"] or (rec["reasoning"] if rec["from_reasoning"] else "")
    return CL.parse(SimpleNamespace(tool_calls=rec["tool_calls"] or [], text=text), rec["meta"]["method"])


print("\n| Model | Method | valid | accuracy (gold set) | primary-label accuracy | low confidence | mean s | $ for 20 | problems |")
print("|---|---|---|---|---|---|---|---|---|")
for model, method in CELLS:
    rs = [r for r in recs if r["model"] == model and r["meta"]["method"] == method]
    res = [parsed(r) for r in rs]
    valid = [(r, o) for r, (o, _) in zip(rs, res) if o]
    acc = sum(o["label"] in r["meta"]["gold"] for r, o in valid)
    prim = sum(o["label"] == r["meta"]["gold"][0] for r, o in valid)
    low = sum(o["confidence"] == "low" for _, o in valid)
    probs = sorted({p for _, p in res if p})
    ok = [r for r in rs if r["ok"]]
    print(f"| `{model}` | {method} | {len(valid)}/{len(rs)} | {acc}/{len(rs)} | {prim}/{len(rs)} | {low} | "
          f"{sum(r['seconds'] for r in ok) / max(1, len(ok)):.2f} | {sum(r['cost'] for r in ok):.4f} | {probs[:3]} |")
print("\nMisses (model, method, state: got -> gold):")
for r in recs:
    o, p = parsed(r)
    if o is None or o["label"] not in r["meta"]["gold"]:
        print(f"  {r['model'].split('/')[-1]} {r['meta']['method']} {r['meta']['state']}: "
              f"{(o or {}).get('label', p)} -> {r['meta']['gold']}")
print(spent())
