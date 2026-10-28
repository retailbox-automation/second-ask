"""MANIFEST.json: everything the full run is bound to, hashed before the run.

The manifest's SHA-256 is computed over its canonical JSON (sorted keys, no whitespace). It pins the buyer cards, the
seeds, every source file of the package, the policies with their model IDs and call parameters, K, the subsample, the
scoring and decision rules, the integrity rule, the budget and the sanity gates. Changing any of them changes the hash.
`verify` rebuilds every section from the live code and compares, so an environment variable that swaps a model, the
endpoint, the caps or the ledger is caught as well as an edited file.
"""
import hashlib, inspect, json
from pathlib import Path

from .. import tf_client as TF
from ..chooser import CONFIGS
from ..classifier import classify, request as label_request
from . import buyers as B, env as E, runner as R, scoring as S
from .policies import (ALL, HYBRID, LLM_POLICIES, REPEATED, RULE_POLICIES, SMART, UNKNOWN_BRANCHES, LLMPolicy,
                       RulesThenSuper, SmartRules, make)

ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(manifest):
    return hashlib.sha256(canonical(manifest).encode()).hexdigest()


def source_hashes():
    files = sorted(p for p in (ROOT / "second_ask").rglob("*.py"))
    return {str(p.relative_to(ROOT)): sha256_file(p) for p in files}


def policy_spec(name):
    rhythm = LLMPolicy.next_check_days if name in LLM_POLICIES else make(name).next_check_days
    if name in RULE_POLICIES:
        return {"kind": "rule", "reads_text": False, "rhythm_days": rhythm,
                "doc": {"shop_rule": "discount to the owner's floor on the first objection or silence, "
                                     "then a reminder every 7 days; offers the quoted slot to a ready buyer",
                        "concession_heuristic": "naive gap-halving: concede half of the remaining gap to the floor "
                                                "(rounded to $5; straight to the floor under $20) at each objection or "
                                                "silence, every 4 days; quoted slot to a ready buyer. Not the heuristic "
                                                "of any paper"}[name]}
    if name == SMART:
        return {"kind": "rule", "reads_text": True, "rhythm_days": rhythm, "model_usd": 0,
                "spec": inspect.cleandoc(SmartRules.__doc__)}
    if name == HYBRID:
        sup = policy_spec("super_only")
        return {"kind": "hybrid", "reads_text": True, "rhythm_days": RulesThenSuper.next_check_days,
                "spec": inspect.cleandoc(RulesThenSuper.__doc__), "rules": policy_spec(SMART)["spec"],
                "unknown_branches": {"ids": list(UNKNOWN_BRANCHES), "rules": "SmartRules rules 8 (think / get back / not "
                                     "sure / maybe) and 9 (nothing else matched) on the customer's latest message, as "
                                     "SmartRules.choose_with_branch returns them"},
                "model_on_unknown": {"agent": "super_only", "label": sup["label"], "choose": sup["choose"]}}
    cfg = LLM_POLICIES[name]
    _, label_params = label_request(cfg.classifier_model, "", cfg.classifier_method)
    label_params = {k: v for k, v in label_params.items() if k not in ("tools", "tool_choice", "response_format")}
    model, params = CONFIGS[cfg.chooser]
    fallback = inspect.signature(classify).parameters["fallback"].default
    return {"kind": "llm", "reads_text": True, "rhythm_days": rhythm,
            "label": {"model": cfg.classifier_model, "method": cfg.classifier_method, "params": label_params,
                      "fallback": list(fallback) if fallback[0] != cfg.classifier_model else None},
            "choose": {"config": cfg.chooser, "model": model, "params": params, "tool_choice": "auto"}}


