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
  p50 1.69 s, p90 4.24 s. Flex billing was not observed separately: the client computes $ from its own price table, so
  the equal cost per call (0.000033) says nothing about what flex is billed. Flex calls did not
  decrement `x-ratelimit-remaining-tokens` (200000 after the flex burst vs 199920 after the default burst), consistent with
  "do not consume rate-limit credits". No 429s.
- Headers returned: `x-ratelimit-limit-{requests,tokens}`, `-remaining-*`, `-reset-*`, `-dynamic-scale-*`,
  `-dynamic-period-remaining`, `-dynamic-period-usage-*`.
- `n: 3` bills the prompt once: prompt 30 tokens for both `n: 1` and `n: 3`; completion 29 vs 75 tokens over 3 choices.
- Projection against the $25 bar: see the tournament projection in `tournament/PROJECTION.md`.

## S3. Structured output for the label — PASS for every shipped method
20 states (`states.json`, committed before the run), reasoning off, one call per state per cell.

| Model | Method | valid | correct (gold set) | correct (primary label) | low confidence | mean s | $ for 20 calls |
|---|---|---|---|---|---|---|---|
| Lightning | named tool | 20/20 | 18/20 | 17/20 | 3 | 1.25 | 0.0012 |
| Lightning | `json_object` | 20/20 | **19/20** | 18/20 | 3 | 1.24 | 0.0008 |
| Lightning | strict `json_schema` | 20/20 | 17/20 | 17/20 | 6 | 1.44 | 0.0009 |
| Super | named tool | 20/20 | 19/20 | 19/20 | 1 | 1.45 | 0.0062 |
| Super | `json_object` | **17/20 (FAIL)** | 17/20 | 17/20 | 0 | 1.30 | 0.0035 |
| Ultra | `json_object` | 20/20 | 20/20 | 20/20 | 0 | 0.87 | 0.0123 |
| Super-VL | named tool | 20/20 | 20/20 | 20/20 | 2 | 0.98 | 0.0057 |

- Strict `json_schema` is accepted on Lightning (20/20 valid), contrary to the OpenAPI field description; it was the least
  accurate of the three Lightning methods here.
- Super with `json_object` invented `confidence: "medium"` 3 times (schema says high/low): the validator caught it.
- Lightning's misses: "We already went with another electrician, thanks" labelled `opt_out` by all three methods
  (gold `other`); a silent, never-opened estimate labelled `price` once; a warranty question labelled `price` once; "Still
  thinking." labelled `ready_to_book` once.
- Shipped: Lightning with `json_object` (routed policy), Super with the named tool (default label model and fallback),
  Ultra with `json_object` (Ultra-only policy), Super-VL with the named tool (policy 6).

## S7. Move choice — routing decision: Ultra is NOT routed in; Super with reasoning chooses
20 states × K = 3; the chooser gets each state's gold primary label, so this measures move choice alone.

| Config | correct (mean of K) | per repeat | valid calls | mean s | mean reasoning tokens | $ per call |
|---|---|---|---|---|---|---|
| Super, reasoning off | 0.817 | 0.85, 0.85, 0.75 | 60/60 | 2.70 | 0 | 0.00075 |
| **Super, reasoning on** | **0.950** | 0.90, 1.00, 0.95 | 60/60 | 2.80 | 143 | 0.00083 |
| Ultra, reasoning on | 0.883 | 0.90, 0.90, 0.85 | 60/60 | 2.65 | 304 | 0.00326 |
| Super-VL, reasoning off | 0.933 | 0.95, 0.95, 0.90 | 60/60 | 1.28 | 0 | 0.00068 |
| Super-VL, reasoning on | 0.900 | 0.90, 0.90, 0.90 | 60/60 | 3.30 | 489 | 0.00113 |

Pre-registered rules applied (`s7_move_choice/decision.json`):
1. Super configuration: reasoning on beats reasoning off by 13.3 points (> 5) → **Super with reasoning on**.
2. Ultra vs that Super: 0.883 vs 0.950 → Ultra does not beat Super by more than 5 points → **the routed policy uses Super
   for every move, including the price move.** Named limitation: in our 20 states Ultra's role is empty. On the five
   price states alone Ultra got 14/15 and Super 15/15. Ultra costs ~3.9× more per choice here.
3. Super-VL: reasoning on does not beat reasoning off (0.900 vs 0.933) → **Super-VL with reasoning off** for policy 6.

