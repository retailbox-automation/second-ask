# read_first: status (2026-10-28)

Done on branch `read-first` (from lane-b2 76d305b): the package (`reader.py`, `decide.py`, `writer.py`, `agent.py`,
`morning.py`), the recorder `tools/record_read_first.py` (`--scenario/--card`, or `--morning`), and offline tests
`tests/test_read_first*.py` (fake client only).

Mutation control (each on a fresh copy of the committed tree, the read_first test files run there; the real tree was
never edited, and a clean run of the full suite follows):

| mutation | failing tests (among others) |
|---|---|
| a skip the opt-out gate before reading | test_stop_keyword_stops_before_any_model_call |
| b let the model text's price through | test_model_text_with_another_price_is_replaced_by_the_template |
| c price below the owner's floor | test_a_ceiling_below_the_floor_gets_the_floor_never_less |
| d offer from empty Facts | test_competitor_price_gets_the_floor_concession_owner_tap_and_the_slot_offer_chose |
| e silence path calling a model | test_silence_is_decided_by_rules_with_no_model_call |
| f skip the date check | test_model_text_with_a_percent_or_another_date_is_replaced |
| g raise the price after a concession | test_a_price_already_conceded_is_never_raised_back_to_list |
| h skip the done-booking claim check | test_text_claiming_a_done_booking_is_replaced_by_the_template |

Open: no live recording yet (the orchestrator runs it through the key launcher); not wired into the demo (out of scope).
