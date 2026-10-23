# Platform spike results (metrics pre-registered in PLAN.md)

Run 2026-10-23 from 15:58Z on Token Factory with our project's default limits. Raw request parameters and raw responses
for every call: `evidence/spikes/<spike>/raw.jsonl`. Scripts: `spikes/`.

## S1. Reasoning switch — PASS for all four Nemotron models
| Model | no parameter | `reasoning_effort:"none"` | top-level `chat_template_kwargs` | literal `extra_body` field |
|---|---|---|---|---|
| Lightning | 234, 291 reasoning tokens | 0, 0 | 0, 0 | 0, 0 |
| Super | 77, 40 | 0, 0 | 0, 0 | 0, 0 |
| Ultra | 44, 45 | 0, 0 | 0, 0 | 0, 0 |
| Super-VL | 51, 61 | 0, 0 | 0, 0 | 0, 0 |
| Qwen3-30B-Instruct | 0, 0 (no reasoning mode) | 0, 0 | 0, 0 | 0, 0 |
| DeepSeek-V4-Flash | 67, 76 | 0, 0 | 0, 0 | 0, 0 |
| gpt-oss-120b | reasons, but usage hides it | HTTP 400 | reasons | reasons |

Every cell with HTTP 200 returned a non-empty `content` ("price") in 2 of 2 repeats.
- All three switch forms work on all four Nemotrons; both `chat_template_kwargs` placements are accepted. We ship
  `reasoning_effort: "none"` (the field Token Factory documents in its OpenAPI schema).
- gpt-oss-120b: `reasoning_effort:"none"` → HTTP 400 "Harmony does not support reasoning_effort='none'"; with any
  other form it still writes a `reasoning` field, and `usage.completion_tokens_details` is `null`, so its reasoning
  tokens cannot be separated in usage (platform finding). It fails S1.

## S2. The 14.10 spike re-measured — Super PASS, Lightning FAIL
| Config | facts of 10 | per call | mean s | mean reasoning tokens | $ for 5 calls |
|---|---|---|---|---|---|
| Super, reasoning on, `force_nonempty_content`, max 12000 | **10/10** | 2,2,2,2,2 | 3.05 | 59 | 0.00162 |
| Lightning, reasoning on, `force_nonempty_content`, max 16000 | 5/10 | 2,1,1,0,1 | 1.07 | 0 | 0.00026 |
| Lightning, reasoning off, max 1024 | 4/10 | 1,1,1,0,1 | 1.12 | 0 | 0.00024 |

- Super passes (≥ 9/10). The 14.10 conclusion "Super for tool loops" stands at the recommended parameters.
- Lightning fails the pre-registered 8/10 bar in both configurations. How it fails: 9 of 10 responses made exactly one
  tool call and stopped, so the second fact was missing; 2 of 10 put the fact in the wrong field
  (`relation: "moved to Austin", object: "last week"`). Lightning also produced 0 reasoning tokens with tools even when
  reasoning was on. By the pre-registered rule the product's default label model becomes Super without reasoning; S3
  measures the label task itself, and the tournament keeps a routed policy with Lightning so its effect is measured in $.

## S4. Tool calls with reasoning on — PASS for Super, Ultra, Super-VL
| Config | parsed tool calls | first tool chosen | `arguments: ""` seen | mean s | mean reasoning tokens | $ for 20 calls |
|---|---|---|---|---|---|---|
| Super, `force_nonempty_content` | **20/20** | get_shop_hours 10, find_slots 10 | 0 | 1.51 | 70 | 0.0063 |
| Ultra, `enable_thinking` + `force_nonempty_content` | **20/20** | find_slots 16, get_shop_hours 4 | 0 | 1.66 | 141 | 0.0248 |
| Super-VL, `force_nonempty_content` | **20/20** | get_shop_hours 16, find_slots 4 | 0 | 1.58 | 184 | 0.0082 |

No model sent `arguments: ""` for the no-argument tool here; the client still maps `""` to `{}` (unit test).

## S6. Limits, flex, `n` — measured
- `GET /v1/models?verbose=true` (`s6_limits_flex_price/models_verbose.json`): limits per minute are Lightning 400K tokens /
  600 requests, Super and Ultra 200K / 300, Super-VL 2M / 300, Qwen3-30B 400K / 600, DeepSeek-V4-Flash 1M / 3000,
  gpt-oss-120b 500K / 600. Super-VL lists **no** supported sampling parameters (empty list); the others list
  `temperature, top_p, top_k, repetition_penalty, frequency_penalty, presence_penalty`. No `seed` anywhere.
- 40 short Super calls at concurrency 8: default tier 40/40, p50 0.86 s, p90 1.09 s; `service_tier:"flex"` 40/40,
  p50 1.69 s, p90 4.24 s. Same $ per call (0.000033), so flex gave no discount in this test. Flex calls did not
  decrement `x-ratelimit-remaining-tokens` (200000 after the flex burst vs 199920 after the default burst), consistent with
  "do not consume rate-limit credits". No 429s.
- Headers returned: `x-ratelimit-limit-{requests,tokens}`, `-remaining-*`, `-reset-*`, `-dynamic-scale-*`,
  `-dynamic-period-remaining`, `-dynamic-period-usage-*`.
- `n: 3` bills the prompt once: prompt 30 tokens for both `n: 1` and `n: 3`; completion 29 vs 75 tokens over 3 choices.
- Projection against the $25 bar: see the tournament projection in `tournament/PROJECTION.md`.
