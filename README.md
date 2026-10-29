# Second Ask

A follow-up assistant for home-service shops. The shop's own code makes every money and timing decision; NVIDIA
Nemotron 3 Super, served by Nebius Token Factory, reads what each customer wrote and drafts the reply. Entry for the
Nebius × NVIDIA Global AI Hackathon (track: Best Apps and Agents).

Demo: https://second-ask.retailbox-automation.com/ (a static replay of recorded runs). Results, with every failure:
[RESULTS.md](RESULTS.md).

## The owner's morning

A roofing, HVAC or plumbing shop sends estimates, and the answers come back in the evening, in any words and sometimes
in Spanish: "no ridge vent, done by Tuesday, not over $2,750", "my landlord has to agree first", "please don't contact
me again". In the morning the owner has to read each one, work out a fair offer, check the calendar and write back.

Second Ask does that part before the owner wakes up:
- Nemotron 3 Super reads each reply into a few facts: the price the customer can live with, items they don't want, a
  deadline, a wait, someone else who must agree, a request to stop, ready to book.
- The shop's code turns those facts into the offer: price from the shop's own menu and never below the owner's floor,
  the earliest open day (the message says so when that is after the customer's deadline), a check-back date, or no
  message at all.
- Nemotron 3 Super writes the message around that offer, and the code checks every dollar amount, percent, date,
  weekday, month and day-count in it; a text that names another price or date, or claims a booking that has not
  happened, is replaced by the code's template.
- Anything below the list price waits for the owner's one tap. Stop requests end the conversation, and no message goes out after one.

A recorded morning (`demo/data/read_first_morning.json`, Token Factory, 2026-10-29, the first run after the last code
change, kept as it came out): 8 replies (7 read by Nemotron, 1 ended by the code's stop words), 6 offers ready, 1 of them
a discount waiting for the owner's tap, 2 stop requests honoured, $0.0065 of Nemotron and 15.6 s of model time for the
whole morning. The replies are synthetic (from our test set below), the shop is made up, and each reply keeps its own test
date (Nov 3–9 on the simulated clock). One of the eight is a reply Nemotron misread in test 4: it took a price the
customer repeated as their limit. In this morning's run it read that reply right. When it does misread that way, the
price falls below the list, and the code holds the offer for the owner's tap instead of sending it.

Why the code, and not the model, decides the price: we tested that four times, writing the rule down before each run.
<!-- evidence:start -->
| Evidence | Where | What it shows |
|---|---|---|
| Main test (v1), pre-registered | `tournament/MANIFEST.json`, [`RESULTS.md`](RESULTS.md) | manifest `1c231403c8fd…` (canonical JSON); frozen in `6d66d53` at 2026-10-24T10:35:38Z, pushed 2026-10-24T10:36:04Z; run 2026-10-24T10:58:31Z to 2026-10-24T11:37:04Z; 2400 conversations; $5.78 of real Token Factory spend |
| Main result | [`RESULTS.md`](RESULTS.md) | routed -$181 [-$319, -$65] per customer, Super-only -$84 [-$215, +$43] per customer against `smart_rules`; default label model after the test: Super |
| Second test (v2): free wording | `tournament/v2/` (PREREG, CHANGES) | Stopped before its main run: the buyer writer did not pass the vocabulary gate (L_own 71.8 % against at most 59.0 %). |
| Third test (v3): rules first, Nemotron where they do not know | `tournament/v3/` (PREREG), [`RESULTS.md`](RESULTS.md) | We also tested rules first, with Nemotron only where the rules don't know: -$71 [-$159, -$9] per customer, behind (third pre-registered test). |
| Fourth test (v4): the rules decide, Nemotron only reads | `second_read/` (PREREG), `data/second_read/`, [`RESULTS.md`](RESULTS.md) | A fourth test had Nemotron only read the customer while the rules decide: same offer as a perfect read on 99.0 % of 600 synthetic replies with Super, 99.33 % with the hardened rules, no clear difference (pre-registered). |
| Limitations | [`RESULTS.md#limitations`](RESULTS.md#limitations) | simulated customers; a rulebook written for the simulator; one published false opt-out; and more |
| Platform spikes | `evidence/spikes/` | pre-registered plans, raw replies, results |
| Replay demo | `demo/data/replay.json` | 8 recorded conversations picked by the rule in `demo/replay.py`, served step for step |
<!-- evidence:end -->

## Is it better than a rule?

<!-- results:start -->
Not in our pre-registered test: against a rulebook we wrote with full knowledge of the simulator, the routed design came out at -$181 [-$319, -$65] per customer [-$36,147 over 200 simulated customers; simulated margin on illustrative prices, not market data], Super-only showed no clear difference (-$84 [-$215, +$43] per customer), and the test switched the default label model to Super. A second pre-registered test, with customers wording the same facts freely, stopped before its main run: the buyer writer did not pass the vocabulary gate (L_own 71.8 % against at most 59.0 %). We also tested rules first, with Nemotron only where the rules don't know: -$71 [-$159, -$9] per customer, behind (third pre-registered test). A fourth test had Nemotron only read the customer while the rules decide: same offer as a perfect read on 99.0 % of 600 synthetic replies with Super, 99.33 % with the hardened rules, no clear difference (pre-registered).
<!-- results:end -->

When Nemotron chose the shop's next move (tests 1 and 3; test 2 stopped at its gate), it never beat a good rulebook,
so the money decisions went back into code. When Nemotron only reads (test 4), it holds up: on 600 synthetic replies
Nemotron 3 Super reached the same offer as a perfect read about as often as a rulebook tuned on the same writer
(99.0 % against 99.33 %, no clear difference), and on those replies Super missed none of the 48 requests to stop,
where the rulebook missed 2 (Lightning missed 10). The replies are generated, so that count shows the kind of miss a
rulebook makes, not a rate you would see in a real inbox. What the tests also support is that the menu matters: asking
why, offering an earlier slot or resending earned more than a naive discount-first rule. Every number is in
[`RESULTS.md`](RESULTS.md), with intervals and the limitations.

## What the agent does

For each customer reply (`read_first/agent.py`, `ReadFirstAgent`):
1. the opt-out check in code runs first (`second_ask/compliance.py`); a stop request in the usual stop words ends the
   conversation with no model call (other wordings are caught by the read in step 2);
2. Nemotron 3 Super reads the reply into Facts (`second_read/contract.py`), with the same prompt and settings that were
   frozen for test 4 (`read_first/reader.py` feeds that reader from a live conversation; a test checks the input is
   byte-identical on all 600 test lines);
3. the shop's code turns the Facts into the offer (`second_read/offer.py`) and maps it to a move on the shop's menu
   (`second_ask/menu.py`, `read_first/decide.py`); prices come only from the menu, never below the owner's floor;
4. Nemotron 3 Super writes the message for that move (`read_first/writer.py`); the code checks amounts, percents,
   dates, weekdays, months, day-counts and booking claims, and sends its template when the text does not match;
5. a price below the list waits for the owner's one tap; quiet hours, the Florida three-a-day cap and the disclosure
   that it is an assistant are enforced in code before anything is sent;
6. once the customer says yes, Nemotron 3 Super books the job with a tool loop (slots, book, record).

Silence (no new reply) is decided by code only: the silence rules of `smart_rules`, no model. The earlier design, where
Nemotron labelled the reason and picked the move (`second_ask/agent.py`), is kept as tested in tests 1–3.

## Nemotron on Token Factory: which model does what

Every model call goes to Nebius Token Factory (`second_ask/tf_client.py`; model IDs in `second_ask/models.py`).

| Step | Model | How it is called | Why this model (measured) |
|---|---|---|---|
| Read the customer's reply into Facts | `nvidia/nemotron-3-super-120b-a12b` | named tool, reasoning off (the frozen test-4 reader) | test 4: same offer as a perfect read on 99.0 % of 600 synthetic replies, 0 of 48 stop requests missed, about $0.66 per 1,000 replies ([RESULTS.md](RESULTS.md)) |
| Decide price, items, date, check-back, stop | none: the shop's code | `second_read/offer.py`, `second_ask/menu.py` | tests 1 and 3: when Nemotron chose the move, a good rulebook beat it |
| Write the message for the chosen offer | `nvidia/nemotron-3-super-120b-a12b` | named tool, reasoning off; text checked by code | in the recorded morning all 6 drafts passed the code's checks; in earlier recordings the code replaced drafts that named a day it had not chosen, claimed a booking, said "as discussed" or left the check-back to the customer |
| Book the accepted job (tool loop: slots, book, record) | `nvidia/nemotron-3-super-120b-a12b` | tools, reasoning on | spike: 20/20 first tool calls parsed (one call each); the full booking loop is tested offline only |
| Other readers in test 4 (descriptive) | Nemotron 3 Ultra, Nemotron 3.5 Super-VL, Nemotron 3.5 Lightning | same reader input | 100.0 %, 99.83 %, 96.17 % on the same 600 replies; Lightning missed 10 of 48 stops |
| Test harness only | `deepseek-ai/DeepSeek-V4-Flash-0731` (test-4 replies), `Qwen/Qwen3-235B-A22B-Instruct-2507` (fidelity judge), `Qwen/Qwen3-30B-A3B-Instruct-2507` (tests 1 and 3 customers; DeepSeek-V4-Flash also wrote test 2's) | JSON | deliberately not NVIDIA, so Nemotron never grades itself |

## Demo

`demo/` serves the demo API. Recorded conversations from the tournament (REPLAY) are the default: real Nemotron replies,
with each step's model, time, cost and response id, picked by a rule written in `demo/replay.py`. LIVE mode, where the
visitor plays the homeowner, is off unless switched on. It is capped per visitor address (5 conversations a day,
6 messages each) and by a shared $5 budget. The shop is a seeded demo shop and the homeowners are simulated.

The published page, https://second-ask.retailbox-automation.com/, is a static build of the replay (`demo/static/`,
`python3 -m demo.static_build`). It opens on the owner's morning: the recorded `read_first/` runs over 8 test-4 replies
(`demo/data/read_first_morning.json`), each with what Nemotron 3 Super read, the offer the shop's code set, the draft and
who did each step; every number on it is built from that file and `data/second_read/test/results.json`, and the words
highlighted in a reply are matched by code, not taken from the model. Below it, the earlier design (test 1, manifest 1c231403) is kept
as a replay. Being static, it serves several visitors at once.

```
python3 -m demo.server                         # REPLAY: http://127.0.0.1:8080/api, /api/scenarios
DEMO_LIVE=1 NEBIUS_API_KEY=... python3 -m demo.server   # also LIVE (POST /api/live/start, /message, /owner, /book)
docker build -t second-ask-demo . && docker run -p 8080:8080 second-ask-demo
```

The LIVE budget lives in the spend ledger. In a container, keep the ledger on a volume (`TF_LEDGER`), or a restart
resets it.

## Run the test yourself

```
python3 -m pytest -q                                     # offline tests (the live ones skip without a key)
git checkout 6d66d53                                     # the code of the main test
python3 -m second_ask.tournament.run full --approve 1c231403c8fd5e1109ad1bad04ad411e0234be49e6f5c04a129260347c233a1e
```

`full` refuses to start unless the manifest matches the code, and when any `SA_*`, `TF_BASE_URL`, `TF_CAPS` or
`TF_LEDGER` variable is set. Every parameter comes from the manifest. Spend is capped in code.

## What is real, what is seeded, what is simulated

- **Real:**
  - the Token Factory calls and their cost;
  - Nemotron's labels, move choices and messages (on simulated conversations; the $ score depends only on the chosen
    move, so message quality was not measured);
  - the compliance rules in code.
- **Seeded:** ten paraphrased home-service negotiations set the parameter ranges of 83 of the 200 simulated customers;
  the rest, the type mix, prices and floors are our assumptions. Speaking styles come from CaSiNo strategy-label counts
  (CC BY 4.0, see `NOTICE`).
- **Simulated:**
  - the shop and its name, estimates, prices and calendar;
  - every customer's hidden parameters and decisions (code);
  - the customers' words (a non-NVIDIA model);
  - the owner's tap;
  - the clock.

All $ are simulated margin on illustrative prices, not market data.

## Platform findings (Token Factory, 2026)

From our spikes and runs; evidence in `evidence/spikes/` and `evidence/platform/`.

1. Spend control. The console has no spend cap or budget alert (the /organization/budgets page returned "Not found" on 2026-10-24), and the billing docs say a negative balance is charged to the card at the start of the month. We had to build a hard cap in our own client. A per-key or per-project cap would help most.
2. Nano reasoning reporting. Nemotron Nano reports no reasoning tokens: completion_tokens_details was null in 83 of 83 Nano responses, so in the usage we receive, hidden reasoning cannot be told apart from visible output. gpt-oss-120b behaves the same way.
3. Nano reasoning switch. reasoning_effort "none" on Nano returns HTTP 200 with empty content and the answer in message.reasoning. chat_template_kwargs.enable_thinking=false works on all four ladder models. One documented switch that works the same way on every Nemotron would help.
4. The reasoning text field differs per model: Nano uses message.reasoning, Lightning and Super fill reasoning and reasoning_content, Ultra fills only reasoning_content.
5. Rate-limit headers do not match enforcement. Super ran 577 calls/min for two minutes against x-ratelimit-limit-requests 300, and x-ratelimit-remaining-requests read 0 on 564 calls with no 429.
6. 503 bursts. In one 38-minute run we saw HTTP 503 x31 on Qwen3-30B, x6 on Ultra, x2 each on Lightning and Super, and 502/503 on Super-VL; the client's retries absorbed most of them, and three conversations still needed a re-run by our runner.
7. Model ID casing is inconsistent (nemotron-3-super-120b-a12b in lower case, Nemotron-3_5-Lightning capitalized with an underscore). A filter on "Super" missed a model.
8. Super-VL lists no supported sampling parameters in /v1/models?verbose=true, and no model supports seed, which makes repeated runs harder to compare.
9. Strict json_schema is accepted on Lightning, which contradicts the OpenAPI field description.
10. Flex tier: service_tier "flex" worked (p50 1.69 s against 0.86 s) and did not decrement the token rate limit, but we could not see flex billed separately.
11. Credits and onboarding. The Devpost activation code is not a promo code ("Invalid promo code" in Top up); the real code arrives by e-mail after a form. The bare promo form answers "Error: no event name found" without the event link parameters. Onboarding requires a card even with promo credit, and the "Company" payer type requires a Tax ID.
12. Usage and billing lag: on 2026-10-23 the Usage page trailed our runs by about two hours.

## Layout

- `read_first/`: the product path: Nemotron reads, the code decides, Nemotron writes; `morning.py` picks the eight replies; `tools/record_read_first.py --morning` records them into `demo/data/read_first_morning.json`.
- `second_read/`: test 4 (contract, offer code, both readers, harness), frozen by `data/second_read/FREEZE.json`.
- `second_ask/`: the agent of tests 1–3, the menu, compliance and the Token Factory client.
  - `classifier.py`: why the customer is quiet.
  - `menu.py`: moves and the owner's floor.
  - `chooser.py`, `compliance.py`, `agent.py`.
  - `tf_client.py`: Token Factory client with a hard spend cap.
  - `tournament/`: customers, environment, policies, scoring, manifest, runner. Pinned by the manifest.
- `tournament/`: the manifests, the customer cards, the runs and their tables; `tournament/v2/` and `tournament/v3/` are the second and third tests.
- `evidence/spikes/`: platform spikes with their pre-registered plans and raw replies.
- `seeds/`: parameter ranges and speaking styles.
- `demo/`: the demo API and the replay set.
- `tools/`: run checks, the generator of `RESULTS.md` and this page's tables, the v2 and v3 analyses, and the live recorder `record_read_first.py`.
- `tests/`, `demo/tests/`: offline tests.

## Notes

- The shop and its name are made up; any real business with the same name is unrelated.
- The compliance rules are applied conservatively and are not legal advice. Whether Fla. Stat. §501.616(6) covers SMS
  follow-ups to an estimate is not confirmed.
- Code: MIT (`LICENSE`). Third-party data and model licences: `NOTICE`.
- Checking the timing: commit times are in `git log`; push times are in GitHub's public events for this repository
  (`https://api.github.com/repos/retailbox-automation/second-ask/events`), for example the v1 freeze `6d66d53` pushed at
  2026-10-24T10:36:04Z, before the run started at 10:58:31Z.