def build(buyers_file, n_buyers, k, subsample, buyer_model, smoke, projection, v2=False, v3=False):
    """The whole manifest from its few inputs and the live code (verify calls this again and compares). v2=True: the
    wording-robustness test (with_v2). v3=True: rules first, Nemotron where the rules do not know (with_v3)."""
    m = {
        "name": "Second Ask tournament",
        "status": "pre-registered; not yet run",
        "supersedes": {"sha256": "699e3c92aa887d688df5cd8ceca6cfeaa13bbe2fea684aa6860a1c2cf7a30771",
                       "why": "phase A acceptance (24.10) found defects before any tournament run; changes are listed "
                              "in tournament/CHANGES.md"},
        "buyers": {"file": buyers_file, "sha256": sha256_file(ROOT / buyers_file), "n": n_buyers,
                   "generator_seed": B.GENERATOR_SEED, "type_mix": B.TYPE_MIX, "assumptions": B.ASSUMPTIONS},
        "seeds": {"home_cases": {"file": "seeds/home_cases.json", "sha256": sha256_file(ROOT / "seeds/home_cases.json"),
                                 "content": "per archetype: id, objection type, trade and parameter ranges only"},
                  "casino_profiles": {"file": "seeds/casino_profiles.json",
                                      "sha256": sha256_file(ROOT / "seeds/casino_profiles.json"),
                                      "source": "CaSiNo data/casino.json at 2f6ed4a6, sha256 4f2c4560a0070906ed018c3f0766e35f8f8f31b36274ebf35b608621915744ab",
                                      "license": "CC BY 4.0, modified (strategy-label counts only)"}},
        "code": source_hashes(),
        "policies": {name: policy_spec(name) for name in ALL},
        "repeats": {"k": k, "subsample": subsample, "once": list(RULE_POLICIES), "repeated": list(REPEATED),
                    "rule": "every policy runs once on every buyer; the repeated policies run k-1 more times on the "
                            "subsample",
                    "why_once": "shop_rule and concession_heuristic never read the buyer's words, opt-out buyers' words "
                                "come from code, and every buyer decision is code, so their outcomes are fixed by the "
                                "cards (tests/test_tournament.py::test_text_blind_rules_do_not_depend_on_wording)",
                    "why_smart_rules_repeated": "smart_rules reads the words, which the buyer model samples"},
        "buyer_simulator": {"model": buyer_model, "params": E.BUYER_PARAMS[buyer_model],
                            "role": "writes the buyer's words only; decisions are code (second_ask/tournament/env.py: "
                                    "react)", "never_sees": ["walkaway"],
                            "opt_out_words": {"keywords": E.OPT_OUT_KEYWORDS, "phrases": E.OPT_OUT_PHRASES,
                                              "rule": "an opt-out buyer's message is chosen by code from these lists "
                                                      "(phrase: by buyer id), never written by the model; every one is "
                                                      "caught by the shared code layer compliance.opt_out_status, so "
                                                      "no policy can send after an opt-out on them (violations are 0 "
                                                      "by construction there, reported with n per style)"},
                            "leak_check": "a buyer message that states the walkaway as a number (any number, $ or not, "
                                          "compared as numbers) is rejected, unless the walkaway equals a number on "
                                          "the card"},
        "environment": {"max_seller_messages": E.MAX_MOVES, "horizon_days": E.HORIZON_DAYS,
                        "late_window_days": E.LATE_WINDOW_DAYS, "seller_latency_minutes": 10,
                        "owner_tap": "stub approves every concession at or above the floor",
                        "compliance": "second_ask/compliance.py applies to every policy",
                        "time": "the shop decides no earlier than its own last scheduled send; a customer message is "
                                "stamped no earlier than it; a household that decides books the offer delivered by "
                                "then; chronology_errors are counted and must be 0",
                        "limitation": "views = 0 and silence occur only for channel buyers (no opened-and-silent "
                                      "buyers); named as a limitation in RESULTS"},
        "scoring": {
            "oracle": "max over packages of min(list, walkaway) - cost, if >= the package floor; slot buyers need an "
                      "open slot by their deadline; opt-out buyers are infeasible",
            "menu_oracle": "the same with real menu prices only (fresh offer on each package), at or below the walkaway",
            "margin": "booked price - cost of the booked package", "given_away": "oracle - margin (booked buyers)",
            "given_away_parts": "given_to_full_payers = given_away of buyers whose walkaway >= the full list price; "
                                "menu_gap = oracle - menu_oracle (booked buyers; unreachable with discrete menu "
                                "prices); given_away = menu_gap + (menu_oracle - margin)",
            "lost": "oracle (feasible buyers not booked); lost_price = the part from price-objection buyers",
            "model_usd": "sum of usage-based cost of the shop's Token Factory calls (buyer simulator excluded)",
            "headline": "$ per successful booking = model_usd / bookings; net = margin - model_usd",
            "totals": "per buyer first: each buyer's mean over its repeats; then the sum over buyers (n = buyers). "
                      "Only buyers finished in every policy and not excluded count (runner.final_rows)",
            "intervals": "band: mean and [min, max] over repeats of the totals on the common subsample (the buyers "
                         "every policy ran in every repeat), every policy on the same buyers; paired bootstrap "
                         "(2000 draws, seed 7, 95 %) of the summed per-buyer difference",
            "comparisons": {"main": ["routed vs smart_rules", "super_only vs smart_rules"], "metric": "net",
                            "secondary": "every other policy vs smart_rules and vs shop_rule"},
            "losing_cells": f"objection type x trade cells where routed earns less net per buyer than the best policy "
                            f"in the cell, on per-buyer means; with n per cell, a bootstrap interval of the mean "
                            f"per-buyer difference at 1 - 0.05/m (Bonferroni over the m cells with n >= "
                            f"{S.MIN_CELL_N}); a clear loss needs n >= {S.MIN_CELL_N} and the interval below 0; the "
                            f"best policy is chosen in-sample, which biases toward a loss",
            "compliance": "violations = messages sent after a buyer asked to stop (per policy, n by opt-out style)"},
        "decision_rules": {
            "label_default": f"routed keeps Lightning as the product's default label model only if the 95 % paired-"
                             f"bootstrap lower bound of sum(net routed - net super_only) over buyers is >= "
                             f"-${S.LABEL_RULE_X_USD:,.0f} (about $10 per buyer) and routed is not worse than "
                             f"super_only on violations, unsure and off-arm calls; otherwise the default is Super, as "
                             f"the S2 rule requires (scoring.label_default)",
            "deviation_from_S2": "evidence/spikes/PLAN.md S2: if Lightning scores below 8/10, Super labels. Lightning "
                                 "scored 5/10 and 4/10, yet the routed policy keeps Lightning as its label model: a "
                                 "disclosed deviation, settled by label_default",
            "smoke": "the smoke checks mechanics and cost only: its outcomes change neither the simulator nor the "
                     "policies, except for crashes and integrity bugs; any change means a new manifest and a line in "
                     "tournament/CHANGES.md",
            "s3_s7": "S3 and S7 are not re-run ($740 kept, DECISIONS 2026-10-24); if they ever are, the 23.10 runs "
                     "stay decisive and a re-run only refreshes the evidence"},
        "integrity": {
            "model_fallbacks": "none: the tournament client has no 404 fallbacks (fail closed)",
            "platform_failure": "a TFError after the client's retries aborts the conversation (runner.StrictClient): "
                                "an errored row, platform_failures = 1",
            "counters": {"off_arm_calls": "a call to a model outside the policy's arm for that step, or answered by "
                                          "another model than asked", "label_fallback_calls": "labels made by the "
                                          "fallback label model (part of the policy; reported, not a re-run reason)",
                         "platform_failures": "Token Factory failures after retries",
                         "chronology_errors": "transcript events out of time order (must be 0)"},
            "clean": "status ok, platform_failures 0, off_arm_calls 0",
            "rerun_rule": f"a conversation that is not clean is re-run up to {R.RERUNS} more times; if none of "
                          f"{R.RERUNS + 1} attempts is clean the buyer is excluded from every policy (paired)"},
        "sanity_gates": {"invariants": "bookings_above_walkaway == 0 and bookings_below_floor == 0 hold by "
                                       "construction (env.react, menu.validate): integrity checks, not evidence of "
                                       "realism",
                         "booking_rate_per_policy": "between 0.30 and 0.80 for the text-blind rule policies "
                                                    "(shop_rule, concession_heuristic), dry run and full run",
                         "price_buyers_booking_rate": "rises with walkaway tercile",
                         "booking_rate_by_type": "spread >= 0.20"},
        "provenance": {
            "real": ["Token Factory calls and their usage-based cost", "Nemotron labels, move choices and message text",
                     "the compliance rules (statutes cited in second_ask/compliance.py)"],
            "seeded": ["buyer archetypes: parameter ranges seeded from ten real home-service negotiations "
                       "(seeds/home_cases.json holds only the ranges)",
                       "speaking styles from CaSiNo strategy-label counts (seeds/casino_profiles.json)"],
            "simulated": ["the shop, its estimates, prices, costs and calendar", "every buyer's hidden parameters and "
                          "every buyer decision (code)", "the buyers' words (a non-NVIDIA model)", "the owner's one-tap "
                          "approval (a stub that approves at or above the floor)", "time (a simulated clock)"]},
        "smoke": smoke,
        "smoke_plan": {"repeat_policies": list(SMOKE_REPEAT_POLICIES), "per_type_policies": list(SMOKE_TYPE_POLICIES),
                       "per_type": 2, "jobs": [list(j) for j in R.smoke_plan(B.load(ROOT / buyers_file),
                                                                              SMOKE_REPEAT_POLICIES,
                                                                              SMOKE_TYPE_POLICIES)],
                       "ceiling_usd": R.SMOKE_CEILING_USD, "purpose": "mechanics and cost only (decision_rules.smoke)"},
        "projection": projection,
        "platform": {"base_url": TF.API, "ledger": {"path": LEDGER_PATH, "tag": "b2"},
                     "caps_usd": {"b2": TF.DEFAULT_CAPS["b2"], "total": TF.DEFAULT_CAPS["total"]},
                     "client": {"max_attempts": MAX_ATTEMPTS, "fallbacks": {}},
                     "env_overrides": "smoke and full refuse to start when any SA_* variable, TF_BASE_URL, TF_CAPS or "
                                      "TF_LEDGER is set, and take every parameter from this manifest"},
        "budget": {"order": "buyer by buyer: every policy and repeat of a buyer before the next buyer",
                   "concurrency": R.CONCURRENCY, "ultra_concurrency": R.ULTRA_CONCURRENCY,
                   "checkpoint": {"after_buyers": R.CHECKPOINT_BUYERS, "safety": R.CHECKPOINT_SAFETY,
                                  "rule": "project the whole run from what it has spent (per policy $ per clean "
                                          "conversation x pending conversations x safety, plus spent); stop if above "
                                          "the run ceiling or above what is left of the b2 cap"},
                   "run_ceiling_usd": R.RUN_CEILING_USD,
                   "budget_cap": "a BudgetExceeded from the ledger stops the run; finished conversations are kept"},
    }
    if v3:
        return with_v3(m, buyers_file)
    return with_v2(m, buyers_file) if v2 else m


