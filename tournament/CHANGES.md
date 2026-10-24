# Changes to the pre-registered tournament

Every change to the code, the buyer cards, the seeds or the rules after a manifest was written means a new manifest
and a line here, with the reason. The smoke checks mechanics and cost only: its outcomes may lead to a change only for a
crash or an integrity bug (MANIFEST.json, decision_rules.smoke).

## Manifest 2 (supersedes 699e3c92, which was never used for a tournament run)

An acceptance review of phase A (24.10.2026) found defects before any tournament run. Changed:

- **Totals per buyer.** Each buyer's mean over its repeats, then the sum over buyers. Before, the totals of the LLM
  policies averaged a 200-buyer repeat with two 100-buyer repeats and came out about a third too low. [min, max] bands
  over repeats now use only the common 100 buyers, for every policy. Losing cells use per-buyer means, with n per cell
  and a Bonferroni bootstrap interval.
- **Network failures.** The client retries dropped connections, TLS errors, cut-off and non-JSON bodies and replies
  without choices, keeps their reservation, and ends in one error type. A spend-cap stop is no longer absorbed.
  Ledger: a broken line is skipped and reported; a reply without usage keeps its reservation.
- **Runner.** Every finished conversation is written and fsynced at once; a restart skips finished ones; a failing
  conversation becomes an errored row; buyer-by-buyer order; a cost checkpoint after 20 buyers; a separate concurrency
  limit for Ultra; a clean stop on the spend cap.
- **Parameters only from the manifest.** `smoke` and `full` refuse SA_* / TF_BASE_URL / TF_CAPS / TF_LEDGER, and
  `verify` rebuilds every section from the live code, so a swapped model, endpoint or cap no longer passes.
- **Integrity.** No model fallbacks in the tournament; a platform failure aborts the conversation; off-arm calls,
  fallback-label calls, platform failures and chronology errors are counted; a conversation that is not clean is
  re-run up to twice, then its buyer is excluded from every policy.
- **Opt-out wording.** Opt-out buyers' words come from published lists chosen by code, never from the buyer model; every
  listed phrase is caught by the shared code layer, which now also reads curly apostrophes and "take me off your list".
  The two text-blind rule policies' outcomes no longer depend on wording (tested on all 200 buyers).
- **A seventh policy, smart_rules.** The strongest rule baseline we could write without a model: same inputs, menu and
  3-day rhythm as the LLM policies. It is the main baseline: routed and super_only are compared against it.
- **Decision rules written before any result.** When Lightning stays the default label model (a non-inferiority margin
  of $2,000 over 200 buyers); the smoke is mechanics only; S3 and S7 are not re-run. The deviation from the S2 rule is
  stated.
- **Seeds.** `seeds/home_cases.json` keeps only id, objection type, trade and parameter ranges. The generated buyer cards
  are byte-identical (`tournament/buyers.json` sha256 unchanged).
- **Smaller fixes.** Time only moves forward in the simulator (the shop decides no earlier than its own queued send; a
  household books the offer it had actually received). The Florida 3-per-24-hours cap counts messages already
  scheduled, and the 90-day SMS rule is judged at send time. The walkaway leak check compares numbers, not substrings.
  given_away is split into money given to buyers who would have paid the full list and the gap the discrete menu cannot
  reach. The 30–80 % booking-rate gate is computed on the text-blind rule policies only, and the two invariant gates are
  labelled as invariants.
