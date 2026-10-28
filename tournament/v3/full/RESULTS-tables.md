# Tables computed from `tournament/v3/full/rows.jsonl` by `run results` (do not edit by hand)

Rows 2000; buyers kept 200 of 200; excluded []; incomplete 0; errored rows 0; re-runs 0.

## Per policy (each buyer's mean over its repeats, summed over buyers)

| policy | buyers | K | bookings | rate | margin $ | given away $ (to full payers / menu gap) | lost $ (price) | model $ | $/booking | net $ | violations | unsure | off-arm | label fallback | platform failures |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rules_then_super | 200 | 5 | 146.2 | 0.731 | 217,368 | 9,068 (50 / 2,390) | 26,604 (1,206) | 0.0587 | 0.00040 | 217,368 | 0.0 | 11.6 | 0.0 | 0.0 | 0.0 |
| smart_rules | 200 | 5 | 154.4 | 0.772 | 231,594 | 9,019 (0 / 2,390) | 12,427 (1,206) | 0.0000 | 0.00000 | 231,594 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |

## Bands over repeats on the common subsample

| policy | buyers | repeats | net mean [min, max] | margin mean [min, max] | bookings mean [min, max] |
|---|---|---|---|---|---|
| rules_then_super | 200 | 5 | 217,368 [216,342, 217,903] | 217,368 [216,342, 217,904] | 146 [145, 147] |
| smart_rules | 200 | 5 | 231,594 [229,820, 238,694] | 231,594 [229,820, 238,694] | 154 [154, 156] |

## Paired bootstrap of the summed per-buyer net difference (95 %)

| comparison | point $ | interval $ | buyers |
|---|---|---|---|
| rules_then_super - smart_rules | -14,226 | [-31,783, -1,845] | 200 |