SMOKE_REPEAT_POLICIES = ("shop_rule", "routed", "super_only", "ultra_only", "super_vl")  # the phase-A smoke
SMOKE_TYPE_POLICIES = ("routed", "ultra_only")
LEDGER_PATH = "~/.local/state/nebius-hackathon/tf-ledger.jsonl"
MAX_ATTEMPTS = 4


def inputs(m):
    """The few build() arguments a manifest records; everything else is rebuilt from the live code."""
    v2, v3 = m.get("test") == V2_TEST, m.get("test") == V3_TEST
    return {"buyers_file": m["buyers"]["file"], "n_buyers": m["buyers"]["n"], "k": m["repeats"]["k"],
            "subsample": m["repeats"]["subsample"], "buyer_model": V1_BUYER_MODEL if v2 else m["buyer_simulator"]["model"],
            "smoke": m["smoke"], "projection": m["projection"], "v2": v2, "v3": v3}


# --- v2: the wording-robustness test (docs: tournament/v2/PREREG.md) ---------------------------------------------------
V2_TEST = "v2 wording robustness"
V1_SHA = "1c231403c8fd5e1109ad1bad04ad411e0234be49e6f5c04a129260347c233a1e"
V1_BUYER_MODEL = "Qwen/Qwen3-30B-A3B-Instruct-2507"
V1_ROWS = "tournament/full/rows.jsonl"
V2_ANALYSIS = "tools/v2_analysis.py"
V2_CHANGED = ("second_ask/tournament/env.py", "second_ask/tournament/manifest.py", "second_ask/tournament/run.py")
V2_POLICIES = ("shop_rule", "concession_heuristic", "smart_rules", "super_only", "routed", "super_vl")  # no ultra_only
V2_SMOKE_REPEAT = ("shop_rule", "routed", "super_only", "super_vl", "smart_rules")
V2_SMOKE_TYPE = ("super_only", "smart_rules")
V2_RUN_CEILING_USD = 4.50
V2_SMOKE_CEILING_USD = 0.50
V2_JUDGE_CEILING_USD = 0.50
V2_JUDGE_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507"
V2_GATES = {"L_own_max": 0.59, "fidelity_min": 0.90, "template_max": 0.02}  # unchanged by the amendment
V2_FIRST_SHA = "83010fc474bf3a7b0a5aaf3269276ff695f221b75a1055a3c2b915e5f0a03e7d"
V2_DISCLOSURE = (
    "Policy outcomes of the first v2 smoke were seen before the amendment, in two episodes. (1) 2026-10-24T12:14:49Z: the "
    "smoke command printed its per-policy table (net and bookings, 52 conversations) and it was read: on the same 23 "
    "customers smart_rules $11,308 and super_only $10,773, and super_vl on 2 customers $1,066; this was before the "
    "amendment was proposed (12:18:52Z). (2) about 2026-10-24T12:20:09Z, after the blinding condition had been set, a "
    "check of the log printed the whole table, so routed (2 customers, $1,066) and shop_rule (2 customers, $982) were also "
    "seen. No interval, D2 or DiD was computed. From this manifest on, v2 smokes print no outcome table, and the second "
    "smoke and the full run are read only through tools/v2_analysis.py after the full run.")


