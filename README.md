# Second Ask

A follow-up agent for home-service shops, built on NVIDIA Nemotron models served by Nebius Token Factory, and a
pre-registered test of whether it beats a good set of rules. Entry for the Nebius × NVIDIA Global AI Hackathon (track:
Best Apps and Agents).

<!-- evidence:start -->
| Evidence | Where | What it shows |
|---|---|---|
| Main test (v1), pre-registered | `tournament/MANIFEST.json`, [`RESULTS.md`](RESULTS.md) | manifest `1c231403c8fd…` (canonical JSON); frozen in `6d66d53` at 2026-10-24T10:35:38Z, pushed 2026-10-24T10:36:04Z; run 2026-10-24T10:58:31Z to 2026-10-24T11:37:04Z; 2400 conversations; $5.78 of real Token Factory spend |
| Main result | [`RESULTS.md`](RESULTS.md) | routed -$181 [-$319, -$65] per customer, Super-only -$84 [-$215, +$43] per customer against `smart_rules`; default label model after the test: Super |
| Second test (v2): free wording | `tournament/v2/` (PREREG, CHANGES) | Stopped before its main run: the buyer writer did not pass the vocabulary gate (L_own 71.8 % against at most 59.0 %). |
| Limitations | [`RESULTS.md#limitations`](RESULTS.md#limitations) | simulated customers; a rulebook written for the simulator; one published false opt-out; and more |
| Platform spikes | `evidence/spikes/` | pre-registered plans, raw replies, results |
| Replay demo | `demo/data/replay.json` | 8 recorded conversations picked by the rule in `demo/replay.py`, served step for step |
<!-- evidence:end -->

## Is it better than a rule?

<!-- results:start -->
Not in our pre-registered test: against a rulebook we wrote with full knowledge of the simulator, the routed design came out at -$181 [-$319, -$65] per customer [-$36,147 over 200 simulated customers; simulated margin on illustrative prices, not market data], Super-only showed no clear difference (-$84 [-$215, +$43] per customer), and the test switched the default label model to Super. A second pre-registered test, with customers wording the same facts freely, stopped before its main run: the buyer writer did not pass the vocabulary gate (L_own 71.8 % against at most 59.0 %).
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

## Demo

`demo/` serves the demo API. Recorded conversations from the tournament (REPLAY) are the default: real Nemotron replies,
with each step's model, time, cost and response id, picked by a rule written in `demo/replay.py`. LIVE mode, where the
visitor plays the homeowner, is off unless switched on. It is capped per visitor address (5 conversations a day,
6 messages each) and by a shared $5 budget. The shop is a seeded demo shop and the homeowners are simulated.

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
