# Second Read (v4): the rules decide, Nemotron reads (pre-registration)

Written before the test replies exist. Frozen by `data/second_read/FREEZE.json` (sha256 of every file under
`second_read/`, the reader prompt, the dev data, the test seed, the strata weights, this file and the decision rule),
committed and pushed before the test split is generated and before any test call.

**v4 is the fourth test of B2.** v1–v3 (RESULTS.md) found that Nemotron choosing the shop's next move did not beat a
rulebook we wrote for our simulator. In that simulator a customer's decision never depends on the words, so reading was
never tested. v4 tests reading alone: the shop's code keeps every money and timing decision, and only the reader differs.
It was designed after v1–v3 were known; the design is in the campaign notes (V4-DESIGN.md) and repeated below.

## Question
On customer replies that neither reader has seen, how often does the shop send the same offer it would send if it had
read the reply perfectly, when the reader is (a) the hardened rulebook and (b) Nemotron 3 Super?

## Readers
- **Rules** (baseline): the patterns of `smart_rules` (`second_ask/tournament/policies.py`) and `amounts_in`
  (`second_ask/menu.py`), rebuilt to return Facts and hardened on the dev split only, by a lane that never called
  Nemotron, in a two-hour time box (it stopped after 24 minutes, 22:20–22:44 UTC on 28.10, when dev, its own unit tests,
  a 5,000-line probe and the dev perturbations were at zero misses). Its brief, time, diff and dev score are published
  (`second_read/RULES-HARDENING-LOG.md`). On dev: rules 100 % same offer, Super 99.0 %; dev is where both were built,
  so these numbers predict nothing about test.
- **Nemotron 3 Super** (`nvidia/nemotron-3-super-120b-a12b`, named tool, reasoning off): one prompt, built on the dev
  split only, in the same time box (22:24–22:37 UTC on 28.10; its $0.80 budget, not the clock, ended it; numbers
  copied from dev lines were then taken out of the prompt and dev re-scored: 198/200). Log:
  `second_read/NEMOTRON-PROMPT-LOG.md`.
- Descriptive only: Nemotron 3.5 Lightning, Nemotron 3 Ultra, Nemotron 3.5 Super-VL (match rate, $ per 1,000 replies,
  p50 latency).

## Data
- 600 test replies generated after the freeze from the 200 estimates of `tournament/buyers.json`, with gold Facts
  sampled by code per stratum: S1 one fact in plain English 25 %, S2 two or three facts 35 %, S3 paraphrase (item
  described without its name, amounts in words, relative dates) 25 %, S4 Spanish or Spanglish 15 %; about 8 % stop
  replies, half without the word "stop".
- Words by `deepseek-ai/DeepSeek-V4-Flash-0731` (not NVIDIA), which sees the estimate, the facts in plain words and a
  style instruction, never any rule.
- Fidelity: `Qwen/Qwen3-235B-A22B-Instruct-2507` (not NVIDIA) extracts the facts blind; its agreement with gold is
  reported, overall and per stratum. **No reply is dropped for it**: the primary analysis uses all 600 replies, because
  keeping only replies an LLM could read would favour an LLM reader. Sensitivity (secondary): the same comparison on the
  replies where the judge agreed with gold.

## Metric
`match` per reply: `offer(read facts) == offer(gold facts)` on stop, dropped items, price and timing, where `offer()` is
the shop's deterministic code (`second_read/offer.py`; prices only from the shop's menu, never below the owner's floor).
A reader that fails to return valid Facts after one retry has not matched that reply. A platform error (the call
itself fails) is re-run up to two times; a reply still unread after that has not matched either, and failures are
reported by kind.

## Decision rule
Nemotron Super reading beats the hardened rules only if the paired difference in match rate (Super − rules) over the
test replies is at least +10 percentage points AND the lower bound of its 95 % paired bootstrap interval (2,000 draws,
seed 7) is above 0 AND Super's missed stops are not more than the rules' missed stops. If the upper bound is below 0:
behind. Otherwise: no clear difference, which is not a tie.

## Secondary (pre-registered)
False stops and missed stops per reader; money per 100 replies (the customer books only if the offer meets every gold
condition; margin at the booked price; money given away against the gold offer); results per stratum; the descriptive
Nemotron sizes.

## Budget
$8.00 of Token Factory for the whole of v4 (dev, smoke, test), in code; the campaign ledger stays under $40.

## Publication
Published whatever the outcome, in RESULTS.md under "Fourth test (v4)". If Super beats the rules by this rule, the
headline becomes: the rules keep the money decisions, Nemotron reads the customer. If not, the v1 headline stands and v4
is reported with its numbers next to v1–v3.