Where the models lost (all configs): S06 "not until after the 15th of next month" (gold `checkin_30`; models often
answered or checked in at 14 days); S20 "Still thinking." (gold `ask_why`; Ultra and Super-VL with reasoning checked in
instead, 0/3); S19 "already went with another electrician" (Super without reasoning answered instead of closing out, 0/3);
S11 Super-VL with reasoning checked in at 3 days instead of dropping the item the wife questioned (0/3).
Every call returned a valid `choose_move` (300/300). Message check: the models' texts were replaced by a template in
2–5 of 60 calls per config, almost always because they quoted the customer's own number back ("under your $3,600
budget"). After this run (post hoc) the check was changed to allow a customer-stated amount when it is at or above the
move's price; with that rule 6 of 300 messages would still be replaced.

## S8. Buyer simulator
### S8a. Buyer model — pick: Qwen3-30B-A3B-Instruct-2507
Each candidate wrote the first message for the same 10 buyer cards (`s8_buyer_writers/raw.jsonl`); Lightning then
labelled each message.

| Candidate | valid JSON | leaks of the hidden walkaway | Lightning reads the intended type | $ per message | mean s |
|---|---|---|---|---|---|
| **Qwen3-30B-A3B-Instruct-2507** | 10/10 | 0 | 9/10 | 0.000038 | 0.75 |
| DeepSeek-V4-Flash-0731 | 10/10 | 0 | 9/10 | 0.000043 | 1.15 |
| gpt-oss-120b | 10/10 | 0 | 9/10 | 0.000111 | 1.67 |

Rule (cheapest with ≥ 9/10 valid and 0 leaks) → Qwen3-30B. The walkaway is never in the writer's prompt, so 0 leaks is
expected; the check still runs on every message. The one miss for every model was a deliberately vague family message
("I need to think it over").

### S8b. Sanity gates — PASS (dry run, 200 buyers, rule policies; `tournament/dryrun/summary.json`)
- Bookings above the buyer's hidden walkaway: **0**; below the owner's floor: **0**. Both hold by construction (the buyer
  code never books above its walkaway, and every move is floor-checked), so they are integrity checks, not evidence that
  the simulator is realistic; a unit test with synthetic violations proves the counters can fire.
- Booking rate: shop rule 0.455, concession heuristic 0.330 (gate 0.30–0.80).
- Price buyers' booking rate by walkaway tercile: 0.00 → 0.03 → 0.47 (rises).
- Booking rate by objection type spans 0 to 1 for both rule policies (gate: spread ≥ 0.20).
The first dry run failed the 30–80 % gate (15 % and 13 %): every unhelpful message cost "patience" for every buyer
type. The buyer model was changed before any live run to lose buyers to time (a hidden "hires someone else by" day),
with patience only for failed price offers; then the gate passed. That first variant was not committed; its gate result
(15 % and 13 %) is recorded only in the lane's internal log.

### Live smoke of phase A (10 conversations: 2 pre-registered buyers × 5 policies; `tournament/smoke/`)
Mechanics only, n = 2 buyers, and run before the three simulator fixes below: none of its numbers is a result. The same
10 conversations were re-run on the frozen code in `tournament/smoke-b/` (see `RESULTS.md`).
All 10 ran end to end through Token Factory. Shop-side cost per conversation: routed $0.0009–0.0017, Super-only
$0.0013–0.0022, Super-VL $0.0010–0.0053, Ultra-only $0.0056–0.0142; buyer messages $0.00003–0.00005 each.
What the smoke exposed (fixed before the manifest; the smoke was not re-run, per the phase A limit of 10 conversations):
1. The buyer writer invented requests not on its card ("can you come back tomorrow afternoon?"), which steered the agent.
   The writer prompt now forbids anything 'This turn' does not say.
2. A family buyer replied to every "I'll check back next week" within hours, so Super-VL spent all 5 messages in 3 days.
   Buyers who are told "I will check back" now wait silently (price, pause and family types).
3. Family deliberation only started after an "info" message, contradicting the S7 gold sets (check-ins acceptable).
   The household now decides on its own clock from its first message; a discount before then only lowers the price it
   books at.
One honest loss already visible: on the price buyer (B018) all four Nemotron policies dropped the optional module
($1,480, margin $666) while the shop rule's floor price ($1,765, margin $687) earned $21 more; the buyer would also
have accepted the 5 % concession ($1,860).
