# Second Ask

A follow-up agent for home-service shops, built on NVIDIA Nemotron models served by Nebius Token Factory, and a
pre-registered test of whether it beats a good set of rules. Entry for the Nebius × NVIDIA Global AI Hackathon (track:
Best Apps and Agents).

Demo: https://second-ask.retailbox-automation.com/ (a static replay of recorded conversations; it opens on the agent's
next move and its dollar effect). Results: [RESULTS.md](RESULTS.md).

<!-- evidence:start -->
| Evidence | Where | What it shows |
|---|---|---|
| Main test (v1), pre-registered | `tournament/MANIFEST.json`, [`RESULTS.md`](RESULTS.md) | manifest `1c231403c8fd…` (canonical JSON); frozen in `6d66d53` at 2026-10-24T10:35:38Z, pushed 2026-10-24T10:36:04Z; run 2026-10-24T10:58:31Z to 2026-10-24T11:37:04Z; 2400 conversations; $5.78 of real Token Factory spend |
| Main result | [`RESULTS.md`](RESULTS.md) | routed -$181 [-$319, -$65] per customer, Super-only -$84 [-$215, +$43] per customer against `smart_rules`; default label model after the test: Super |
| Second test (v2): free wording | `tournament/v2/` (PREREG, CHANGES) | Stopped before its main run: the buyer writer did not pass the vocabulary gate (L_own 71.8 % against at most 59.0 %). |
| Third test (v3): rules first, Nemotron where they do not know | `tournament/v3/` (PREREG), [`RESULTS.md`](RESULTS.md) | We also tested rules first, with Nemotron only where the rules don't know: -$71 [-$159, -$9] per customer, behind (third pre-registered test). |
| Limitations | [`RESULTS.md#limitations`](RESULTS.md#limitations) | simulated customers; a rulebook written for the simulator; one published false opt-out; and more |
| Platform spikes | `evidence/spikes/` | pre-registered plans, raw replies, results |
| Replay demo | `demo/data/replay.json` | 8 recorded conversations picked by the rule in `demo/replay.py`, served step for step |
<!-- evidence:end -->

## Is it better than a rule?

<!-- results:start -->
Not in our pre-registered test: against a rulebook we wrote with full knowledge of the simulator, the routed design came out at -$181 [-$319, -$65] per customer [-$36,147 over 200 simulated customers; simulated margin on illustrative prices, not market data], Super-only showed no clear difference (-$84 [-$215, +$43] per customer), and the test switched the default label model to Super. A second pre-registered test, with customers wording the same facts freely, stopped before its main run: the buyer writer did not pass the vocabulary gate (L_own 71.8 % against at most 59.0 %). We also tested rules first, with Nemotron only where the rules don't know: -$71 [-$159, -$9] per customer, behind (third pre-registered test).
<!-- results:end -->

We wrote the test down before running it, published it with its failures, and let it change the product: the default
label model is now Super, because the cheaper Lightning design did not pass the rule we had set for it. What the test does
support is that the menu matters. Every policy that asks why, offers an earlier slot or resends on the other channel
earned more than a naive discount-first rule, the rulebook included. That gain comes from the menu, not from the model.
Every number is in [`RESULTS.md`](RESULTS.md), with intervals and the limitations.

## What the agent does

A shop sends an estimate and the customer goes quiet. For each reply, or each silence, the agent:
1. labels why the customer has not booked: price, timing, a family decision, a missed message, ready, or opt-out;
2. picks the next move from a menu the shop's own code builds. Options: answer, ask why, check back later, offer an
   earlier slot, resend on the other channel, drop an optional item, or make a small concession that the owner approves
   with one tap;
3. writes the message, and books the job with a tool loop once the customer accepts.

Prices on the menu never go below the owner's floor. The messaging rules (quiet hours, message caps, opt-out, disclosure
that it is an assistant) are enforced in code; the model only picks a move id and writes the text. In the shipped
configuration Nemotron Super labels and chooses.

