# Second Ask

Work in progress for the Nebius × NVIDIA Global AI Hackathon (track: Best Apps and Agents).

A home-service shop sends estimates and customers go quiet. Second Ask reads each reply (or silence), labels why the
customer has not booked (price, timing, family decision, missed message, ready, opt-out), and picks the next move from a
menu the shop's code builds: answer, ask why, check back later, an earlier slot, a resend on the other channel, dropping
an optional item, or a small concession the owner approves with one tap. Prices on the menu never go below the owner's
floor, the messaging rules (quiet hours, message caps, opt-out, disclosure) are enforced in code, and the model only
picks a move id and writes the text. NVIDIA Nemotron models on Nebius Token Factory do the labelling and the choosing.

## Is it better than a rule?

<!-- results:start -->
Not in our pre-registered test: against a rulebook we wrote with full knowledge of the simulator, the routed design came out at -$181 [-$319, -$65] per customer [-$36,147 over 200 simulated customers; simulated margin on illustrative prices, not market data], Super-only showed no clear difference (-$84 [-$215, +$43] per customer), and the test switched the default label model to Super.
<!-- results:end -->

We measured that before writing any claim: a tournament of seven policies against 200 simulated homeowners, with the
cards, code, models, scoring and decision rules hashed into `tournament/MANIFEST.json` before the run. Results, with
intervals and the cells where routing loses: [`RESULTS.md`](RESULTS.md). What changed between the first and the final
manifest, and why: [`tournament/CHANGES.md`](tournament/CHANGES.md). Platform spikes run before any of this:
[`evidence/spikes/RESULTS.md`](evidence/spikes/RESULTS.md).

Not results: the dry run (`tournament/dryrun/`, fake models and template buyers) and the smokes (`tournament/smoke/`,
`tournament/smoke-b/`) check mechanics and cost only. The pre-run smoke used two buyers per type rather than the
population mix, and it did not run `smart_rules` or `concession_heuristic` live: their only cost is the buyer writer,
and their mechanics are covered by the dry run and the tests.

## Layout

- `second_ask/` the agent: `classifier.py` (why is the customer quiet), `menu.py` (moves and the owner's floor),
  `chooser.py` (pick a move, write the message), `compliance.py` (messaging rules), `agent.py`, `tf_client.py` (Token
  Factory client with a hard spend cap), `trace.py`.
- `second_ask/tournament/` buyers, environment, policies, scoring, manifest, runner. Frozen by the manifest.
- `tests/` offline tests (`python3 -m pytest -q`); `tests/test_live.py` calls Token Factory and is skipped without a key.
- `spikes/`, `evidence/` platform spikes with their pre-registered plans and raw replies.
- `seeds/` parameter ranges from ten paraphrased home-service negotiations (they seed 83 of the 200 simulated
  customers; the rest are our assumptions), and speaking styles derived from CaSiNo (see `NOTICE`).
- `tools/run_checks.py` checks on a run directory and the generator of `RESULTS.md`.

## Run

```
python3 -m pytest -q                                   # offline
python3 -m second_ask.tournament.run dry-run           # fake models: mechanics only
NEBIUS_API_KEY=... python3 -m second_ask.tournament.run full --approve <sha256 from tournament/MANIFEST.sha256>
python3 -m second_ask.tournament.run results           # tables from tournament/full/rows.jsonl
python3 tools/run_checks.py results-md tournament/full # RESULTS.md
```

`full` refuses to start unless the manifest still matches the code, and when any `SA_*`, `TF_BASE_URL`, `TF_CAPS` or
`TF_LEDGER` variable is set; every parameter comes from the manifest.

## Notes

- The shop and its name are made up; any real business with the same name is unrelated. All $ in the results are
  simulated margin on illustrative prices (not market data).
- The compliance rules are applied conservatively and are not legal advice; whether Fla. Stat. §501.616(6) covers SMS
  follow-ups to an estimate is not confirmed.
- Third-party data and model licences: see `NOTICE`.
