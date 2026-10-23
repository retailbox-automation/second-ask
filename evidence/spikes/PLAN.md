# Platform spikes for Second Ask: plan and pass/fail metrics

This file was committed before any of these spikes ran (see `git log -- evidence/spikes/PLAN.md`).
Results are appended to `evidence/spikes/RESULTS.md`; raw request parameters and raw responses are in
`evidence/spikes/<spike>/raw.jsonl`. API keys are never written anywhere.

All calls go to Nebius Token Factory (`https://api.tokenfactory.nebius.com/v1/`), non-streaming, with
`temperature 1.0, top_p 0.95` unless a row says otherwise. Model IDs are copied from the live catalog
(`/api/public/models_info`, read 2026-10-23). There is no `seed` in the API, so results are counts over repeats.

| Short name | Model ID |
|---|---|
| Lightning | `nvidia/Nemotron-3_5-Lightning` |
| Super | `nvidia/nemotron-3-super-120b-a12b` |
| Ultra | `nvidia/Nemotron-3-Ultra-550b-a55b` |
| Super-VL | `nvidia/Nemotron-3.5-Super-VL` |
| Qwen3-30B (buyer candidate) | `Qwen/Qwen3-30B-A3B-Instruct-2507` |
| DeepSeek-V4-Flash (buyer candidate) | `deepseek-ai/DeepSeek-V4-Flash-0731` |
| gpt-oss-120b (buyer candidate) | `openai/gpt-oss-120b` |

## S1. Reasoning switch
Same one-line classification prompt, `max_tokens 4000`, 2 repeats per cell, 7 models × 4 forms:
(a) no reasoning parameter; (b) `reasoning_effort: "none"`; (c1) top-level `chat_template_kwargs: {"enable_thinking": false}`
(what the OpenAI SDK sends for `extra_body`); (c2) a literal `extra_body: {"chat_template_kwargs": {"enable_thinking": false}}`
field (the OpenAPI schema lists `extra_body` as a body field). gpt-oss-120b also gets (d) `reasoning_effort: "low"`.
- **Metric:** per model × form: HTTP status, `usage.completion_tokens_details.reasoning_tokens`, non-empty `content`.
- **Pass (per model):** at least one form gives `reasoning_tokens == 0` and non-empty `content` in 2 of 2 repeats.
  Fail → that model is only used with reasoning on and a large `max_tokens`.

## S2. Re-measure the 14.10 spike at recommended parameters
The exact 14.10 task (`write_fact` tool, message "I moved to Austin last week, and my dentist is now Dr. Lee."),
`tool_choice: auto`, 5 repeats per configuration:
- Super, reasoning on (default), `force_nonempty_content: true`, `max_tokens 12000`;
- Lightning, reasoning on, `force_nonempty_content: true`, `max_tokens 16000`;
- Lightning, reasoning off (the form that passed S1), `max_tokens 1024`.
- **Metric:** facts captured per call out of 2. A fact counts when a `write_fact` call with parseable JSON arguments has
  `object` containing "Austin" (move) or "Lee" (dentist), case-insensitive. Score = facts over 10 per configuration.
- **Pass:** Super ≥ 9/10. Lightning ≥ 8/10 in at least one configuration → Lightning keeps the classifier role
  (the classifier is re-checked directly in S3); otherwise the label is made by Super without reasoning.

## S3. Structured output for the "why is the customer quiet" label
20 hand-written negotiation states with a gold label each (`evidence/spikes/states.json`, committed before S3 runs),
reasoning off, one call per state per cell:
- Lightning × {named tool `label_reply` with `tool_choice` by name; `response_format: json_object`; strict `json_schema`};
- Super × {named tool; json_object}; Ultra × {json_object}; Super-VL × {named tool}.
- **Metric:** valid rate = responses whose label object parses and validates (label in the enum, `reason` a string).
  Secondary (reported, not a gate): label accuracy against gold.
- **Pass:** ≥ 95 % valid (19 of 20) for the method we ship. Fail → tool calling + validation + one retry.

## S4. Tool calls with reasoning on (booking loop, first step)
Booking prompt with four tools (`get_shop_hours` has no arguments, to provoke `arguments: ""`; `find_slots`, `book_job`,
`record_job`), `tool_choice: auto`, 20 calls per model:
Super (`force_nonempty_content: true`, `max_tokens 12000`), Ultra (`enable_thinking: true, force_nonempty_content: true`,
`max_tokens 16000`), Super-VL (same as Super).
- **Metric:** parsed tool-call rate = responses with ≥ 1 tool call whose name is one of the four tools and whose arguments
  parse to a JSON object containing that tool's required fields. Count of `arguments: ""` seen.
- **Pass:** ≥ 19 of 20 per model; every `arguments: ""` is handled (parses to `{}`).

## S6. Limits, flex and price
- Free: `GET /v1/models?verbose=true` for our 7 models (`per_request_limits`, `supported_sampling_parameters`).
- 40 short Super calls (reasoning off, `max_tokens 64`) at concurrency 8 with the default tier, then 40 with
  `service_tier: "flex"`; record latency, `x-ratelimit-*` headers, 429s and cost.
- One Super call with `n: 3` vs the same call with `n: 1`: is the prompt billed once or three times?
- **Metric:** headers present; flex accepted (HTTP 200) and its latency; `prompt_tokens` ratio for `n=3`.
- **Pass:** projected cost of the full tournament (from S6 prices and the live smoke) ≤ $25. This lane's cap is $20,
  so the proposal must fit $20 minus phase A spend.

## S7. Move choice: Ultra vs Super vs Super-VL (the routing decision)
The same 20 states. For each, code builds the move menu (`second_ask/menu.py`) and the state file names the gold set of
acceptable move ids, written before the run. The chooser calls `choose_move(move_id, message, reason)`; K = 3 repeats.
Variants: Super reasoning on; Super reasoning off; Ultra reasoning on (required with tools); Super-VL reasoning on;
Super-VL reasoning off.
- **Metric:** correct-move rate = share of (state, repeat) where a valid `choose_move` call names a move id in the
  gold set. Invalid or missing calls count as wrong. Also mean $ per call and latency.
- **Decision rules, fixed now:**
  1. Super configuration = reasoning off, unless reasoning on beats it by more than 5 percentage points (1 state of 20).
  2. Ultra is routed in for the price move only if its correct-move rate beats the chosen Super configuration by more
     than 5 percentage points. Otherwise ("Super not worse than Ultra") the routed policy uses Super and we publish
     that as a named limitation.
  3. Same rule picks the Super-VL configuration for policy 6.

## S8. Buyer simulator
Hidden buyer parameters (walkaway price, objection type, patience, channel, readiness) and every buyer decision are set
in code (`second_ask/tournament/`); the buyer model only writes the buyer's message and keeps short notes.
(a) Buyer-model choice: the three non-NVIDIA candidates each write 10 buyer messages for the same 10 buyer cards.
- **Metric:** JSON validity; leaks (the hidden walkaway number appears in the message); share of first messages that
  Lightning labels with the buyer's true objection type; $ per message and latency.
- **Pick:** the cheapest candidate with ≥ 9/10 valid and 0 leaks; ties broken by label agreement.
(b) Sanity gates on the simulated buyers (dry run with all 200 buyers and the two rule policies, plus the live smoke):
- no booking above the buyer's hidden walkaway price (count must be 0);
- booking rate per policy between 30 % and 80 %;
- outcome moves with the hidden parameters: for price-objection buyers the booking rate rises with the walkaway
  tercile, and booking rates differ across objection types.
