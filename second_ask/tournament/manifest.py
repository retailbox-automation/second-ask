"""MANIFEST.json: everything the full run is bound to, hashed before the run.

The manifest's SHA-256 is computed over its canonical JSON (sorted keys, no whitespace). It pins the buyer cards, the
seeds, every source file of the package, the policies with their model IDs and call parameters, K, the subsample, the
scoring rules and the sanity gates. Changing any of them changes the hash.
"""
import hashlib, json
from pathlib import Path

from .. import models as M
from ..chooser import CONFIGS
from ..classifier import request as label_request
from . import buyers as B, env as E
from .policies import LLM_POLICIES, RULE_POLICIES

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
    if name in RULE_POLICIES:
        return {"kind": "rule", "doc": {"shop_rule": "discount to the owner's floor on the first objection or silence, "
                                                     "then a reminder every 7 days; offers the quoted slot to a ready buyer",
                                        "concession_heuristic": "concede half of the remaining gap to the floor (rounded to "
                                                                "$5; straight to the floor under $20) at each objection or "
                                                                "silence, every 4 days; quoted slot to a ready buyer"}[name]}
    cfg = LLM_POLICIES[name]
    _, label_params = label_request(cfg.classifier_model, "", cfg.classifier_method)
    label_params = {k: v for k, v in label_params.items() if k not in ("tools", "tool_choice", "response_format")}
    model, params = CONFIGS[cfg.chooser]
    return {"kind": "llm", "label": {"model": cfg.classifier_model, "method": cfg.classifier_method, "params": label_params,
                                     "fallback": [M.SUPER, "tool"]},
            "choose": {"config": cfg.chooser, "model": model, "params": params, "tool_choice": "auto"}}


def build(buyers_path, n_buyers, k_llm, subsample, buyer_model, buyer_params, smoke, projection):
    return {
        "name": "Second Ask tournament",
        "status": "pre-registered; not yet run",
        "buyers": {"file": "tournament/buyers.json", "sha256": sha256_file(buyers_path), "n": n_buyers,
                   "generator_seed": B.GENERATOR_SEED, "type_mix": B.TYPE_MIX, "assumptions": B.ASSUMPTIONS},
        "seeds": {"home_cases": {"file": "seeds/home_cases.json", "sha256": sha256_file(ROOT / "seeds/home_cases.json")},
                  "casino_profiles": {"file": "seeds/casino_profiles.json",
                                      "sha256": sha256_file(ROOT / "seeds/casino_profiles.json"),
                                      "source": "CaSiNo data/casino.json at 2f6ed4a6, sha256 4f2c4560a0070906ed018c3f0766e35f8f8f31b36274ebf35b608621915744ab",
                                      "license": "CC BY 4.0, modified (strategy-label counts only)"}},
        "code": source_hashes(),
        "policies": {name: policy_spec(name) for name in RULE_POLICIES + tuple(LLM_POLICIES)},
        "repeats": {"rule_policies": 1, "llm_policies": k_llm,
                    "why_rule_K1": "rule policies never read message text and every buyer decision is code, so their "
                                   "outcomes are identical across repeats by construction",
                    "llm_subsample_for_extra_repeats": subsample},
        "buyer_simulator": {"model": buyer_model, "params": buyer_params, "role": "writes the buyer's words only; "
                            "decisions are code (second_ask/tournament/env.py: react)", "never_sees": ["walkaway"]},
        "environment": {"max_seller_messages": E.MAX_MOVES, "horizon_days": E.HORIZON_DAYS,
                        "late_window_days": E.LATE_WINDOW_DAYS, "seller_latency_minutes": 10,
                        "owner_tap": "stub approves every concession at or above the floor",
                        "compliance": "second_ask/compliance.py applies to every policy"},
        "scoring": {
            "oracle": "max over packages of min(list, walkaway) - cost, if >= the package floor; slot buyers need an "
                      "open slot by their deadline; opt-out buyers are infeasible",
            "margin": "booked price - cost of the booked package", "given_away": "oracle - margin (booked buyers)",
            "lost": "oracle (feasible buyers not booked); lost_price = the part from price-objection buyers",
            "model_usd": "sum of usage-based cost of the shop's Token Factory calls (buyer simulator excluded)",
            "headline": "$ per successful booking = model_usd / bookings; net = margin - model_usd",
            "intervals": "mean and [min, max] over repeats; paired bootstrap (2000 draws, seed 7) of per-buyer net "
                         "difference vs shop_rule",
            "losing_cells": "objection type x trade cells where routed earns less net per buyer than another policy",
            "compliance": "violations = messages sent after a buyer asked to stop (reported per policy)"},
        "sanity_gates": {"bookings_above_walkaway": "== 0", "bookings_below_floor": "== 0",
                         "booking_rate_per_policy": "between 0.30 and 0.80 (rule policies, dry run and full run)",
                         "price_buyers_booking_rate": "rises with walkaway tercile",
                         "booking_rate_by_type": "spread >= 0.20"},
        "provenance": {
            "real": ["Token Factory calls and their usage-based cost", "Nemotron labels, move choices and message text",
                     "the compliance rules (statutes cited in second_ask/compliance.py)"],
            "seeded": ["buyer archetypes paraphrased from ten real home-service negotiations (seeds/home_cases.json)",
                       "speaking styles from CaSiNo strategy-label counts (seeds/casino_profiles.json)"],
            "simulated": ["the shop, its estimates, prices, costs and calendar", "every buyer's hidden parameters and "
                          "every buyer decision (code)", "the buyers' words (a non-NVIDIA model)", "the owner's one-tap "
                          "approval (a stub that approves at or above the floor)", "time (a simulated clock)"]},
        "smoke": smoke,
        "projection": projection,
        "platform": {"base_url": "https://api.tokenfactory.nebius.com/v1/", "ledger_tag": "b2",
                     "caps_usd": {"b2": 20.0, "total": 40.0}, "concurrency": 8},
    }


def write(manifest, path=ROOT / "tournament" / "MANIFEST.json"):
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    h = digest(manifest)
    (path.parent / "MANIFEST.sha256").write_text(f"{h}  MANIFEST.json (sha256 of canonical JSON: sorted keys, no spaces)\n")
    return h


def verify(path=ROOT / "tournament" / "MANIFEST.json"):
    """The manifest on disk still matches its recorded hash and the current code, buyers and seeds."""
    m = json.loads(path.read_text())
    recorded = (path.parent / "MANIFEST.sha256").read_text().split()[0]
    problems = [] if digest(m) == recorded else ["MANIFEST.json does not match MANIFEST.sha256"]
    if m["code"] != source_hashes():
        problems.append("source files changed since the manifest was written")
    if m["buyers"]["sha256"] != sha256_file(ROOT / m["buyers"]["file"]):
        problems.append("buyers.json changed")
    for s in m["seeds"].values():
        if s["sha256"] != sha256_file(ROOT / s["file"]):
            problems.append(f"{s['file']} changed")
    return recorded, problems
