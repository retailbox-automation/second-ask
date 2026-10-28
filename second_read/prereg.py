"""Second Read (v4) pre-registered constants. Everything here is recorded in data/second_read/FREEZE.json.

The decision rule text is copied verbatim from V4-DESIGN §6; `analysis.verdict` implements it.
"""
DECISION_RULE = (
    "Nemotron Super reading beats the hardened rules only if the paired difference in match rate is ≥ +10 percentage "
    "points AND the lower bound of the 95 % paired bootstrap interval (2,000 draws, seed 7, over test lines) is > 0, "
    "AND Super's missed-stop count is not higher than the rules'. Upper bound < 0 → behind. Otherwise → no clear "
    "difference (not a tie)."
)
BEATS, BEHIND, UNCLEAR = "beats", "behind", "no clear difference"
MIN_GAIN_PP = 10.0
BOOTSTRAP = {"draws": 2000, "seed": 7}

PRIMARY = ("super", "rules")  # paired difference super - rules
ARMS = ("rules", "super", "lightning", "ultra", "super_vl")
DESCRIPTIVE = ("lightning", "ultra", "super_vl")

# Test split (V4-DESIGN §4): generated only after both readers are frozen.
TEST_SEED = 20261029
TEST_N = 600
DEV_N = 200
STRATA_WEIGHTS = {"S1": 0.25, "S2": 0.35, "S3": 0.25, "S4": 0.15}
STOP_SHARE = 0.08

# Spend (V4-DESIGN §8): the whole v4 under one ceiling, measured on the shared ledger's b2 tag from the first start.
LEDGER_TAG = "b2"
V4_CEILING_USD = 8.00
CONCURRENCY = 8
ULTRA_CONCURRENCY = 3
PLATFORM_RERUNS = 2  # a line whose reader hit a platform error is re-run up to this many times, then counted failed
SMOKE_N = 30
