# v3: rules first, Nemotron only where the rules do not know (pre-registration)

Written and frozen before the first live call of v3. Manifest: `tournament/v3/MANIFEST.json` (its sha256, of the
canonical JSON as for v1 and v2, is in `tournament/v3/MANIFEST.sha256`). The manifest records the sha256 of this file and
of the analysis script `tools/v3_analysis.py`; `verify` fails if either changes, so neither can be edited after the
freeze without a new manifest.

**v3 is the third test of the same simulator. It was designed after v1's results were known, and after v2 stopped at its
gate.** The hand-off points were chosen from the code of `smart_rules` (rules 8 and 9 of `SmartRules`), not from v1
outcomes split by branch. Before the freeze we counted, on v1's smart_rules transcripts, how often those rules fire on a
customer's words: 94 of 649 customer lines (49 rule 8, 45 rule 9; a text-level count, not a replay). No v1 outcome was
looked at by branch.

## Question
In v1, Nemotron alone did not beat `smart_rules`, a rulebook we wrote knowing the simulator. v3 asks: do the rules plus
Nemotron, with Nemotron called only where the rules do not know the answer, beat the rules alone?

## The hybrid: `rules_then_super`
- `smart_rules` decides every move, unchanged (`SmartRules.choose_with_branch` returns the same move as v1's
  `SmartRules.choose` at every decision point; `tests/test_v3.py` checks this against the v1 code).
- Where the rules do not know: rule 8 (the customer's latest message says only think / get back / not sure / maybe) and
  rule 9 (nothing the rules recognise). There the rules fall back to a move that does not come from anything the customer
  said (ask why, then check in in 7 days or answer). On those two rules the hybrid hands the decision to the `super_only`
  agent: Nemotron 3 Super labels the reply (named tool, reasoning off) and Nemotron 3 Super chooses a move from the same
  code-built menu (reasoning on). The model never sets a price; the floor check and the compliance gate are the same.
- Silence and rules 1 to 7 (a named item, a named amount, sooner, a number of weeks or "later", family words, ready
  words, price words) stay with the rules. So does a customer whose stop word the shared opt-out code layer has already caught
  (every policy stops there). Rule 7 stays with the rules on purpose: the rules know the reason (price).
- Every hand-off is logged (model-less step "handoff"): the rule, the move the rules would have made, and the model's move
  or stop.

## Data and design
- The same 200 customers (`tournament/buyers.json`, sha256 in the manifest), seeds, simulator, menu, compliance and
  scoring as manifest `1c231403`. The buyer writer is v1's: `Qwen/Qwen3-30B-A3B-Instruct-2507`, same parameters.
- Policies run: `smart_rules` and `rules_then_super`, both fresh, in the same run, K = 5 repeats on all 200 customers:
  2,000 conversations.
- Common random numbers: within one repeat, a customer's line is written once per (customer, repeat, the whole
  conversation so far, the customer's state) and reused by both policies. While the hybrid follows the rules it sends the
  same template text at the same times as `smart_rules`, so both policies meet the same words until the first hand-off;
  their outcomes part only after Nemotron's first different move. This lowers noise; it changes no decision.

## Hypothesis and decision rule
H1: D3 = sum over the 200 customers of (net `rules_then_super` - net `smart_rules`) > 0. Net = simulated margin minus
model spend; each customer's mean over its 5 repeats; paired bootstrap over customers, 2,000 draws, seed 7, 95 %.
- **Beats** only if the lower bound of D3 is above 0 **and** the hybrid is not worse than `smart_rules` on opt-out
  violations, false opt-outs and off-arm calls (summed per-customer means; strict comparison).
- **Behind** if the upper bound of D3 is below 0.
- **Otherwise:** no clear difference, which is not equivalence.
- A false opt-out is a conversation with a customer whose type is not opt_out that ends because a model read an opt-out.
  False opt-outs are a column of the main table for both policies, not a secondary number.

## Secondary, descriptive, not adjusted
- D3 by objection type.
- Hand-offs: how many, at which rule, by type; in how many of them Nemotron's move differed from the rule's move; stops.
- The hybrid against v1's smart_rules rows (independent buyer words there).

## Smoke, budget, stop rules
- Smoke: for each type, the first two vague customers (the first two when a type has fewer), both policies, repeat 0:
  28 conversations. Mechanics and cost only; it prints no outcome table; its rows are not results. Ceiling $0.30.
- Full run ceiling $3.00 in code (and what is left of the b2 cap). Projection, an upper bound: every hybrid conversation
  costing a whole v1 `super_only` conversation, $2.84 with the 1.3 safety factor. The hybrid calls the model only at
  hand-offs, so the expected cost is far lower. The lane's ceiling for this test is $6; the campaign's $40.
- Checkpoint after 20 finished customers as in v1; platform failures re-run up to twice, then the customer is excluded from
  both policies.
- Nobody reads policy outcomes before the full run is complete: progress lines carry no outcome, and the numbers come only
  from `tools/v3_analysis.py` after the run.

## Publication, decided now
Published whatever the outcome, in `RESULTS.md` under "Third test (v3)", with this file's manifest hash and freeze time.
- **Beats:** the headline becomes "Rules + Nemotron beat rules alone: +$X [lo, hi] over 200 simulated customers", with
  the same caveat (simulated margin on illustrative prices); v1 and v2 stay below as history.
- **No clear difference or behind:** v1's framing stands ("Nemotron did not beat a good rulebook; the test changed what
  we ship"), and README and the Devpost text add one line: "We also tested rules first, with Nemotron only where the rules
  don't know: <D3 and interval>, <no clear gain / behind>."
