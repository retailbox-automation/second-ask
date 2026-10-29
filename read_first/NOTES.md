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
| i skip the month-name check | test_live_test_0003_draft_with_months_the_code_never_chose_gets_the_template |
| j skip the "as discussed" check | test_0001_quoted_slot_remains_and_as_discussed_get_the_template |
| k skip the "offered slot called the quoted one" check | test_offered_slot_called_the_quoted_one_is_flagged_only_when_it_is_another_day |
| l skip the "check-in put on the customer" check | test_0003_check_back_put_on_the_customer_gets_the_template |
| m writer falls back to the UTC template of second_ask | test_0006_template_shows_the_customers_local_time |

Move descriptions on the menu (`second_ask.menu.build_menu`, pinned) still print UTC times; only the text sent to the
customer (model text or `read_first.template.template`) is in local time.

Open: no live recording yet (the orchestrator runs it through the key launcher); not wired into the demo (out of scope).
