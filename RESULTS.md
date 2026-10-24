# Second Ask: tournament results

Pre-registered manifest `tournament/MANIFEST.json`, sha256 `1c231403c8fd5e1109ad1bad04ad411e0234be49e6f5c04a129260347c233a1e` (changes from the first manifest: `tournament/CHANGES.md`). Run directory `tournament/full/`: started 2026-10-24T10:58:31Z, finished 2026-10-24T11:37:04Z, stop: **complete**; 2403 conversation rows (2400 clean, 3 errored); spend in the run window $5.7776 by the shared ledger.

Every number on this page is computed from `tournament/full/rows.jsonl` and `outcomes.jsonl` by `python3 -m second_ask.tournament.run results` (tables) and `python3 tools/run_checks.py results-md` (this page). Nothing is typed by hand.

## Headline

- Against the shop's old rule (`shop_rule`: discount to the floor at the first objection, then weekly reminders), difference in net summed over 200 buyers [95 % interval]: `routed` $81,020 [$53,395, $110,597]; `super_only` $100,447 [$67,011, $139,369]; `super_vl` $110,626 [$78,469, $148,226]; `ultra_only` $119,819 [$86,014, $159,317].
- Against `smart_rules`, the strongest rule baseline we could write (pre-registered main comparison), difference in net [95 % interval]: `routed` -$36,147 [-$63,856, -$13,051]: behind (interval below 0); `super_only` -$16,720 [-$43,004, $8,643]: no clear difference (interval includes 0); `super_vl` -$6,541 [-$29,232, $15,492]: no clear difference (interval includes 0); `ultra_only` $2,652 [-$13,955, $21,811]: no clear difference (interval includes 0).
- Highest net: `ultra_only` ($232,872); `smart_rules` $230,220; `shop_rule` $113,053.
- Model $ per booking: `routed` $0.0023; `super_only` $0.0028; `super_vl` $0.0024; `ultra_only` $0.0113.
- Default label model by the pre-registered rule: **Super**.

## What was run

- 200 simulated homeowners (cards fixed by seed 20261023, `tournament/buyers.json` sha256 `722dd96f6c4d…`), each with a hidden walkaway price and a reason for going quiet. Every buyer decision is code; a non-NVIDIA model (`Qwen/Qwen3-30B-A3B-Instruct-2507`) only writes the buyer's words and never sees the walkaway.
- 7 policies. `shop_rule` and `concession_heuristic` never read the buyer's words and run once per buyer; `smart_rules` and the four Nemotron policies read them and run 3 times on a fixed subsample of 100 buyers.
- Totals are per buyer first: each buyer's mean over its repeats, then the sum over buyers. Only buyers finished in every policy count.
- **net** = margin earned on booked jobs minus the shop's model spend. **given away** = margin left on the table on booked jobs; **lost** = margin of buyers who could have booked and did not.
- Main comparison (pre-registered): `routed` and `super_only` against `smart_rules`, the strongest rule baseline we could write without a model. Intervals: paired bootstrap over buyers, 2,000 draws, 95 %.

## Main comparisons

| comparison (net, summed over buyers) | difference [95 % interval] | buyers | reading |
|---|---|---|---|
| routed - smart_rules | -$36,147 [-$63,856, -$13,051] | 200 | behind (interval below 0) |
| super_only - smart_rules | -$16,720 [-$43,004, $8,643] | 200 | no clear difference (interval includes 0) |
| ultra_only - smart_rules | $2,652 [-$13,955, $21,811] | 200 | no clear difference (interval includes 0) |
| super_vl - smart_rules | -$6,541 [-$29,232, $15,492] | 200 | no clear difference (interval includes 0) |
| shop_rule - smart_rules | -$117,167 [-$155,302, -$83,655] | 200 | behind (interval below 0) |
| concession_heuristic - smart_rules | -$145,696 [-$187,338, -$109,192] | 200 | behind (interval below 0) |

Against the shop's old rule (`shop_rule`), secondary:

| comparison | difference [95 % interval] | buyers |
|---|---|---|
| concession_heuristic - shop_rule | -$28,530 [-$51,064, -$9,278] | 200 |
| routed - shop_rule | $81,020 [$53,395, $110,597] | 200 |
| smart_rules - shop_rule | $117,167 [$83,655, $155,302] | 200 |
| super_only - shop_rule | $100,447 [$67,011, $139,369] | 200 |
| super_vl - shop_rule | $110,626 [$78,469, $148,226] | 200 |
| ultra_only - shop_rule | $119,819 [$86,014, $159,317] | 200 |

## Default label model (pre-registered rule)

routed (Lightning labels) minus super_only (Super labels), net: -$19,427 [-$43,215, $35] over 200 buyers. The rule keeps Lightning only if the lower bound is at or above -$2,000 and routed is not worse on violations, unsure and off-arm calls: the lower bound misses the margin; violations 0.0 vs 0.0 (not worse); unsure 98.3 vs 28.7 (worse); off_arm_calls 0.0 vs 0.0 (not worse). **Default label model: Super.** Non-inferiority was not shown, so the default stays Super, as the S2 rule requires. "Not worse" is a strict comparison of the summed counts; violations and off-arm calls are 0 by construction in the rows that count (opt-outs are enforced in code; a conversation with an off-arm call is not clean). routed and super_only differ in the label model, the label output format (JSON object vs named tool) and the label fallback, not in the model alone.

Rows 2403; buyers kept 200 of 200; excluded []; incomplete 0; errored rows 3; re-runs 3.

## Per policy (each buyer's mean over its repeats, summed over buyers)

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

## Paired bootstrap of the summed per-buyer net difference (95 %)

| comparison | point $ | interval $ | buyers |
|---|---|---|---|
| concession_heuristic - smart_rules | -145,696 | [-187,338, -109,192] | 200 |
| routed - smart_rules | -36,147 | [-63,856, -13,051] | 200 |
| shop_rule - smart_rules | -117,167 | [-155,302, -83,655] | 200 |
| super_only - smart_rules | -16,720 | [-43,004, 8,643] | 200 |
| super_vl - smart_rules | -6,541 | [-29,232, 15,492] | 200 |
| ultra_only - smart_rules | 2,652 | [-13,955, 21,811] | 200 |
| concession_heuristic - shop_rule | -28,530 | [-51,064, -9,278] | 200 |
| routed - shop_rule | 81,020 | [53,395, 110,597] | 200 |
| smart_rules - shop_rule | 117,167 | [83,655, 155,302] | 200 |
| super_only - shop_rule | 100,447 | [67,011, 139,369] | 200 |
| super_vl - shop_rule | 110,626 | [78,469, 148,226] | 200 |
| ultra_only - shop_rule | 119,819 | [86,014, 159,317] | 200 |

## Cells where routed earns less than the best policy in the cell

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


Of the 23 cells listed above, 7 differ by less than $1 per buyer; in 7 of them routed and the best policy earned the same margin from every buyer, so the gap is model spend only. Cells with a gap of $1 or more: 16; clear losses (n >= 10 and the whole interval below 0): 0. The intervals are in dollars per buyer.

## Compliance

Violations = messages sent after the buyer asked to stop. Opt-out buyers' words come from code (published keyword and phrase lists), and the shared code layer catches every one of them, so 0 there is by construction for every policy, not a model result:

- concession_heuristic: keyword 0 violations over n = 7; phrase 0 violations over n = 3
- routed: keyword 0 violations over n = 7; phrase 0 violations over n = 3
- shop_rule: keyword 0 violations over n = 7; phrase 0 violations over n = 3
- smart_rules: keyword 0 violations over n = 7; phrase 0 violations over n = 3
- super_only: keyword 0 violations over n = 7; phrase 0 violations over n = 3
- super_vl: keyword 0 violations over n = 7; phrase 0 violations over n = 3
- ultra_only: keyword 0 violations over n = 7; phrase 0 violations over n = 3

