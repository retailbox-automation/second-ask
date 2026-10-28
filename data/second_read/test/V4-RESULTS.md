# Second Read (v4) results: test split

600 customer replies. Same decision code in every arm; only the reader differs.

## Decision rule (pre-registered, verbatim)

> Nemotron Super reading beats the hardened rules only if the paired difference in match rate is ≥ +10 percentage points AND the lower bound of the 95 % paired bootstrap interval (2,000 draws, seed 7, over test lines) is > 0, AND Super's missed-stop count is not higher than the rules'. Upper bound < 0 → behind. Otherwise → no clear difference (not a tie).

## Primary: Super reader minus hardened rules (paired, every line)

- Difference in match rate: **-0.33 pp**, 95 % paired bootstrap [-1.33, 0.67] over 600 lines
- Missed stops: super 0, rules 2
- **Verdict: no clear difference**

## Every arm

| arm | n | match | 95 % CI | failures | missed stops | false stops | margin / 100 | given away / 100 | lost / 100 | $ / 1,000 | p50 s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| rules | 600 | 99.3 % | [98.3, 99.74] | 0 | 2 | 0 | 101364.42 | 0.0 | 374.0 | 0.0 | 0.0 |
| super | 600 | 99.0 % | [97.84, 99.54] | 0 | 0 | 0 | 101493.5 | 154.17 | 90.75 | 0.6566 | 1.253 |
| lightning | 600 | 96.2 % | [94.31, 97.43] | 15 | 10 | 0 | 101401.0 | 22.25 | 351.92 | 0.0926 | 0.73 |
| ultra | 600 | 100.0 % | [99.36, 100.0] | 0 | 0 | 0 | 101738.42 | 0.0 | 0.0 | 2.8263 | 1.862 |
| super_vl | 600 | 99.8 % | [99.06, 99.97] | 0 | 0 | 0 | 101720.42 | 18.0 | 0.0 | 0.6327 | 1.003 |

Perfect read (gold Facts) for reference: booked 75.33 and margin $101738.42 per 100 replies.

## Per stratum

| stratum | n | rules | super | lightning | ultra | super_vl | super - rules (pp) |
|---|---|---|---|---|---|---|---|
| S1 | 150 | 100.0 % | 97.3 % | 97.3 % | 100.0 % | 99.3 % | -2.67 [-5.33, -0.67] |
| S2 | 210 | 99.5 % | 99.5 % | 94.3 % | 100.0 % | 100.0 % | 0.0 [-1.43, 1.43] |
| S3 | 150 | 98.7 % | 99.3 % | 98.0 % | 100.0 % | 100.0 % | 0.67 [-1.33, 3.33] |
| S4 | 90 | 98.9 % | 100.0 % | 95.6 % | 100.0 % | 100.0 % | 1.11 [0.0, 3.33] |

## Fidelity (descriptive, not a filter)

A blind non-NVIDIA judge's read gives the gold offer on 82.0 % of 600 lines; per stratum: S1 70.0 %, S2 88.1 %, S3 80.67 %, S4 90.0 %.
Sensitivity (judge-agreed lines only, 492): super - rules 0.2 pp [-0.61, 1.02].