def v2_smoke_round2(cards):
    """The second v2 smoke, on customers the first one did not use: the first three unused customers of each type on
    super_only and smart_rules, and the next unused price and family customer on shop_rule, routed and super_vl."""
    used = {b for b, _, _ in R.smoke_plan(cards, V2_SMOKE_REPEAT, V2_SMOKE_TYPE, per_type=3)}
    jobs = []
    for typ in dict.fromkeys(c.type for c in cards):
        fresh = [c for c in cards if c.type == typ and c.id not in used]
        jobs += [(c.id, p, 0) for c in fresh[:3] for p in V2_SMOKE_TYPE]
    taken = used | {b for b, _, _ in jobs}
    for typ in ("price", "family"):
        c = next(c for c in cards if c.type == typ and c.id not in taken)
        jobs += [(c.id, p, 0) for p in ("shop_rule", "routed", "super_vl")]
    return jobs


def with_v2(m, buyers_file):
    """The v1 manifest with only the buyer writer, the policy list, the ceilings, the gates and the decision rules of the
    v2 test changed. Policies, menu, compliance, scoring, buyers and runner are the code of manifest 1c231403."""
    m = dict(m, test=V2_TEST)
    m.pop("supersedes", None)
    m["baseline"] = {"manifest_sha256": V1_SHA, "commit": "6d66d53", "rows": V1_ROWS,
                     "rows_sha256": sha256_file(ROOT / V1_ROWS),
                     "changed_files": list(V2_CHANGED),
                     "note": "every other file under second_ask/ has the sha256 it has in manifest 1c231403 "
                             "(tests/test_v2.py); v1 is reproduced at commit 6d66d53"}
    m["run_policies"] = list(V2_POLICIES)
    m["buyer_simulator"] = dict(m["buyer_simulator"], writer=E.V2_WRITER, model=E.V2_WRITER_MODEL,
                                params=E.V2_WRITER_PARAMS, prompt=E.V2_SYSTEM,
                                input="the turn's facts from env.intent (the same facts env.instruction gives the v1 "
                                      "writer, as data); never the simulator's instruction sentences or any policy's rules")
    cs = B.load(ROOT / buyers_file)
    m["smoke_plan"] = {"round": 2, "rule": inspect.cleandoc(v2_smoke_round2.__doc__),
                       "jobs": [list(j) for j in v2_smoke_round2(cs)],
                       "first_smoke": {"dir": "tournament/v2/smoke.83010fc4", "manifest_sha256": V2_FIRST_SHA,
                                       "jobs": [list(j) for j in R.smoke_plan(cs, V2_SMOKE_REPEAT, V2_SMOKE_TYPE, per_type=3)],
                                       "gates": "failed: L_own 71.0 % (> 59 %), fidelity 79.7 % (< 90 %); published"},
                       "ceiling_usd": V2_SMOKE_CEILING_USD, "prints_outcomes": False,
                       "purpose": "mechanics, cost and the v2 gates (gates_v2) only"}
    m["budget"] = dict(m["budget"], run_ceiling_usd=V2_RUN_CEILING_USD)
    m["gates_v2"] = {
        **V2_GATES,
        "L_own": "share of model-written buyer lines (type != opt_out) on which the smart_rules rule meant for the "
                 "customer's type fires (tools/v2_analysis.py OWN_RULE), the $-amount pattern excluded; L_own with it "
                 "and the v1 values are reported as descriptive numbers",
        "fidelity": f"share of model-written v2 lines from which an independent judge ({V2_JUDGE_MODEL}, not the writer, "
                    "not NVIDIA, sees no rules) recovers the turn's facts: kind, amount within 5 %, weeks within 1, weekday "
                    "exact, someone else with a say (household member, partner, landlord), item; household-must-review "
                    "accepts the judge's 'undecided' when someone else is mentioned; the share under the first mapping is "
                    "reported too; reference: the v1 writer on 300 sampled turns (seed 7) judged the same way",
        "template": "share of v2 buyer messages that fell back to the v1 template",
        "integrity": "as v1: all smoke rows clean, no errored row after re-runs, no HTTP 429, 0 off-arm calls",
        "on_failure": "one revision of the writer prompt, with a line in tournament/v2/CHANGES.md and a new manifest; a "
                      "second failure stops v2 (reported as: the writer did not pass the gate)"}
    m["decision_rules_v2"] = {
        "primary": "Super-only beats the rulebook on wording outside the simulator's vocabulary only if the 95 % paired-"
                   "bootstrap lower bound of D2 = sum over customers of (net super_only - net smart_rules) in v2 is above 0",
        "robustness": "the rulebook is more brittle than Super-only only if the lower bound of DiD = sum over customers of "
                      "[(super_only - smart_rules) in v2 - (super_only - smart_rules) in v1] is above 0 (each customer's "
                      "mean over its repeats in each test; paired bootstrap over the 200 customers, 2000 draws, seed 7)",
        "continuity": "the same two statistics for routed; secondary, unadjusted: super_vl, and v2 - v1 per policy",
        "otherwise": "no advantage shown on live wording; the v1 headline stands",
        "publication": "published whatever the outcome, both tests in RESULTS.md",
        "not_tested": "ultra_only (left out for budget)"}
    m["amendment"] = {
        "from_manifest_sha256": V2_FIRST_SHA, "why": "the first v2 smoke failed both writer gates; part of the failure was "
        "the measuring instrument, not the writer (tournament/v2/CHANGES.md)",
        "changes": ["judge mapping: household-must-review accepts 'undecided' when someone else is mentioned, and a "
                    "landlord counts as someone with a say", "L_own: the $-amount pattern is out of the gated metric "
                    "(threshold unchanged at 59 %)", "one revision of the writer prompt: plain meaning of each act, no "
                    "added questions, no day, date or time not in the facts"],
        "unchanged": "hypothesis H1, decision rules, metrics, policies, thresholds, budget and ceilings",
        "disclosure": V2_DISCLOSURE, "second_failure": "stop v2: the writer did not pass the gate; the v1 framing stands"}
    m["analysis"] = {"script": V2_ANALYSIS, "sha256": sha256_file(ROOT / V2_ANALYSIS), "judge_model": V2_JUDGE_MODEL,
                     "judge_ceiling_usd": V2_JUDGE_CEILING_USD}
    return m


