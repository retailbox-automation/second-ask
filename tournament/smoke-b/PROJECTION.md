# Projected cost of the full tournament

Smoke: `tournament/smoke-b`. Per policy, $ per conversation is the mean over at least 10 clean smoke conversations when there are that many; otherwise (shop calls per conversation from the dry run) x ($ per shop call in the smoke) + (buyer messages per conversation from the dry run) x ($ per buyer message in the smoke) x 1.1. Totals x 1.3. The rule policies have no shop-side model cost.

| Policy | $ per conversation | method |
|---|---|---|
| shop_rule | 0.00009 | dry-run calls x smoke prices (2 smoke conversations of its own) |
| concession_heuristic | 0.00011 | dry-run calls x smoke prices (0 smoke conversations of its own) |
| smart_rules | 0.00008 | dry-run calls x smoke prices (0 smoke conversations of its own) |
| super_only | 0.00178 | ultra_only's calls per conversation (16 smoke conversations) x this policy's $ per shop call (2 smoke conversations of its own) |
| ultra_only | 0.00650 | measured: mean of 16 clean smoke conversations |
| routed | 0.00142 | measured: mean of 16 clean smoke conversations |
| super_vl | 0.00144 | ultra_only's calls per conversation (16 smoke conversations) x this policy's $ per shop call (2 smoke conversations of its own) |

Buyer message: $0.000045 each (smoke).

| Option | buyers | K | buyers with K repeats | projected $ |
|---|---|---|---|---|
| A | 200 | 3 | 200 | 8.80 |
| B | 200 | 3 | 100 | 5.88 |
| C | 200 | 3 | 60 | 4.72 |
| D | 200 | 1 | 0 | 2.97 |
| E | 120 | 3 | 120 | 5.28 |

Budget for the full run: $12.00. Proposed: option A.

Note (added by hand after the projection, 24.10.2026): the smoke ran two buyers per type, not the population mix
(TYPE_MIX: e.g. price 25 % of buyers but 3 of 16 here, opt-out 5 % but 2 of 16), so this projection is not weighted by
type. `smart_rules` and `concession_heuristic` were not in the smoke: their only cost is the buyer writer, taken from the
dry run. `super_only` and `super_vl` use the calls per conversation of the measured proxy policy. The cost checkpoint
after 20 buyers of the full run re-projected from the real mix (`tournament/full/checkpoint.json`).