## Nemotron on Token Factory: which model does what

Every model call goes to Nebius Token Factory (`second_ask/tf_client.py`; model IDs in `second_ask/models.py`).

| Step | Model | How it is called | Why this model (measured) |
|---|---|---|---|
| Label why the customer is quiet | `nvidia/nemotron-3-super-120b-a12b` | named tool, reasoning off | spike S3: label right on 19/20 test states with a named tool; the tournament's pre-registered rule replaced Lightning with Super ([RESULTS.md](RESULTS.md)) |
| Pick the next move from the code-built menu, write the message | `nvidia/nemotron-3-super-120b-a12b` | named tool, reasoning on | spike S7: 0.950 of 60 calls right, against 0.883 for Ultra at about 3.9 times the cost per choice (`evidence/spikes/RESULTS.md`) |
| Book the accepted job (tool loop: slots, book, record) | `nvidia/nemotron-3-super-120b-a12b` | tools, reasoning on | spike: 20/20 tool calls parsed |
| Comparison arms in the tests | Nemotron 3.5 Lightning, Nemotron 3 Ultra, Nemotron 3.5 Super-VL | as above, per policy | the first design labelled with Lightning; Ultra and Super-VL ran as policies of their own |
| Simulated customers' words (test harness only) | `Qwen/Qwen3-30B-A3B-Instruct-2507` (v2: `deepseek-ai/DeepSeek-V4-Flash-0731`) | JSON object | deliberately not NVIDIA, so Nemotron never grades itself |

Prices never come from a model: the shop's code builds the menu and enforces the owner's floor, quiet hours, message caps
and opt-out before anything is sent. In the third test (v3), the rules decide and Super is called only where the rules
do not recognise the reason in the customer's words.

## Demo

`demo/` serves the demo API. Recorded conversations from the tournament (REPLAY) are the default: real Nemotron replies,
with each step's model, time, cost and response id, picked by a rule written in `demo/replay.py`. LIVE mode, where the
visitor plays the homeowner, is off unless switched on. It is capped per visitor address (5 conversations a day,
6 messages each) and by a shared $5 budget. The shop is a seeded demo shop and the homeowners are simulated.

The published page, https://second-ask.retailbox-automation.com/, is a static build of the replay (`demo/static/`,
`python3 -m demo.static_build`). It opens on the agent's next move and its dollar effect against a discount-first reply
on the same menu (our stand-in rule, `demo/static_build.py`), and each step says who did it: the shop's code or which
Nemotron model. Being static, it serves several visitors at once.

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

- `second_ask/`: the agent.
  - `classifier.py`: why the customer is quiet.
  - `menu.py`: moves and the owner's floor.
  - `chooser.py`, `compliance.py`, `agent.py`.
  - `tf_client.py`: Token Factory client with a hard spend cap.
  - `tournament/`: customers, environment, policies, scoring, manifest, runner. Pinned by the manifest.
- `tournament/`: the manifests, the customer cards, the runs and their tables; `tournament/v2/` is the second test.
- `evidence/spikes/`: platform spikes with their pre-registered plans and raw replies.
- `seeds/`: parameter ranges and speaking styles.
- `demo/`: the demo API and the replay set.
- `tools/`: run checks, the generator of `RESULTS.md` and this page's tables, and the v2 analysis.
- `tests/`, `demo/tests/`: offline tests.

## Notes

- The shop and its name are made up; any real business with the same name is unrelated.
- The compliance rules are applied conservatively and are not legal advice. Whether Fla. Stat. §501.616(6) covers SMS
  follow-ups to an estimate is not confirmed.
- Code: MIT (`LICENSE`). Third-party data and model licences: `NOTICE`.
- Checking the timing: commit times are in `git log`; push times are in GitHub's public events for this repository
  (`https://api.github.com/repos/retailbox-automation/second-ask/events`), for example the v1 freeze `6d66d53` pushed at
  2026-10-24T10:36:04Z, before the run started at 10:58:31Z.