# --- v3: rules first, Nemotron only where the rules do not know (docs: tournament/v3/PREREG.md) --------------------------
V3_TEST = "v3 rules then Nemotron"
V2_SHA = "0f6f76bc31d04a3197271422f666da014e02c82de8c23dd1010824019e2b899e"
V3_CHANGED = ("second_ask/tournament/policies.py", "second_ask/tournament/env.py", "second_ask/tournament/manifest.py",
              "second_ask/tournament/run.py")
V3_POLICIES = (SMART, HYBRID)
V3_K = 5
V3_RUN_CEILING_USD = 3.00
V3_SMOKE_CEILING_USD = 0.30
V3_PREREG = "tournament/v3/PREREG.md"
V3_ANALYSIS = "tools/v3_analysis.py"
V3_DISCLOSURE = (
    "v3 is the third test of the same simulator. It was designed after v1's results were known (and after v2 stopped at "
    "its gate): the hybrid's hand-off branches were chosen from smart_rules' code (rules 8 and 9 of SmartRules), not "
    "from v1 outcomes by branch. Before the freeze we counted, on v1's smart_rules transcripts, how often those branches "
    "fire (94 of 649 customer lines, a text-level count); no v1 outcome was split by branch.")


def v3_smoke_plan(cards):
    """The v3 smoke: for each type in order of first appearance, the first two vague buyers (the first two buyers when the
    type has fewer than two vague ones), on both v3 policies, repeat 0."""
    jobs = []
    for typ in dict.fromkeys(c.type for c in cards):
        of = [c for c in cards if c.type == typ]
        vague = [c for c in of if c.clarity == "vague"]
        pick = vague[:2] if len(vague) >= 2 else of[:2]
        jobs += [(c.id, p, 0) for c in pick for p in V3_POLICIES]
    return jobs


