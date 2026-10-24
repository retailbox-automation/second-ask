# Tables computed from `tournament/full/rows.jsonl` by `run results` (do not edit by hand)

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
