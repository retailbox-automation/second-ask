# Rule reader hardening log (Second Read v4, baseline arm "rules")

Lane L-C of CT2810-B2H. Goal: the strongest rule-based reader we could write in a 2-hour time box, so the comparison
with the Nemotron reader is against a genuinely strong baseline.

Ground rules kept for the whole box:
- no language model and no Token Factory endpoint was called; `second_read/readers/nemotron.py` and every model prompt
  were never opened (the file is in the tree because this branch fast-forwards to the L-A merge, for `offer.py` and the
  shared reader input; it is not imported by `rules.py`);
- tuned on `data/second_read/dev.jsonl` only (200 lines); the test split does not exist yet;
- nothing under `second_ask/` changed; the reader gets exactly `readers.reader_input(case)`, the same input as the
  model reader;
- unit tests (`tests/test_second_read_rules.py`) use our own phrasings written from the conventions in
  `second_read/data.py`, plus a few dev lines as examples.

Scores below: "exact" = all seven Facts fields equal gold (decider compared case-insensitively); "offer" = the shop's
offer from the read Facts equals the offer from gold Facts (`second_read/offer.py`, the primary metric's unit).
Command: `python -m second_read.rules_dev_score`.

Time box: start 2026-10-28 22:20:03 UTC.

| round | time (UTC) | dev exact | dev offer | what changed |
|---|---|---|---|---|
| 0 | 22:25 | 33.5 % | 56.0 % | starting point `SmartRulesPatternReader`: SmartRules' own regexes mapped onto Facts (opt-out layer, `amounts_in` below the total, exact item name + "not needed" words, "N weeks"/"later" phrases, family words, ready words; no date reading) |
| 1 | 22:25 | 90.5 % | 100.0 % | first `RuleReader`: every fact collected independently; stop (opt-out layer + English/Spanish phrases + upper-case STOP), amounts with/without "$", "2k", number words, references to line-item and package prices ignored unless a ceiling word precedes; absolute/relative/Spanish dates resolved from today, "before the Nth" = N-1, "end of next week" = Friday of next week; weeks/months to wait_weeks; optional items matched by name words and synonyms in a clause that says no; deciders (EN/ES) mapped to one word; ready only without a wait or a decider |
| 2 | 22:26 | 100.0 % | 100.0 % | a weekday followed by a month ("sunday nov 22") is not a second, bare-weekday date; "when the crew can come" word order counts as ready |
| 3 | 22:31 | 100.0 % | 100.0 % | dev is saturated, so from here the work is generalisation, driven by our own unit-test phrasings (not dev): items now need a "no" right before them or "isn't needed / no lo necesito" right after them in the same clause ("we want the thermostat, not the purifier" drops only the purifier; lists "ni el termostato ni el purificador" drop both); Spanish opt-outs ("no quiero que me sigan escribiendo", "no vuelvan a contactarme"); "mil ochocientos"; "not until next week" = 1; "-ish" weeks; "antes del 20"; "pasado mañana" = +2; weekday dates only after by/before/para (not "on", "until"); "$750 is more than..." is a reference, not a limit. (A slicing bug in the first version of the new item rule briefly dropped dev to 98.5 %, three Spanish lines; fixed in the same round.) |
| 4 | 22:33 | 100.0 % | 100.0 % | more wordings from our own probes: "nine thousand two fifty", "9 thousand", "6/11" read as day/month when month/day is far off, "a month and a half"/"mes y medio" = 6, in-laws/better half/missus/condo board/property management, "qué fecha tienen", "how soon can you", "agéndalo", "except/rather not/decline/ditch" before an item; "opt out of the surge protector" leaves out an item and is not a stop; "didn't contact me again", "borren mi número" are stops |
| 5 | 22:38 | 100.0 % | 100.0 % | a combinatorial probe of our own templates (all 12 jobs, 1-3 facts, English/Spanish, loose wording; a local script, not dev data, 13,000 lines over 3 seeds) found two failure kinds, both fixed: two items in a list with filler words between them ("the smart thermostat upgrade and the UV light thing") and a ceiling word after the number ("2,450 is my max" when 2,450 equals a line-item price); probe after the fix: 0 misses in 5,000. Also: ordinal words ("the nineteenth", "twenty-first", "el quince de noviembre"), texting shorthand ("u", "ur", "b4", "wen", "wks", "&"), an explicit date beats a bare weekday ("next friday (nov 13)"), "friday next week", European thousands ("$1.850"), "Not interested. Stop." (each sentence goes through the opt-out layer), "another month", "3wks", "don't add/include the X", "no me pongan el X", leaf guards; a booking request inside an "if" clause still counts as ready (a price condition and a booking request go together) |
| 6 | 22:40 | 100.0 % | 100.0 % | Spanish, the smallest dev stratum (30 lines), probed with 19 of our own sentences: "a más tardar el 20 de este mes" (a day number followed by "de" that is not a month), "no contacten más" / "favor de no contactarme" (opt-out without "me"), "semanitas", "vamos a hacerlo". Meaning-preserving perturbations of the dev texts (all lower case without sentence punctuation, "$1,850" as "1850 dollars"/"1,850 bucks", week numbers as words and back, curly apostrophes, all upper case, greetings and sign-offs added; 1,185 changed texts): 0 misses |
| 7 | 22:41 | 100.0 % | 100.0 % | guards against facts read out of small talk (the writer's "chatty"/"stressed" tones): weeks in the past ("it's been a crazy couple of weeks", "2 weeks ago", "hace 2 semanas") are not a wait unless "from now/out" follows; dates after "since/until/after/from/desde" are not a deadline; years after "since/in/built" are not an amount; "let's go with just the circuit" is a choice, not a booking. 7 new tests; probe 5,000 lines and perturbations: 0 misses |
