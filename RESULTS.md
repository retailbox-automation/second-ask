# Second Ask: tournament results

**Second Ask, measured: in our pre-registered test, Nemotron did not beat a good rulebook. Against `smart_rules`, a rulebook we wrote with full knowledge of the simulator, the routed design (Lightning labels, Super chooses) came out at -$36,147 [-$63,856, -$13,051] against it over 200 simulated customers (-$181 [-$319, -$65] per customer): behind. Super-only: -$16,720 [-$43,004, +$8,643] (-$84 [-$215, +$43] per customer): no clear difference. The test changed what we ship: Super labels by default. All $ are simulated margin on illustrative prices (not market data).**

Pre-registered manifest `tournament/MANIFEST.json`: sha256 `1c231403c8fd5e1109ad1bad04ad411e0234be49e6f5c04a129260347c233a1e` of its canonical JSON (sorted keys, no spaces; the file's own sha256 is `e3551d565a561fd1…`). Frozen in commit `6d66d53` at 2026-10-24T10:35:38Z, pushed 2026-10-24T10:36:04Z; the run started 2026-10-24T10:58:31Z and finished 2026-10-24T11:37:04Z. `second_ask/**` and the manifest did not change between the freeze and the end of the run (the second test later changed three files; this run reproduces at commit `6d66d53`). Run: 2403 conversation rows (2400 clean, 3 errored and re-run clean), real Token Factory spend $5.78.

Numbers on this page are computed from the run files by `python3 -m second_ask.tournament.run results` (the pre-registered tables) and `tools/run_checks.py results-md` (this page). That generator is not in the manifest and was first committed in `db01ff6` at 2026-10-24T11:39:15Z, after the run: the numbers are computed, but the presentation and wording were chosen with the results known.

## Main comparison (pre-registered): Nemotron against a good rulebook

| comparison: net, simulated margin on illustrative prices (not market data) | over 200 customers [95 % interval] | per customer | reading |
|---|---|---|---|
| routed - smart_rules | -$36,147 [-$63,856, -$13,051] | -$181 [-$319, -$65] per customer | behind: the whole interval is below 0 |
| super_only - smart_rules | -$16,720 [-$43,004, +$8,643] | -$84 [-$215, +$43] per customer | no clear difference, which is not equivalence: from $215 per customer behind to $43 ahead is inside the interval |

Read this with how `smart_rules` was made. We wrote it with full knowledge of the simulator's vocabulary and decision code: asking why turns the 49 customers who start vague into clear ones, who then state amounts, weeks and deadlines in the simulator's phrasing, and an amount a customer names is 88–97 % of their hidden walkaway (`second_ask/tournament/buyers.py`). It takes 91.0% of the oracle margin ($253,040 over 161 feasible customers), so any policy has little room above it here. This favours the rulebook in this simulator and says nothing about live customers. Paired bootstrap over customers, 2,000 draws, seed 7.

## What the test changed: the default label model

The product as first designed (routed: Lightning labels, Super chooses) finished behind the rulebook and failed the pre-registered label rule against super_only: routed - super_only -$19,427 [-$43,215, +$35] (-$97 [-$216, +$0.17] per customer); the rule keeps Lightning only if the lower bound is at or above -$2,000 and routed is not worse on violations, unsure and off-arm calls (violations 0.0 vs 0.0; unsure 98.3 vs 28.7; off_arm_calls 0.0 vs 0.0). The test changed the default label model to **Super**. Keeping Lightning in routed was already a disclosed deviation from spike S2, where Lightning scored 5/10 and 4/10 (`decision_rules.deviation_from_S2`); the tournament's rule reversed it. "Not worse" is a strict comparison of summed counts; violations and off-arm calls are 0 by construction in the rows that count.

## Secondary comparisons (unadjusted 95 % intervals)

- ultra_only - smart_rules: +$2,652 [-$13,955, +$21,811] (+$13 [-$70, +$109] per customer): no clear difference, which is not equivalence: from $70 per customer behind to $109 ahead is inside the interval.
- super_vl - smart_rules: -$6,541 [-$29,232, +$15,492] (-$33 [-$146, +$77] per customer): no clear difference, which is not equivalence: from $146 per customer behind to $77 ahead is inside the interval.
- Against a naive discount-first rule (`shop_rule`: discount to the floor at the first objection, then weekly reminders; our stand-in, not a real shop's data), in one line: `smart_rules` (no model) +$117,167 [+$83,655, +$155,302] (+$586 per customer); `routed` +$81,020 [+$53,395, +$110,597] (+$405 per customer); `super_only` +$100,447 [+$67,011, +$139,369] (+$502 per customer); `super_vl` +$110,626 [+$78,469, +$148,226] (+$553 per customer); `ultra_only` +$119,819 [+$86,014, +$159,317] (+$599 per customer). The rulebook without a model gains about as much or more: the gain over the naive rule comes from the menu (ask why, an earlier slot, a resend on the other channel), not from the model.
- `concession_heuristic` (naive gap-halving, also our stand-in) - smart_rules: -$145,696 [-$187,338, -$109,192].
- Descriptive, not pre-registered: ultra_only - super_only +$19,372 [+$3,050, +$40,433] at $1.30 more model spend. Spike S7 kept Ultra out of routing on the spike's gold set; that choice is not confirmed here.

## Per policy

Each customer's mean over its repeats, summed over 200 customers; simulated margin on illustrative prices (not market data). Net per customer: `concession_heuristic` $423; `routed` $970; `shop_rule` $565; `smart_rules` $1,151; `super_only` $1,067; `super_vl` $1,118; `ultra_only` $1,164. Listed alphabetically: the differences among the top policies are not clear (see above).

| policy | buyers | K | bookings | rate | margin $ | given away $ (to full payers / menu gap) | lost $ (price) | model $ | $/booking | net $ | violations | unsure | off-arm | label fallback | platform failures |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| concession_heuristic | 200 | 1 | 66.0 | 0.330 | 84,524 | 7,719 (5,990 / 682) | 160,798 (41,612) | 0.0000 | 0.00000 | 84,524 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| routed | 200 | 3 | 140.3 | 0.702 | 194,073 | 5,963 (653 / 1,920) | 53,004 (9,852) | 0.3230 | 0.00230 | 194,073 | 0.0 | 98.3 | 0.0 | 1.3 | 0.0 |
| shop_rule | 200 | 1 | 91.0 | 0.455 | 113,053 | 18,524 (12,430 / 992) | 121,462 (22,072) | 0.0000 | 0.00000 | 113,053 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| smart_rules | 200 | 3 | 154.3 | 0.772 | 230,220 | 9,217 (0 / 2,390) | 13,603 (1,206) | 0.0000 | 0.00000 | 230,220 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| super_only | 200 | 3 | 146.0 | 0.730 | 213,500 | 5,409 (28 / 1,642) | 34,131 (12,963) | 0.4109 | 0.00281 | 213,500 | 0.0 | 28.7 | 0.0 | 0.0 | 0.0 |
| super_vl | 200 | 3 | 145.7 | 0.728 | 223,680 | 2,858 (70 / 1,635) | 26,503 (10,108) | 0.3449 | 0.00237 | 223,679 | 0.0 | 70.0 | 0.0 | 0.0 | 0.0 |
| ultra_only | 200 | 3 | 151.3 | 0.757 | 232,874 | 5,432 (166 / 2,085) | 14,734 (5,325) | 1.7121 | 0.01131 | 232,872 | 0.0 | 36.0 | 0.0 | 0.0 | 0.0 |

## Bands over repeats on the common subsample

| policy | buyers | repeats | net mean [min, max] | margin mean [min, max] | bookings mean [min, max] |
|---|---|---|---|---|---|
| concession_heuristic | 100 | 1 | 37,637 [37,637, 37,637] | 37,637 [37,637, 37,637] | 32 [32, 32] |
| routed | 100 | 3 | 96,191 [92,360, 99,771] | 96,191 [92,360, 99,771] | 68 [68, 69] |
| shop_rule | 100 | 1 | 60,304 [60,304, 60,304] | 60,304 [60,304, 60,304] | 45 [45, 45] |
| smart_rules | 100 | 3 | 107,353 [106,358, 108,748] | 107,353 [106,358, 108,748] | 75 [75, 76] |
| super_only | 100 | 3 | 97,073 [94,391, 100,915] | 97,074 [94,392, 100,916] | 70 [68, 71] |
| super_vl | 100 | 3 | 102,060 [97,666, 106,361] | 102,060 [97,666, 106,361] | 70 [69, 71] |
| ultra_only | 100 | 3 | 101,716 [98,187, 106,524] | 101,717 [98,188, 106,525] | 71 [69, 73] |

## Where routed loses

Pre-registered test by cell (objection type x trade): only 6 of 28 cells have n >= 10, so "no clear loss" in a cell means not testable, not no loss; in total routed is clearly behind (above).

| type | trade | n | routed net/buyer | best | best net/buyer | gap | interval | clear loss |
|---|---|---|---|---|---|---|---|---|
| timing_slot | roofing | 3 | 2,757 | smart_rules | 5,217 | 2,460 | [-7380.0, -0.0] at 0.9917 | False |
| timing_pause | roofing | 11 | 1,282 | ultra_only | 2,521 | 1,239 | [-3669.53, 0.01] at 0.9917 | False |
| timing_pause | HVAC | 5 | 1,210 | smart_rules | 1,923 | 714 | [-2484.0, -0.0] at 0.9917 | False |
| channel | HVAC | 8 | 1,344 | smart_rules | 1,933 | 589 | [-2237.63, -8.63] at 0.9917 | False |
| price | HVAC | 14 | 854 | smart_rules | 1,283 | 429 | [-1485.27, 166.19] at 0.9917 | False |
| timing_pause | plumbing | 10 | 578 | super_only | 997 | 419 | [-1414.5, 0.45] at 0.9917 | False |
| family | roofing | 7 | 3,888 | super_vl | 4,114 | 226 | [-887.71, 0.0] at 0.9917 | False |
| price | roofing | 9 | 973 | super_vl | 1,049 | 76 | [-420.0, 123.41] at 0.9917 | False |
| family | HVAC | 10 | 1,560 | super_vl | 1,627 | 67 | [-227.8, 0.0] at 0.9917 | False |
| timing_pause | electrical | 8 | 290 | smart_rules | 346 | 56 | [-143.3, -0.0] at 0.9917 | False |
| timing_slot | plumbing | 5 | 101 | super_vl | 122 | 21 | [-84.0, 0.0] at 0.9917 | False |
| price | plumbing | 11 | 506 | super_vl | 524 | 18 | [-72.12, 0.0] at 0.9917 | False |
| channel | electrical | 7 | 1,257 | smart_rules | 1,274 | 18 | [-48.43, -0.0] at 0.9917 | False |
| channel | roofing | 7 | 1,335 | smart_rules | 1,344 | 9 | [-37.72, -0.0] at 0.9917 | False |
| price | electrical | 16 | 413 | super_only | 417 | 4 | [-21.25, 6.67] at 0.9917 | False |
| channel | plumbing | 8 | 538 | smart_rules | 541 | 4 | [-14.25, -0.0] at 0.9917 | False |
| family | plumbing | 9 | 1,184 | smart_rules | 1,184 | 0 | [-0.0, -0.0] at 0.9917 | False |
| ready | HVAC | 7 | 359 | concession_heuristic | 359 | 0 | [-0.0, -0.0] at 0.9917 | False |
| ready | electrical | 7 | 683 | concession_heuristic | 683 | 0 | [-0.0, -0.0] at 0.9917 | False |
| ready | plumbing | 4 | 597 | concession_heuristic | 597 | 0 | [-0.0, -0.0] at 0.9917 | False |
| ready | roofing | 4 | 2,593 | concession_heuristic | 2,593 | 0 | [-0.0, -0.0] at 0.9917 | False |
| timing_slot | HVAC | 7 | 1,376 | smart_rules | 1,376 | 0 | [-0.0, -0.0] at 0.9917 | False |
| timing_slot | electrical | 5 | 103 | smart_rules | 103 | 0 | [-0.0, -0.0] at 0.9917 | False |

Of the 23 cells listed, 7 differ by less than $1 per customer (model spend only); clear losses (n >= 10 and the interval below 0): 0.

Descriptive, by objection type (post-hoc, not pre-registered): routed - smart_rules, net summed over the type's customers [95 % interval].

| type | customers | difference [95 % interval] |
|---|---|---|
| channel | 30 | -$4,931 [-$13,946, -$243] |
| family | 34 | -$737 [-$4,409, +$1,712] |
| opt_out | 10 | $0 [$0, $0] |
| price | 50 | -$5,753 [-$17,273, +$2,116] |
| ready | 22 | -$0.02 [-$0.02, -$0.02] |
| timing_pause | 34 | -$17,346 [-$33,794, -$3,651] |
| timing_slot | 20 | -$7,380 [-$22,140, -$0.03] |

## Compliance

Violations (messages after a customer asked to stop) are 0 for every policy by construction: opt-out customers' words come from published lists, and a shared code layer stops every policy on them. That is not an achievement of any model:

- concession_heuristic: keyword 0 over n = 7; phrase 0 over n = 3
- routed: keyword 0 over n = 7; phrase 0 over n = 3
- shop_rule: keyword 0 over n = 7; phrase 0 over n = 3
- smart_rules: keyword 0 over n = 7; phrase 0 over n = 3
- super_only: keyword 0 over n = 7; phrase 0 over n = 3
- super_vl: keyword 0 over n = 7; phrase 0 over n = 3
- ultra_only: keyword 0 over n = 7; phrase 0 over n = 3

False opt-outs (a customer who did not ask to stop, stopped as an opt-out): 1.
- B139 (timing_pause), routed, repeat 0: stopped by the label model, after the customer wrote "I'll think about it and get back to you."; a feasible customer was lost (oracle margin $202.50). Published as a failure.
- Customer lines that the shared opt-out code layer reads as a stop: 0 of 3321; read as ambiguous: 1.

## Unsure flags (messages sent despite a flag)

| policy | flags by kind |
|---|---|
| routed | label with low confidence 204, message replaced by the template 4 |
| super_only | label with low confidence 49, message replaced by the template 5 |
| super_vl | label with low confidence 128, message replaced by the template 14, move choice failed: code default 7, opt-out word inside a sentence 1 |
| ultra_only | label with low confidence 40, message replaced by the template 32 |

## Integrity

- Errored rows 3 (HTTP 503 after the client's retries), each clean on its second attempt; customers excluded 0; customers kept 200 of 200.
- Token Factory error replies in the run window, all retried: Qwen3-30B-A3B-Instruct-2507 HTTP 503 x31; Nemotron-3-Ultra-550b-a55b HTTP 503 x6; Nemotron-3.5-Super-VL HTTP 502 x1; Nemotron-3.5-Super-VL HTTP 503 x2; Nemotron-3_5-Lightning HTTP 503 x2; nemotron-3-super-120b-a12b HTTP 503 x2; HTTP 429: 0. Reservations kept on network-level retries (held as spend): 13.
- Usage-based cost of the kept conversations: shop $5.5897, buyer writer $0.1439; the ledger window ($5.7776) also holds the errored attempts and the kept reservations.
- Off-arm calls 0, chronology errors 0, buyer text from the template 0, fallback labels 2 (routed), duplicate outcome lines 0.

## Simulator gates (text-blind rule policies)

- Booking rate in 30–80 %: shop_rule 0.455, concession_heuristic 0.330; price customers' booking rate by walkaway tercile [0, 0.029, 0.471].
- Bookings above the walkaway 0, below the floor 0: invariants by construction, integrity checks, not evidence of realism.

## Smoke before the run (mechanics and cost only)

`tournament/smoke-b/`: 38 conversations, 38 clean, spend $0.1334. Two customers per type rather than the population mix; `smart_rules` and `concession_heuristic` were not in it (their only cost is the buyer writer). Not results.

## Second test (v2): wording outside the simulator's vocabulary

**Stopped before the main run: the buyer writer did not pass the pre-registered vocabulary gate, so v2 has no policy results and the headline above stands.**

Why: on v1's 3,133 buyer lines, 97.1 % trip at least one of `smart_rules`' triggers and 79.0 % the rule meant for the customer's type (pre-registered definition). v2 asked whether the rulebook's edge survives when a model that never saw the rules writes the same facts in its own words (`deepseek-ai/DeepSeek-V4-Flash-0731`; the facts come from `env.intent`, which a test checks against v1's instructions on every card and state). Everything else is the v1 code, checked by sha256; only `env.py`, `run.py` and `manifest.py` changed. Ultra was not in v2.

- Pre-registered: manifest `83010fc474bf3a7b0a5aaf3269276ff695f221b75a1055a3c2b915e5f0a03e7d`, pushed 2026-10-24T12:13:59Z (`tournament/v2/PREREG.md`): hypothesis, decision rule (lower bounds of D2 and DiD above 0), budget, and gates L_own at most 59.0 %, fidelity at least 90.0 %, template at most 2.0 %.
- First smoke (52 conversations, `tournament/v2/smoke.83010fc4/`): L_own 71.0 % [60.9 %, 81.2 %] (fail), fidelity 79.7 % [69.6 %, 88.4 %] (fail; the v1-writer reference 90.1 %), template 0.0 %, integrity pass.
- **The gates were amended after the failed smoke**: manifest `0f6f76bc31d04a3197271422f666da014e02c82de8c23dd1010824019e2b899e`, pushed 2026-10-24T12:26:46Z before the second smoke. The judge mapping (household-must-review accepts "undecided" when someone else with a say is mentioned; a landlord counts), L_own without the $-amount pattern (a faithful price line has to state the amount), and the one allowed revision of the writer prompt. Thresholds, hypothesis, decision rules, policies and budget unchanged. Before, after and why, with example lines: `tournament/v2/CHANGES.md`.
- Disclosure: Policy outcomes of the first v2 smoke were seen before the amendment, in two episodes. (1) 2026-10-24T12:14:49Z: the smoke command printed its per-policy table (net and bookings, 52 conversations) and it was read: on the same 23 customers smart_rules $11,308 and super_only $10,773, and super_vl on 2 customers $1,066; this was before the amendment was proposed (12:18:52Z). (2) about 2026-10-24T12:20:09Z, after the blinding condition had been set, a check of the log printed the whole table, so routed (2 customers, $1,066) and shop_rule (2 customers, $982) were also seen. No interval, D2 or DiD was computed. From this manifest on, v2 smokes print no outcome table, and the second smoke and the full run are read only through tools/v2_analysis.py after the full run.
- Second smoke (48 conversations on customers the first smoke did not use, `tournament/v2/smoke/`): fidelity 94.9 % [89.7 %, 98.7 %] (pass; 91.0 % under the first mapping; the v1-writer reference 93.1 %, 90.8 % under the first mapping), template 0.0 % (pass), integrity pass, **L_own 71.8 % [61.5 %, 80.8 %] (fail)**; by type family 65.4 % (n = 26), price 75.0 % (n = 20), ready 40.0 % (n = 10), timing_pause 100.0 % (n = 9), timing_slot 84.6 % (n = 13). For comparison: v1 under the same definition 76.7 %; with the $-amount pattern 76.9 % (v1 79.0 %). "v1 minus 20 points" (56.7 %) would not have been met either.
- What this shows, and what it does not: a writer that never saw the rules, writing the same facts in its own words, still mostly used phrasing the rulebook catches, because the facts themselves (how many weeks, the deadline day, who else decides, the amount) are what the rulebook keys on. So this design could not separate wording from facts. It says nothing either way about whether the rulebook or Nemotron holds up on real customers' wording.
- v2 spend (two smokes, the judge and the references): $0.19 of the $6 budget.

## Third test (v3): rules first, Nemotron only where the rules do not know

**Pre-registered verdict: behind.** rules_then_super - smart_rules, net over 200 customers: -$14,226 [-$31,783, -$1,845] (-$71 [-$159, -$9] per customer); behind: the whole interval is below 0.

The hybrid `rules_then_super` is `smart_rules` unchanged, except where the rules do not know the answer: rule 8 (the customer's latest message says only think / get back / not sure / maybe) and rule 9 (nothing the rules recognise). There the `super_only` agent decides (Nemotron Super labels the reply, Nemotron Super picks a move from the same code-built menu; the floor check and the compliance gate are the same). Silence, rules 1 to 7 and customers the shared opt-out layer has stopped stay with the rules. Both policies ran fresh in one run, 5 times on all 200 customers, with the same buyer writer as v1 and common random numbers: within a repeat both policies meet the same customer words until the hybrid's first hand-off.

- Pre-registered: manifest `01b6dc1f8d24869c0e187c3758629cf8690717cee4fdc8962fdb9a5a7a1fc1a2` (`tournament/v3/PREREG.md`; the pre-registration and `tools/v3_analysis.py` are pinned by sha256 in it), frozen in `b577466` at 2026-10-28T13:59:14Z, pushed 2026-10-28T13:59:16Z. Smoke 28 conversations (mechanics only, no outcome printed); run started 2026-10-28T14:01:45Z, 2000 rows (2000 clean, 0 errored and re-run).
- Disclosure: v3 is the third test of the same simulator. It was designed after v1's results were known (and after v2 stopped at its gate): the hybrid's hand-off branches were chosen from smart_rules' code (rules 8 and 9 of SmartRules), not from v1 outcomes by branch. Before the freeze we counted, on v1's smart_rules transcripts, how often those branches fire (94 of 649 customer lines, a text-level count); no v1 outcome was split by branch.

| policy | customers | bookings | net $ | model $ | violations | false opt-outs (conversations) | off-arm |
|---|---|---|---|---|---|---|---|
| smart_rules | 200 | 154.4 | 231,594 | 0.0000 | 0.0 | 0.00 (0) | 0.0 |
| rules_then_super | 200 | 146.2 | 217,368 | 0.0587 | 0.0 | 0.00 (0) | 0.0 |

Each customer's mean over its repeats, summed over customers; simulated margin on illustrative prices (not market data).

- Hand-offs to Nemotron: 262 in 140 conversations (rule 8: 241, rule 9: 21). Nemotron's move differed from the move the rules would have made in 228 of 262.
- By objection type (descriptive, not adjusted): channel $0 [$0, $0]; family -$50 [-$131, -$0.01]; opt_out $0 [$0, $0]; price $0 [$0, $0]; ready $0 [$0, $0]; timing_pause -$14,303 [-$30,807, -$2,356]; timing_slot +$126 [$0, +$378].
- Against v1's smart_rules rows (independent customer words there, descriptive): -$12,852 [-$30,692, -$1,334].
- Where it lost (post-hoc, descriptive; written after the run): on rule 8 ("I'll think about it"), where the rules ask why, Nemotron chose a check-in in 198 of 241 hand-offs. In this simulator a customer who waits for a date and has not said when stays silent after a check-in (`second_ask/tournament/env.py`, `react`), while asking why makes them name the weeks, which the rules then match. Timing-pause conversations that ended at the five-message cap: rules_then_super 58 of 170, smart_rules 13 of 170.
- Spend: run $0.379, smoke $0.0139 of real Token Factory spend (ceilings $3.00 and $0.30).
- What this shows, and what it does not: the same simulator and the same rulebook written for it, so the room above the rules is small by construction (smart_rules takes most of the oracle margin). It says nothing about live customers.

## What is real, what is seeded, what is simulated

- Real: Token Factory calls and their usage-based cost; Nemotron labels, move choices and message text, on simulated conversations. The $ score depends only on the chosen move: the buyer code never reads the shop's message (`second_ask/tournament/env.py`, `react`), so message wording changes no customer decision and message quality is not measured. The compliance rules in code.
- Seeded: ten paraphrased cases from one homeowner's history set the parameter ranges of 83 of 200 customers (6 of the 10 cases mapped from other trades); the other 117 customers, the type mix, prices and floors are our assumptions. The agent was not tested on the real cases. Speaking styles come from CaSiNo strategy-label counts.
- Simulated: the shop, its estimates, prices, costs and calendar; every buyer's hidden parameters and every buyer decision (code); the buyers' words (a non-NVIDIA model); the owner's one-tap approval (a stub that approves at or above the floor); time (a simulated clock).

## Limitations

1. The customers are simulated: their decisions are code drawn from a seeded card, and their words come from a non-NVIDIA model (`Qwen/Qwen3-30B-A3B-Instruct-2507`). The result says how the policies behave in this simulator, not with live homeowners.
2. `smart_rules` was written by us knowing the simulator's vocabulary and decision code: asking why makes the 49 vague customers clear, and a named amount is 88–97 % of the hidden walkaway. It takes 91% of the oracle, so any policy has little room to grow here. This works for the baseline and says nothing about live customers.
3. All $ are simulated margin on illustrative prices (not market data), summed over 200 customers. The shop and its name are made up.
4. The $ score depends only on the chosen move: message wording changes no customer decision. The quality and persuasiveness of Nemotron's messages were not measured.
5. Real layer: ten paraphrased cases from one homeowner's history set parameter ranges for only 83 of 200 customers, some mapped from other trades. The agent was not tested on the real cases themselves.
6. Silence comes in one kind only: the estimate was never opened (channel customers). There are no customers who opened the estimate and stayed silent.
7. The by-cell test has little power: n >= 10 in only 6 of 28 cells. "No clear loss" means not testable, not no loss.
8. A published failure: 1 false opt-out (B139 routed): the label model read a customer's sentence as a request to stop, and a feasible customer was lost.
9. Opt-out violations are 0 by construction (the shared code layer), not to any model's credit. Whether Fla. Stat. §501.616(6) covers SMS reminders about an estimate is not confirmed; the compliance code is not legal advice.
10. Repeats (K = 3) ran only on a subsample of 100 customers; the other 100 ran once, at temperature 1.0.
11. Before the first manifest, the simulator was calibrated to the 30–80 % booking-rate gate on the text-blind rule policies with fake models: the first dry run gave 15 % and 13 %, and the buyer model was changed to lose customers to time. Nemotron took no part in that calibration. The generator of this page was written after the run and is not in the manifest.
12. Routing deliberately departed from spike S2's rule (Lightning 5/10 and 4/10); the deviation was disclosed. The tournament's pre-registered rule brought back Super labels.
13. `shop_rule` and `concession_heuristic` are our stand-ins, not observed practice of real shops.
14. Ultra is not in the routing, per spike S7 on the spike's gold set; in this tournament, descriptively, ultra_only - super_only = +$19,372 [+$3,050, +$40,433]: S7's choice is not confirmed here.

## Reproduce

```
git checkout 6d66d53                                    # the code of this run
python3 -m pytest -q                                        # offline tests
python3 -m second_ask.tournament.run full --approve 1c231403c8fd5e1109ad1bad04ad411e0234be49e6f5c04a129260347c233a1e   # live, needs NEBIUS_API_KEY
python3 -m second_ask.tournament.run results                 # pre-registered tables from rows.jsonl
git checkout lane-b2 && python3 tools/run_checks.py results-md tournament/full   # this page and README's answer
```
