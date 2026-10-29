# read_first: work in progress (paused by the orchestrator, 2026-10-28)

Done and committed on branch `read-first` (from lane-b2 76d305b): the package (`reader.py`, `decide.py`, `writer.py`,
`agent.py`), the recorder `tools/record_read_first.py`, and tests `tests/test_read_first*.py` (offline, fake client).

Not done yet:
1. Mutation control (brief item 10): mutations (a) skip the opt-out gate, (b) let a model price through, (c) price
   below the floor, (d) offer from empty Facts, (e) silence calling a model. Not run; the tests meant to catch them:
   (a) test_stop_keyword_stops_before_any_model_call, (b) test_model_text_with_another_price_is_replaced_by_the_template,
   (c) test_a_ceiling_below_the_floor_gets_the_floor_never_less, (d) test_competitor_price_gets_the_floor_concession_owner_tap_and_the_slot_offer_chose,
   (e) test_silence_is_decided_by_rules_with_no_model_call. Plan: mutate a copy of the tree, run only these test files.
2. No live smoke (by design: the orchestrator runs it).
3. Not wired into the demo (`demo/live.py` still uses the old Agent; out of scope of this lane).