Conversations of buyers who are not opt-out buyers that still ended on an opt-out (a buyer-model phrase read as a stop by the shared layer):

- concession_heuristic: 0
- routed: 0
- shop_rule: 0
- smart_rules: 0
- super_only: 0
- super_vl: 0
- ultra_only: 0

Buyer-model lines that the opt-out layer reads as a stop (type is not opt_out): 0 of 3321; read as ambiguous: 1.

## Unsure flags (messages sent despite a flag)

| policy | flags by kind |
|---|---|
| routed | label with low confidence 204, message replaced by the template 4 |
| super_only | label with low confidence 49, message replaced by the template 5 |
| super_vl | label with low confidence 128, message replaced by the template 14, move choice failed: code default 7, opt-out word inside a sentence 1 |
| ultra_only | label with low confidence 40, message replaced by the template 32 |

## Integrity

- Errored rows 3, excluded buyers [] (by type -), re-runs 3, buyers kept 200 of 200.
- Token Factory error replies in the run window, by model and code: {'Qwen/Qwen3-30B-A3B-Instruct-2507 HTTP 503': 31, 'nvidia/Nemotron-3-Ultra-550b-a55b HTTP 503': 6, 'nvidia/Nemotron-3.5-Super-VL HTTP 502': 1, 'nvidia/Nemotron-3.5-Super-VL HTTP 503': 2, 'nvidia/Nemotron-3_5-Lightning HTTP 503': 2, 'nvidia/nemotron-3-super-120b-a12b HTTP 503': 2}; reservations kept (network or malformed replies, no usage, or in flight at the end): {'Qwen/Qwen3-30B-A3B-Instruct-2507': 4, 'nvidia/nemotron-3-super-120b-a12b': 4, 'nvidia/Nemotron-3_5-Lightning': 1, 'nvidia/Nemotron-3-Ultra-550b-a55b': 3, 'nvidia/Nemotron-3.5-Super-VL': 1}. Calls by model: {'Qwen/Qwen3-30B-A3B-Instruct-2507': 3168, 'nvidia/nemotron-3-super-120b-a12b': 2171, 'nvidia/Nemotron-3_5-Lightning': 751, 'nvidia/Nemotron-3-Ultra-550b-a55b': 1399, 'nvidia/Nemotron-3.5-Super-VL': 1410}.
- Duplicate outcome lines (interrupted between the two writes): none.
- Usage-based cost of the kept conversations: shop $5.5897, buyer writer $0.1439; the ledger window ($5.7776) also holds the errored attempts' calls and the reservations kept on network-level retries.

| policy | attempts | errored | platform failures | off-arm calls | fallback labels | chronology errors | buyer text from the template |
|---|---|---|---|---|---|---|---|
| concession_heuristic | 200 | 0 | 0 | 0 | 0 | 0 | 0 |
| routed | 400 | 0 | 0 | 0 | 2 | 0 | 0 |
| shop_rule | 201 | 1 | 1 | 0 | 0 | 0 | 0 |
| smart_rules | 400 | 0 | 0 | 0 | 0 | 0 | 0 |
| super_only | 400 | 0 | 0 | 0 | 0 | 0 | 0 |
| super_vl | 401 | 1 | 1 | 0 | 0 | 0 | 0 |
| ultra_only | 401 | 1 | 1 | 0 | 0 | 0 | 0 |

## Simulator gates (text-blind rule policies)

- Booking rate in 30–80 %: {'shop_rule': 0.455, 'concession_heuristic': 0.33} -> {'shop_rule': True, 'concession_heuristic': True}.
- Price buyers' booking rate by walkaway tercile: [0, 0.029, 0.471] (rises: True); spread by type >= 0.20: True.
- Bookings above the walkaway 0, below the floor 0: both hold by construction (integrity checks, not evidence of realism).

## Smoke before the run (mechanics and cost only)