def with_v3(m, buyers_file):
    """The v1 manifest with only the policies (smart_rules and the hybrid), K, the buyer writer's common random numbers,
    the smoke, the ceilings and the decision rules changed. Menu, compliance, scoring, buyers, writer model and runner
    are the code of manifest 1c231403 (tests/test_v3.py)."""
    m = dict(m, test=V3_TEST)
    m.pop("supersedes", None)
    m["baseline"] = {"manifest_sha256": V1_SHA, "commit": "6d66d53", "rows": V1_ROWS,
                     "rows_sha256": sha256_file(ROOT / V1_ROWS), "v2_manifest_sha256": V2_SHA,
                     "changed_files": list(V3_CHANGED),
                     "note": "every other file under second_ask/ has the sha256 it has in manifest 1c231403 except the "
                             "v2 changes to env.py, manifest.py and run.py; smart_rules makes the same move as in v1 at "
                             "every decision point (tests/test_v3.py); v1 is reproduced at commit 6d66d53"}
    m["policies"] = {name: policy_spec(name) for name in V3_POLICIES}
    m["run_policies"] = list(V3_POLICIES)
    m["repeats"] = {"k": V3_K, "subsample": "all", "once": [], "repeated": list(V3_POLICIES),
                    "rule": f"both policies run {V3_K} times on every buyer"}
    m["buyer_simulator"] = dict(m["buyer_simulator"], common_random_numbers=inspect.cleandoc(E.SharedWriter.__doc__))
    cs = B.load(ROOT / buyers_file)
    m["smoke_plan"] = {"rule": inspect.cleandoc(v3_smoke_plan.__doc__), "jobs": [list(j) for j in v3_smoke_plan(cs)],
                       "ceiling_usd": V3_SMOKE_CEILING_USD, "prints_outcomes": False,
                       "purpose": "mechanics and cost only; prints no outcome table; its rows are not results"}
    m["budget"] = dict(m["budget"], run_ceiling_usd=V3_RUN_CEILING_USD)
    m["scoring"] = dict(m["scoring"], comparisons={"main": [f"{HYBRID} vs {SMART}"], "metric": "net",
                                                     "secondary": "descriptive only: by objection type; hand-offs and "
                                                                  "how often the model's move differed from the rule's; "
                                                                  "against v1 smart_rules rows"})
    m["decision_rules"] = {
        "primary": f"{HYBRID} beats smart_rules only if the 95 % paired-bootstrap lower bound of D3 = sum over the 200 "
                   f"customers of (net {HYBRID} - net smart_rules) is above 0 (each customer's mean over its {V3_K} "
                   f"repeats; 2000 draws, seed 7), and {HYBRID} is not worse than smart_rules on opt-out violations, "
                   f"false opt-outs and off-arm calls (summed per-customer means; strict comparison)",
        "behind": "the upper bound of D3 is below 0",
        "otherwise": "no clear difference (not equivalence)",
        "false_opt_out": "a conversation with a customer whose type is not opt_out that ends because a model read an "
                         "opt-out; counted per policy in the main table",
        "publication": "published whatever the outcome. Beats: the headline becomes rules + Nemotron beat rules alone, "
                       "with the interval; v1 and v2 stay below as history. Otherwise: v1's framing stands and the test is "
                       "reported in one line with its number and interval, and in its own RESULTS section"}
    m["prereg"] = {"file": V3_PREREG, "sha256": sha256_file(ROOT / V3_PREREG)}
    m["analysis"] = {"script": V3_ANALYSIS, "sha256": sha256_file(ROOT / V3_ANALYSIS)}
    m["disclosure"] = V3_DISCLOSURE
    return m


