# Projected cost of the full tournament

Smoke: `tournament/smoke`. Per policy, $ per conversation is the mean over at least 10 clean smoke conversations when there are that many; otherwise (shop calls per conversation from the dry run) x ($ per shop call in the smoke) + (buyer messages per conversation from the dry run) x ($ per buyer message in the smoke) x 1.1. Totals x 1.3. The rule policies have no shop-side model cost.

| Policy | $ per conversation | method |
|---|---|---|
| shop_rule | 0.00009 | dry-run calls x smoke prices (2 smoke conversations of its own) |
| concession_heuristic | 0.00010 | dry-run calls x smoke prices (0 smoke conversations of its own) |
| smart_rules | 0.00008 | dry-run calls x smoke prices (0 smoke conversations of its own) |
| super_only | 0.00250 | dry-run calls x smoke prices (2 smoke conversations of its own) |
| ultra_only | 0.01397 | dry-run calls x smoke prices (2 smoke conversations of its own) |
| routed | 0.00186 | dry-run calls x smoke prices (2 smoke conversations of its own) |
| super_vl | 0.00232 | dry-run calls x smoke prices (2 smoke conversations of its own) |

Buyer message: $0.000044 each (smoke).

| Option | buyers | K | buyers with K repeats | projected $ |
|---|---|---|---|---|
| A | 200 | 3 | 200 | 16.22 |
| B | 200 | 3 | 100 | 10.83 |
| C | 200 | 3 | 60 | 8.67 |
| D | 200 | 1 | 0 | 5.44 |
| E | 120 | 3 | 120 | 9.73 |

Budget for the full run: $12.00. Proposed: option B.
