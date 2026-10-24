# Changes to the v2 pre-registration

Any change after `tournament/v2/MANIFEST.json` was first written means a new manifest, a new run directory and a line
here with the reason. A failed gate allows one revision of the writer prompt (PREREG.md).

## Manifest 1 of v2: 83010fc474bf3a7b0a5aaf3269276ff695f221b75a1055a3c2b915e5f0a03e7d
The pre-registered version (pushed 2026-10-24T12:13:59Z). Its smoke, 52 conversations, ran 12:14:06–12:14:49Z and
failed both writer gates: L_own 71.0 % [60.9, 81.2] against at most 59 %, fidelity 79.7 % [69.6, 88.4] against at least
90 % (the v1-writer reference scored 90.1 %). Integrity passed and template fallbacks were 0 %. Everything is kept in
`tournament/v2/smoke.83010fc4/`, including that manifest and `gates.json`.

## Manifest 2 of v2: 0f6f76bc31d04a3197271422f666da014e02c82de8c23dd1010824019e2b899e
**The gates were amended after a failed smoke.** The measuring instrument had two defects, and the writer prompt gets its
one allowed revision. Unchanged: hypothesis H1, the decision rules (lower bounds of D2 and DiD above 0), metrics,
policies, thresholds (L_own at most 59 %, fidelity at least 90 %, template at most 2 %), budget and ceilings. The second
smoke uses 48 conversations on customers the first smoke did not use, declared in this manifest before it runs.

### Disclosure: policy outcomes of the first smoke were seen before the amendment
1. **2026-10-24T12:14:49Z** (smoke log and run report mtimes): the smoke command printed its per-policy table (net and
   bookings, 52 conversations) and it was read: on the same 23 customers `smart_rules` $11,308 and `super_only` $10,773,
   and `super_vl` on 2 customers $1,066. This was before the amendment was proposed (lane log 12:18:52Z).
2. **About 2026-10-24T12:20:09Z** (lane log), after the orchestrator had set a blinding condition: a check of the log
   printed the whole table, so `routed` (2 customers, $1,066) and `shop_rule` (2 customers, $982) were also seen.

No interval, D2 or DiD was computed from the first smoke. From manifest 2 on, a v2 smoke prints no outcome table, and
the progress log shows only each conversation's key and status. The second smoke and the full run are read only through
`tools/v2_analysis.py` after the full run.

### Amendment 1: judge mapping (instrument defect)
- **Before:** a line written for the act `household_must_review` counted as faithful only when the judge's `kind` was
  `household`. The judge prompt defined `household` as "someone else in the household".
- **After:** the judge's `undecided` also counts, but only when the judge reports that someone else is involved
  (`household` true; that check was already required). The judge prompt now defines it as "someone else who has a say in
  the decision (a family member, a partner, a landlord)". The share under the first mapping is reported next to the new
  one, on the same judge readings, for v2 and for the reference.
- **Why it is a measurement defect, not a fit:** the writer conveyed the fact, and the judge recorded it
  (`household: true`). It also, correctly, read the message as "not decided yet". "We need to review it before we
  decide" is both. Lines from the first smoke that failed only because of this:
  - "Hey, thanks for the estimate! My partner and I need to review it together before we decide, so we'll get back to
    you soon. …" (judge: `undecided`, household true)
  - "Hey, my landlord needs to review the estimate for the GFCI outlets in the kitchen and baths before we move forward.
    …" (judge: `undecided`, household false, because a landlord is not "in the household")

  The same defect hit the v1-writer reference.

### Amendment 2: L_own without the $-amount pattern (instrument defect)
- **Before:** for price customers, L_own counted a line if `smart_rules`' price words or its $-amount pattern matched.
- **After:** the $-amount pattern is out of the gated L_own. L_own with it is reported as a descriptive number, as are
  the v1 values. The threshold stays at **59 %**. For reference only (not a gate): v1 without the pattern is 76.7 %, so
  "v1 minus 20 points" would be 56.7 %.
- **Why it is a measurement defect, not a fit:** the gate asked for wording outside the simulator's vocabulary, but a
  faithful price line has to state the amount the customer can pay; the fidelity gate requires it within 5 %. Any figure
  written with a $ trips the pattern whatever the wording, so the two gates contradicted each other on price lines.
  First-smoke examples (both faithful, both counted as vocabulary leakage):
  - "… I got another quote for less, but I really like your work. I can do $1,700 …"
  - "… I've got a quote from another company for $1,700, so I'm wondering if we can adjust things a bit. …"

  On the first smoke the pattern alone explained most of the gap: without it, L_own is 59.4 %; with it, 71.0 %.

### Amendment 3: the one revision of the writer prompt
- **Before:** the prompt gave the facts as JSON and said to add nothing the facts do not say. It did not explain the
  act names.
- **After:** it adds the plain meaning of each act ("schedule_problem - the visit date offered does not suit them …"),
  asks for no new requests or questions, and allows no day, date or time that is not in the facts. Like before, it does
  not see any rule pattern and does not list words to use or avoid. A test checks that the prompt text itself trips none
  of `smart_rules`' patterns, so the instructions do not prime the rulebook's vocabulary in either direction.
- **Why:** first-smoke writer errors (fidelity failures that were the writer's fault, not the instrument's):
  - an added question: "My partner and I need to review it together before we decide … Which option would you recommend
    for us?"
  - an act misread: `schedule_problem` (the timing does not suit them) written as "it looks good. Could you let us know
    when you might be able to come by …?"
  - an invented time: "Sunday at 2 works for me …"
  - a deadline left out: `done_by` Wednesday written as "sooner than Thursday Nov 12 if possible".

### What a second failure means
If the second smoke fails any gate, v2 stops; it is reported as "the writer did not pass the gate" and the v1 framing
stands. There is no further revision.

## Result of the second smoke: v2 stopped
The second smoke ran 2026-10-24T12:28:04–12:28:58Z on manifest 0f6f76bc: 48 conversations, all clean. An earlier attempt
at 12:26:52Z stopped before any call, when fetching the API key timed out. Gates, 12:29:03–12:32:13Z
(`tournament/v2/smoke/gates.json`):
- fidelity 94.9 % [89.7, 98.7], pass (91.0 % under the first mapping; v1-writer reference 93.1 %, 90.8 % under the first
  mapping);
- template 0 %, pass; integrity, pass;
- **L_own 71.8 % [61.5, 80.8], fail** (at most 59 %). By type: timing_pause 100 %, timing_slot 84.6 %, price 75.0 %,
  family 65.4 %, ready 40.0 %.

Descriptive: L_own with the $-amount pattern 76.9 %, v1 under the amended definition 76.7 %; "v1 minus 20 points"
(56.7 %) would not have been met either.

Per the pre-registered rule this is the second failure: v2 stops before its main run, there is no further revision, and
the v1 framing stands. No policy outcome of the second smoke was read.
