# v3 results: rules first, Nemotron only where the rules do not know

Computed by `tools/v3_analysis.py` from the run's rows only (manifest sha256 `01b6dc1f8d24869c0e187c3758629cf8690717cee4fdc8962fdb9a5a7a1fc1a2`).

**Verdict (pre-registered rule): behind.** D3 = rules_then_super - smart_rules, net summed over 200 customers [95 % paired bootstrap]: -$14,226 [-$31,783, -$1,845] (-$71 [-$159, -$9] per customer).

| policy | customers | bookings | net $ | model $ | violations | false opt-outs (conversations) | off-arm | unsure |
|---|---|---|---|---|---|---|---|---|
| smart_rules | 200 | 154.4 | 231,594 | 0.0000 | 0.0 | 0.00 (0) | 0.0 | 0.0 |
| rules_then_super | 200 | 146.2 | 217,368 | 0.0587 | 0.0 | 0.00 (0) | 0.0 | 11.6 |

Each customer's mean over its repeats, summed over customers. All $ are simulated margin on illustrative prices (not market data).

## Hand-offs to Nemotron (descriptive)

- 262 hand-offs in 140 conversations; by rule: {'think': 241, 'none': 21}; by type: {'family': 48, 'timing_slot': 15, 'price': 3, 'timing_pause': 196}.
- Nemotron's move differed from the rule's move in 228 of 262; stops: none.
- rule move -> model move: ask_why -> checkin_7 x100; ask_why -> checkin_3 x95; ask_why -> ask_why x34; ask_why -> earlier_slot_1 x12; ask_why -> answer x11; ask_why -> checkin_14 x3; ask_why -> drop_surge_protector x2; answer -> checkin_7 x2; ask_why -> concede_pkg_3 x1; ask_why -> drop_annual_maintenance_plan x1; answer -> checkin_3 x1

## By objection type (descriptive, not adjusted)

| type | D3 [95 %] | customers |
|---|---|---|
| channel | +$0 [+$0, +$0] | 30 |
| family | -$50 [-$131, -$0] | 34 |
| opt_out | +$0 [+$0, +$0] | 10 |
| price | -$0 [-$0, +$0] | 50 |
| ready | +$0 [+$0, +$0] | 22 |
| timing_pause | -$14,303 [-$30,807, -$2,356] | 34 |
| timing_slot | +$126 [-$0, +$378] | 20 |

Against v1's smart_rules rows (independent buyer words, descriptive): -$12,852 [-$30,692, -$1,334].
