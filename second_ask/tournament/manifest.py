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
from .policies import ALL, LLM_POLICIES, REPEATED, RULE_POLICIES, SMART, LLMPolicy, SmartRules, make

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
    cfg = LLM_POLICIES[name]
    _, label_params = label_request(cfg.classifier_model, "", cfg.classifier_method)
    label_params = {k: v for k, v in label_params.items() if k not in ("tools", "tool_choice", "response_format")}
    model, params = CONFIGS[cfg.chooser]
    fallback = inspect.signature(classify).parameters["fallback"].default
    return {"kind": "llm", "reads_text": True, "rhythm_days": rhythm,
            "label": {"model": cfg.classifier_model, "method": cfg.classifier_method, "params": label_params,
                      "fallback": list(fallback) if fallback[0] != cfg.classifier_model else None},
            "choose": {"config": cfg.chooser, "model": model, "params": params, "tool_choice": "auto"}}


def build(buyers_file, n_buyers, k, subsample, buyer_model, smoke, projection):
    """The whole manifest from its few inputs and the live code (verify calls this again and compares)."""
    return {
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


SMOKE_REPEAT_POLICIES = ("shop_rule", "routed", "super_only", "ultra_only", "super_vl")  # the phase-A smoke
SMOKE_TYPE_POLICIES = ("routed", "ultra_only")
LEDGER_PATH = "~/.local/state/nebius-hackathon/tf-ledger.jsonl"
MAX_ATTEMPTS = 4


def inputs(m):
    """The few build() arguments a manifest records; everything else is rebuilt from the live code."""
    return {"buyers_file": m["buyers"]["file"], "n_buyers": m["buyers"]["n"], "k": m["repeats"]["k"],
            "subsample": m["repeats"]["subsample"], "buyer_model": m["buyer_simulator"]["model"], "smoke": m["smoke"],
            "projection": m["projection"]}


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