def write(manifest, path=ROOT / "tournament" / "MANIFEST.json"):
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    h = digest(manifest)
    (path.parent / "MANIFEST.sha256").write_text(f"{h}  MANIFEST.json (sha256 of canonical JSON: sorted keys, no spaces)\n")
    return h


def verify(path=ROOT / "tournament" / "MANIFEST.json"):
    """The manifest on disk still matches its recorded hash, and every section equals what the live code (models,
    parameters, endpoint, caps, rules, files) builds now."""
    m = json.loads(path.read_text())
    recorded = (path.parent / "MANIFEST.sha256").read_text().split()[0]
    problems = [] if digest(m) == recorded else ["MANIFEST.json does not match MANIFEST.sha256"]
    try:
        live = json.loads(canonical(build(**inputs(m))))
    except Exception as e:  # noqa: BLE001 - any failure to rebuild is a verification failure
        return recorded, problems + [f"cannot rebuild the manifest from the live code: {type(e).__name__}: {e}"]
    named = {"code": "source files changed since the manifest was written", "buyers": "buyers.json changed"}
    for k in sorted(set(m) | set(live)):
        if m.get(k) == live.get(k):
            continue
        if k == "seeds":
            problems += [f"{s['file']} changed" for n, s in m["seeds"].items() if s != live["seeds"].get(n)]
        elif k == "policies":
            problems += [f"policy {n} differs from the live code (model, parameters or spec)"
                         for n in sorted(set(m["policies"]) | set(live["policies"]))
                         if m["policies"].get(n) != live["policies"].get(n)]
        else:
            problems.append(named.get(k, f"{k} differs from the live code"))
    return recorded, problems
