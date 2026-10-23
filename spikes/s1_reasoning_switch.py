"""S1: which request form turns reasoning off on each model (pre-registered in evidence/spikes/PLAN.md)."""
from common import M, run, spent

PROMPT = [{"role": "user", "content": "A homeowner replied to a plumbing estimate: \"We found someone cheaper.\" "
                                      "Answer with one word: price, timing, family or channel."}]
FORMS = {
    "a_none_set": {},
    "b_effort_none": {"reasoning_effort": "none"},
    "c1_kwargs_top": {"chat_template_kwargs": {"enable_thinking": False}},
    "c2_extra_body": {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
}
MODELS = [M.LIGHTNING, M.SUPER, M.ULTRA, M.SUPER_VL, M.QWEN3_30B, M.DEEPSEEK_V4_FLASH, M.GPT_OSS_120B]

jobs = []
for model in MODELS:
    forms = dict(FORMS, **({"d_effort_low": {"reasoning_effort": "low"}} if model == M.GPT_OSS_120B else {}))
    for form, extra in forms.items():
        for rep in range(2):
            jobs.append({"id": f"{form}#{rep}", "model": model, "messages": PROMPT,
                         "params": {**M.SAMPLING, "max_tokens": 4000, **extra}, "meta": {"form": form}})

recs = run("s1_reasoning_switch", jobs, workers=6)
rows = {}
for r in recs:
    rows.setdefault((r["model"], r["meta"]["form"]), []).append(r)
print("\n| Model | Form | HTTP | reasoning tokens | non-empty content | content |")
print("|---|---|---|---|---|---|")
for (model, form), rs in rows.items():
    print(f"| `{model}` | {form} | {','.join(str(r['status']) for r in rs)} | "
          f"{','.join(str(r.get('reasoning_tokens', '-')) for r in rs)} | "
          f"{sum(bool((r.get('content') or '').strip()) for r in rs)}/{len(rs)} | "
          f"{' / '.join(repr((r.get('content') or r.get('error') or '')[:40]) for r in rs)} |")
print(spent())
