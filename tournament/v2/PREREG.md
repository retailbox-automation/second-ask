# v2: wording robustness (pre-registration)

> **Amended after a failed smoke** (manifest 2, sha256 `0f6f76bc31d04a3197271422f666da014e02c82de8c23dd1010824019e2b899e`): two defects of the measuring instrument were fixed (the judge mapping for household-must-review, and the $-amount pattern out of the gated L_own), the writer prompt got its one revision, and the second smoke uses new customers. Thresholds, hypothesis, decision rules, policies and budget are unchanged. Before, after, why, and a disclosure that policy outcomes of the first smoke were seen before the amendment: `tournament/v2/CHANGES.md`. The text below is the original pre-registration of manifest 1.

Written and pushed before the first live call of v2. Manifest `tournament/v2/MANIFEST.json`, sha256
`83010fc474bf3a7b0a5aaf3269276ff695f221b75a1055a3c2b915e5f0a03e7d` (canonical JSON, as for v1). Analysis script
`tools/v2_analysis.py`, sha256 `f13d1f11a7ed4f51ff68922712dd773ede93f153059b28a3c22dd4feb356d499` (recorded in the
manifest; `verify` fails if it changes). v1 rows it compares against: `tournament/full/rows.jsonl`, sha256
`364f964adabce1dbb22bf5184c1cb931c4b73b27ab38727b218d6a9018348dbe`.

## Why
In v1 the rulebook `smart_rules`, which we wrote knowing the simulator's vocabulary, matched or beat every Nemotron
policy. Its rules fire on the simulator's own phrasing: on v1's 3,133 buyer lines, 97 % match at least one of its
triggers and 79 % match the rule meant for the customer's type. v2 asks whether that edge survives when customers do not
speak the simulator's vocabulary.

## What changes, and what does not
- Only the buyer writer. `FreeWordingWriter` (second_ask/tournament/env.py) gets the turn's facts as data from
  `env.intent()`, the same facts `env.instruction()` gives the v1 writer (a test checks this on every card, spec and
  state), and writes them in its own words. It never sees the simulator's instruction sentences or any policy's rules.
  Writer: `deepseek-ai/DeepSeek-V4-Flash-0731`, temperature 1.0, top_p 0.95, reasoning off (v1: Qwen3-30B). Its prompt is
  in the manifest (`buyer_simulator.prompt`).
- Changed files: `second_ask/tournament/env.py` (the writer, `intent()`, a per-message log), `run.py` (selects the writer,
  the policy list and the run directory from the manifest; refuses to resume a directory started under another manifest),
  `manifest.py` (the v2 sections). Every other file under `second_ask/` has the sha256 it has in manifest 1c231403
  (`tests/test_v2.py::test_everything_but_the_writer_hookup_is_the_v1_code`): policies, menu, compliance, scoring,
  buyers, runner and client are the v1 code. The 200 buyer cards are the same file. v1 is reproduced at commit `6d66d53`.
- Policies run: shop_rule, concession_heuristic, smart_rules, super_only, routed, super_vl, with K = 3 on the same
  100-customer subsample: 2,000 conversations. **ultra_only is not tested in v2** (left out for budget).

## Hypothesis and decision rule
H1: a rulebook written for the simulator's vocabulary loses more than Nemotron when customers word things freely.
Metric as in v1: net (simulated margin minus model spend), each customer's mean over its repeats, summed over customers;
paired bootstrap over customers, 2,000 draws, seed 7, 95 %.
- **Primary:** D2 = sum over customers of (net super_only - net smart_rules) in v2. "Super-only beats the rulebook on
  wording outside the simulator's vocabulary" only if the lower bound of D2 is above 0.
- **Robustness:** DiD = sum over customers of [(super_only - smart_rules) in v2 - (super_only - smart_rules) in v1].
  "The rulebook is more brittle than Super-only" only if the lower bound of DiD is above 0.
- **Continuity:** the same two statistics for routed. Secondary, unadjusted: super_vl; each policy's v2 - v1.
- **Otherwise:** no advantage shown on live wording; the v1 headline stands.
- Published whatever the outcome; both tests in RESULTS.md.

## Gates on the smoke (52 conversations), before the full run
1. Integrity as in v1: every smoke row clean, no errored row after re-runs, no HTTP 429, 0 off-arm calls.
2. Template fallbacks at most 2 % of v2 buyer messages.
3. Vocabulary: L_own (share of model-written lines on which the smart_rules rule meant for the type fires) at most
   **59 %** (v1: 79 %; by type: timing_pause 58 %, timing_slot 86 %, family 68 %, price 100 %, ready 100 %). Reported by
   type with intervals.
4. Fidelity: an independent judge, `Qwen/Qwen3-235B-A22B-Instruct-2507` (not the writer, not NVIDIA; sees no rules),
   recovers the turn's facts (kind; amount within 5 %; weeks within 1; weekday exact; household; item) from at least
   **90 %** of v2 lines, with every line judged.
   Reference, reported next to it: the v1 writer (Qwen3-30B with the v1 instruction sentences) on 300 turns sampled
   across the cards (seed 7), judged the same way. Deviation from the draft, disclosed: the draft said "300 v1 lines",
   but v1 lines carry no stored facts to judge against, so the reference regenerates v1-writer lines for sampled turns.

On a failed gate: one revision of the writer prompt (it may not see the rules or list trigger words), a line in
`tournament/v2/CHANGES.md`, a new manifest and a new smoke directory. A second failure stops v2, reported as "the writer
did not pass the gate". The smoke checks mechanics, cost and these gates only.

## Budget and stop rules
- Ceilings in code: full run $4.50 (manifest `budget.run_ceiling_usd`), smoke $0.50, judge $0.50 (the ledger delta it
  checks covers the reference writer too). Sum $5.50 within the $6 budget; Token Factory total stays under $13.
- Projection from v1's measured cost per conversation: $2.97 for the full run.
- Cost checkpoint after 20 customers, as v1.
- Stop (Ctrl-C) if more than 5 % of customers are excluded or any policy has more than 10 % errored rows; any
  conversation failing on HTTP 429 after its retries stops the run.

## Time box
Test and an independent audit of the numbers by Sun 25.10 16:00 ET (20:00Z); no v2 numbers by Sun 18:00Z means stop and
submit on the v1 framing.
