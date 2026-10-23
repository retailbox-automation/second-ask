# Projected cost of the full tournament

Method: (shop-side calls per conversation, from the dry run over all buyers) x ($ per shop call, measured in the live smoke) + (buyer messages per conversation) x ($ per buyer message, smoke) x 1.1, all x 1.3.
Rule policies have no shop-side model cost; their buyer text costs the same as for the LLM policies.

| Policy | shop calls/conv (dry run) | $ per shop call (smoke) | projected $ per conversation |
|---|---|---|---|
| shop_rule | 0.00 | 0.00000 | 0.00012 |
| concession_heuristic | 0.00 | 0.00000 | 0.00013 |
| super_only | 4.21 | 0.00057 | 0.00325 |
| ultra_only | 4.22 | 0.00329 | 0.01816 |
| routed | 4.18 | 0.00043 | 0.00242 |
| super_vl | 4.24 | 0.00053 | 0.00301 |

Buyer message: $0.000044 each (smoke).

| Option | buyers | K for LLM policies | buyers with K repeats | projected $ |
|---|---|---|---|---|
| A | 200 | 3 | 200 | 16.16 |
| B | 200 | 3 | 100 | 10.79 |
| C | 200 | 3 | 60 | 8.64 |
| D | 200 | 1 | 0 | 5.42 |
| E | 120 | 3 | 120 | 9.69 |

Budget for the full run: $12.00. Proposed: option B.