`tournament/smoke-b/`: 38 conversations, 38 clean, 0 errored, spend $0.1334. Not results: its buyers are two per type, not the population mix, and `smart_rules` and `concession_heuristic` were not in it (their only cost is the buyer writer; their mechanics are covered by the dry run and the tests). The 10 phase-A conversations, re-run on the frozen code (phase A ran before three simulator fixes):

- B006 shop_rule: phase A booked=True price=785 end='booked'; now booked=True price=785 end='booked'
- B018 shop_rule: phase A booked=True price=1765 end='booked'; now booked=True price=1765 end='booked'
- B018 super_vl: phase A booked=True price=1480.0 end='booked'; now booked=True price=1480.0 end='booked'
- B006 routed: phase A booked=True price=890.0 end='booked'; now booked=True price=890.0 end='booked'
- B018 ultra_only: phase A booked=True price=1480.0 end='booked'; now booked=True price=1480.0 end='booked'
- B006 super_vl: phase A booked=False price=None end='move cap'; now booked=True price=890.0 end='booked'
- B018 super_only: phase A booked=True price=1480.0 end='booked'; now booked=True price=1480.0 end='booked'
- B018 routed: phase A booked=True price=1480.0 end='booked'; now booked=True price=1480.0 end='booked'
- B006 ultra_only: phase A booked=True price=890.0 end='booked'; now booked=True price=890.0 end='booked'
- B006 super_only: phase A booked=True price=890.0 end='booked'; now booked=True price=890.0 end='booked'

## What is real, what is seeded, what is simulated

- Real: Token Factory calls and their usage-based cost; Nemotron labels, move choices and message text; the compliance rules (statutes cited in second_ask/compliance.py).
- Seeded: buyer archetypes: parameter ranges seeded from ten real home-service negotiations (seeds/home_cases.json holds only the ranges); speaking styles from CaSiNo strategy-label counts (seeds/casino_profiles.json).
- Simulated: the shop, its estimates, prices, costs and calendar; every buyer's hidden parameters and every buyer decision (code); the buyers' words (a non-NVIDIA model); the owner's one-tap approval (a stub that approves at or above the floor); time (a simulated clock).

## Limitations

- The buyers are simulated: their decisions are code drawn from a seeded card, so the numbers say how the policies do against this simulator, not against real homeowners.
- `smart_rules` was written by us against the simulator's own instruction vocabulary ("need it done by", "in about N weeks", named amounts). That favours the baseline and makes the comparison conservative for the Nemotron policies.
- Only channel buyers have an unopened estimate and silence; there are no opened-and-silent buyers.
- Ultra is not routed in (spike S7: Super with reasoning chose at least as well, at about a quarter of the price); `ultra_only` is a separate arm, not part of `routed`. In this tournament `ultra_only`'s total net is $19,372 above `super_only`'s at $1.30 more model spend; that comparison was not pre-registered and is descriptive only.
- given away splits into money given to buyers who would have paid the full list (`given_to_full_payers`) and the gap the menu's discrete prices cannot reach (`menu_gap`).
- In the losing-cells table the best policy of each cell is picked in-sample, which biases toward a loss; a clear loss needs n >= 10 and a Bonferroni interval below 0.
- The compliance rules are applied conservatively in code and are not legal advice; whether Fla. Stat. §501.616(6) covers SMS follow-ups to an estimate is not confirmed.
- Shop and trade names are made up; any real business with the same name is unrelated.

## Reproduce

```
python3 -m pytest -q                                        # offline tests
python3 -m second_ask.tournament.run dry-run                 # fake models, mechanics only
python3 -m second_ask.tournament.run full --approve 1c231403c8fd5e1109ad1bad04ad411e0234be49e6f5c04a129260347c233a1e   # live, needs NEBIUS_API_KEY
python3 -m second_ask.tournament.run results                 # tables from tournament/full/rows.jsonl
python3 tools/run_checks.py results-md tournament/full       # this page
```
