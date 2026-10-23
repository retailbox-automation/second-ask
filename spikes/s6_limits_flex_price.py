"""S6: limits, flex and billing of n>1 (pre-registered in PLAN.md).

The verbose model list is a free GET (no tokens billed), so it is read directly; every chat call goes through tf_client.
"""
import json, os, statistics, urllib.request
from common import EVIDENCE, M, run, spent

OURS = [M.LIGHTNING, M.SUPER, M.ULTRA, M.SUPER_VL, M.QWEN3_30B, M.DEEPSEEK_V4_FLASH, M.GPT_OSS_120B]
req = urllib.request.Request("https://api.tokenfactory.nebius.com/v1/models?verbose=true",
                             headers={"Authorization": "Bearer " + os.environ["NEBIUS_API_KEY"]})
with urllib.request.urlopen(req, timeout=60) as r:
    listing = json.load(r)
keep = {m["id"]: {k: m.get(k) for k in ("context_length", "pricing", "per_request_limits", "supported_features",
                                        "supported_sampling_parameters")}
        for m in listing.get("data", []) if m["id"] in OURS}
(EVIDENCE / "s6_limits_flex_price").mkdir(parents=True, exist_ok=True)
(EVIDENCE / "s6_limits_flex_price" / "models_verbose.json").write_text(json.dumps(keep, indent=1))
print("| Model | context | limits | sampling params |\n|---|---|---|---|")
for mid in OURS:
    m = keep.get(mid, {})
    print(f"| `{mid}` | {m.get('context_length')} | {m.get('per_request_limits')} | {m.get('supported_sampling_parameters')} |")

MSG = [{"role": "user", "content": "Reply with one short sentence: what does a home AC capacitor do?"}]
base = {**M.SAMPLING, "max_tokens": 64, **M.REASONING_OFF}
jobs = [{"id": f"{tier}#{i}", "model": M.SUPER, "messages": MSG, "meta": {"tier": tier},
         "params": {**base, **({"service_tier": "flex"} if tier == "flex" else {})}}
        for tier in ("default", "flex") for i in range(40)]
jobs += [{"id": f"n{n}", "model": M.SUPER, "messages": MSG, "meta": {"tier": f"n{n}"},
          "params": {**base, "max_tokens": 32, "n": n}} for n in (1, 3)]
recs = run("s6_limits_flex_price", jobs, workers=8)

print("\n| Tier | ok | 429/other errors | p50 s | p90 s | $ per call | last x-ratelimit headers |\n|---|---|---|---|---|---|---|")
for tier in ("default", "flex"):
    rs = [r for r in recs if r["meta"]["tier"] == tier]
    ok = [r for r in rs if r["ok"]]
    secs = sorted(r["seconds"] for r in ok)
    hdr = max(ok, key=lambda r: r["ts"])["headers"] if ok else {}
    print(f"| {tier} | {len(ok)}/{len(rs)} | {[r['status'] for r in rs if not r['ok']]} | "
          f"{statistics.median(secs):.2f} | {secs[int(0.9 * (len(secs) - 1))]:.2f} | "
          f"{sum(r['cost'] for r in ok) / max(1, len(ok)):.6f} | {hdr} |")
n1, n3 = (next(r for r in recs if r["id"] == f"n{n}") for n in (1, 3))
print(f"\nn=1: usage {n1.get('usage')}  choices={len((n1.get('raw') or {}).get('choices', []))}")
print(f"n=3: usage {n3.get('usage')}  choices={len((n3.get('raw') or {}).get('choices', []))}")
print(spent())
