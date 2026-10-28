# Nemotron reader: prompt log (dev split only)

Lane L-D of CT2810-B2H. The Nemotron Super reader (`NemotronReader`, `nvidia/nemotron-3-super-120b-a12b`, method `tool`:
named tool `record_facts`, reasoning off, sampling 1.0 / 0.95 as in `second_ask.models`) got the same treatment as the
rule reader: built on `data/second_read/dev.jsonl` only (200 lines), in a 2-hour time box, without touching the shared
input (`readers.reader_input` / `render`), the contract or `offer.py`. The only thing changed is `SYSTEM_PROMPT` in
`second_read/readers/nemotron.py`. The test split did not exist during this work.

- Time box: started 2026-10-28 22:23:51Z (from `date -u`); last scoring pass ended 22:35:27Z; see the end of this file
  for the close.
- Scorer: `python -m second_read.nemotron_dev_score` (concurrency 8, every call through `Client("b2", max_attempts=4)`,
  the shared ledger). It reports the offer match (`offer.match(offer(read), offer(gold))`, the primary metric), exact
  Facts match (every field equal; decider compared by presence, which is all the offer uses), per field, per stratum,
  $ spent and p50 latency. Each pass is one sample at temperature 1.0, so two passes of the same prompt differ by a few
  lines (rounds 2 and 4).
- Budget for this lane: $0.80 on the ledger's b2 tag. Ledger b2 before the first pass 7.0539, after the last 7.8410:
  **$0.787 spent** (sum of reply costs $0.786; the rest are reservations the client keeps on retried calls).

## Rounds (all 200 dev lines)

| Round | Time (UTC) | Offer match | Exact Facts | Reader failures | $ | p50 s | What changed |
|---|---|---|---|---|---|---|---|
| 0 | 22:25:03 | 175/200 = 87.5 % | 96/200 = 48.0 % | 24 | 0.1268 | 1.51 | Baseline: the prompt as shipped at d9784f1. |
| 1 | 22:27:05 | 200/200 = 100.0 % | 185/200 = 92.5 % | 0 | 0.1268 | 1.50 | Empty values as JSON null (all 24 failures were `need_by: "None"`, rejected by the contract and not repaired); the data's reading conventions written into the fields: "by X" is X itself and only "before X" moves a day earlier, "end of next week" is Friday of next week, a bare day of the month / weekday resolved from today, "a couple of weeks" 2, "about a month" 4, "about two months" 8; ready only on "book now" or "when can the crew come"; a price only repeated from the estimate is not a ceiling; on stop every other field stays empty. |
| 2 | 22:28:15 | 200/200 = 100.0 % | 185/200 = 92.5 % | 0 | 0.1301 | 1.46 | ready: false by default, with a list of things that are not ready (price limits and "can you do it for that?", confirming a deadline, removing an item, wanting the job, matching another price, scheduling weeks out). |
| 3 | 22:29:31 | 197/200 = 98.5 % | 179/200 = 89.5 % | 0 | 0.1408 | 1.47 | A worked date example (made-up dates) and "never this week's Friday". Worse: the model invented deadlines on replies without a date and pushed a this-week "end of next week" a week too far. Reverted. |
| 4 | 22:30:35 | 199/200 = 99.5 % | 183/200 = 91.5 % | 0 | 0.1301 | 1.93 | No change: round 2's prompt again, to see the sampling spread (1 offer line apart). |
| 5 | 22:34:36 | 199/200 = 99.5 % | 186/200 = 93.0 % | 0 | 0.1311 | 1.58 | Round 2's prompt plus one sentence in need_by: no day or date named means null; "book it now" / "when can the crew come" is ready, not a deadline (rounds 3 and 4 each had one invented deadline on such a reply). **Final prompt.** |

False stops and missed stops: 0 in every round (16 stop lines on dev).

## Final prompt (round 5) on dev

Per field (200 lines): stop 100.0 % · ceiling_usd 98.5 % · drop_items 100.0 % · need_by 99.5 % · wait_weeks 100.0 % ·
decider (presence) 100.0 % · ready 94.5 %.

Per stratum (offer match / exact Facts): S1 50/50 = 100 % / 47/50 = 94 % · S2 70/70 = 100 % / 69/70 = 98.6 % ·
S3 49/50 = 98 % / 43/50 = 86 % · S4 30/30 = 100 % / 27/30 = 90 %.

## What still goes wrong

- **ready said true too often** (11 lines in round 5): a reply that only drops an item ("just the basic job"), names a
  price limit with "let me know if that works", or asks to confirm a deadline. On dev it never changed the offer,
  because each of those lines also carries a fact that already leads to a slot; on a reply where the only other fact is
  a decider it would turn a one-week check-back into a slot offer.
- **An invented deadline on a "book it, when can the crew come?" reply** (1 line per pass in rounds 3–5, the only offer
  miss in round 5): it adds `meets_deadline` to the slot, so the offer differs.
- **A price repeated from the estimate read as a ceiling** (2–3 lines per pass, e.g. "book ... for $520" where $520 is
  the listed price): harmless when it equals the list price, an unrequested discount when it is an item's price below
  the total.
- Dates: by/before and "end of next week" are right in most passes but slip on single lines between passes (sampling
  at temperature 1.0).

## Where the prompt's wording came from

No whole dev line and no input/output pair from dev is in the prompt. Short phrases in the field definitions were
picked after reading dev failures; these quoted phrases also occur in dev texts: "remove me from your list" (1 line),
"don't reach out again" (1), "I can't go over 560" (1), "if we can keep it at 2500 total" (1), "by the 18th" (1),
"by the end of next week" (4), "by Thursday" (7), "about a week from now" (4), "a couple of weeks" (1), "about three
weeks" (3), "about a month" (1), "book it" (12), "quiero agendar" (1), "¿cuándo pueden venir?" (1), "can you do it for
that?" (2), "can you work with that?" (1). The rule lane adds phrases from dev the same way.

## Descriptive sizes

Not run: after the five Super passes $0.013 of the $0.80 lane budget was left, less than one full dev pass of any other
size. The pre-registered v4 run reads every size on the test split anyway.

## Close

Time box closed 2026-10-28 22:37Z (about 13 minutes of the 2 hours used; the $0.80 budget, not the clock, ended the
work). Full suite at the commit: 617 passed, 14 skipped.
