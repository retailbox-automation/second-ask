"""v2 wording-robustness test: the pre-registered gates and analysis (sha256 in tournament/v2/MANIFEST.json).

    python3 tools/v2_analysis.py gates tournament/v2/smoke      smoke gates: integrity, template share, L_own, fidelity
                                                                (+ the v1-writer reference on 300 sampled turns); live
                                                                judge calls; writes gates.json; exit 1 on a failed gate
    python3 tools/v2_analysis.py analyze tournament/v2/full     D2, DiD, per-policy v2 - v1, decision rule, L_own and
                                                                fidelity on the full run; live judge; writes analysis.json
Judge calls go through second_ask.tf_client (ledger tag b2) and stop at the manifest's judge ceiling.
"""
import json, random, re, statistics, sys, time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from second_ask.agent import Conversation  # noqa: E402
from second_ask.compliance import Recipient  # noqa: E402
from second_ask.menu import amounts_in  # noqa: E402
from second_ask.tf_client import Client, Ledger, TFError  # noqa: E402
from second_ask.trace import Trace  # noqa: E402
from second_ask.tournament import buyers as B, env as E, manifest as MF, policies as P, runner as R, scoring as S  # noqa: E402

V1_MANIFEST = ROOT / "tournament" / "MANIFEST.json"
V2_MANIFEST = ROOT / "tournament" / "v2" / "MANIFEST.json"
V1_OUTCOMES = ROOT / "tournament" / "full" / "outcomes.jsonl"
CODE_TEXTS = {"We talked it over. Let's book it."}

# ---- leakage: does the smart_rules rule meant for the customer's type fire on the line? ----------------------------
TRIGGERS = {"not_needed": P._NOT_NEEDED, "sooner": P._SOONER, "weeks": P._WEEKS, "family": P._FAMILY,
            "ready": P._READY, "price": P._PRICE, "think": P._THINK}
# Amended after the first v2 smoke (tournament/v2/CHANGES.md): the $-amount pattern is out of the gated L_own, because a
# faithful line has to state the amount a price customer names; OWN_RULE_WITH_AMOUNT is kept as a descriptive number.
OWN_RULE = {"timing_slot": {"sooner"}, "timing_pause": {"weeks", "later"}, "price": {"price"},
            "family": {"family"}, "ready": {"ready"}, "channel": {"ready"}}
OWN_RULE_WITH_AMOUNT = dict(OWN_RULE, price={"amount", "price"})


def fires(text):
    """The smart_rules triggers that match a customer line (the same patterns SmartRules.choose uses)."""
    t = text.translate(str.maketrans({"’": "'", "‘": "'"})).lower()
    out = {k for k, rx in TRIGGERS.items() if rx.search(t)}
    if any(re.search(pat, t) for pat in P._LATER):
        out.add("later")
    if amounts_in(t):
        out.add("amount")
    return out


def v1_lines(outcomes):
    """(type, text) of every model-written customer line of the v1 run (type != opt_out)."""
    out = []
    for o in outcomes:
        if o["type"] == "opt_out":
            continue
        out += [(o["type"], e["text"]) for e in o["transcript"] if e["who"] == "customer" and e["text"] not in CODE_TEXTS]
    return out


def v2_turns(outcomes):
    """Every v2 buyer message with its facts: (type, turn dict)."""
    return [(o["type"], t) for o in outcomes if o["type"] != "opt_out" for t in o.get("buyer_turns") or []]


def _share_ci(flags, draws=2000, seed=7):
    if not flags:
        return {"share": None, "n": 0}
    rng = random.Random(seed)
    boots = sorted(sum(rng.choice(flags) for _ in flags) / len(flags) for _ in range(draws))
    return {"share": round(sum(flags) / len(flags), 4), "lo": round(boots[int(0.025 * draws)], 4),
            "hi": round(boots[int(0.975 * draws) - 1], 4), "n": len(flags)}


def leakage(lines):
    """L_own (gated: the amount pattern excluded), L_own_with_amount and L_any (descriptive), overall and by type, with
    bootstrap intervals over lines."""
    own = [bool(fires(text) & OWN_RULE.get(typ, set())) for typ, text in lines]
    with_amount = [bool(fires(text) & OWN_RULE_WITH_AMOUNT.get(typ, set())) for typ, text in lines]
    anyf = [bool(fires(text)) for _, text in lines]
    by = defaultdict(list)
    for (typ, _), f in zip(lines, own):
        by[typ].append(f)
    return {"L_own": _share_ci(own), "L_own_with_amount": _share_ci(with_amount), "L_any": _share_ci(anyf),
            "L_own_by_type": {t: _share_ci(v) for t, v in sorted(by.items())}}


# ---- fidelity: can an independent judge recover the turn's facts from the line? -------------------------------------
JUDGE_SYSTEM = (
    "You read one text message that a homeowner sent to a home-service company about an estimate. Report what the "
    "message says, as a JSON object with exactly these fields: \"kind\": one of \"go_ahead\" (wants to go ahead, or asks "
    "when they can come), \"cost_too_high\" (the cost is a problem), \"wait_until_later\" (wants the job, but later), "
    "\"need_sooner\" (the timing does not work, or they need it sooner), \"household\" (someone else in the household "
    "has to agree or has given a view), \"undecided\" (still thinking, no reason given), \"other\"; \"amount\": a dollar "
    "amount they say they could pay, as a number, or null; \"weeks\": how many weeks they want to wait, as a number, or "
    "null; \"weekday\": the weekday they need the work done by, or null; \"household\": true if they mention someone else "
    "who has a say in the decision (a family member, a partner, a landlord), else false; \"item\": a line item they say is "
    "not needed, or null. Answer only with the JSON object.")
JUDGE_PARAMS = {"temperature": 0.0, "max_tokens": 200}
ACCEPT = {"saw_it_only_now": {"go_ahead"}, "go_ahead": {"go_ahead"}, "cost_concern": {"cost_too_high"},
          "will_decide_later": {"undecided", "wait_until_later"}, "needs_to_think": {"undecided"},
          "still_thinking": {"undecided"}, "wants_it_later": {"wait_until_later"}, "still_not_ready": {"wait_until_later"},
          "schedule_problem": {"need_sooner"}, "needs_it_sooner": {"need_sooner"},
          "household_must_review": {"household", "undecided"},  # amended: undecided + someone else mentioned (checked below)
          "household_still_discussing": {"household", "undecided"}, "household_view": {"household", "cost_too_high", "other"}}
OLD_ACCEPT = dict(ACCEPT, household_must_review={"household"})  # the pre-registered table of 83010fc4, for comparison


def faithful(facts, j, accept=None):
    """(ok, reasons): the judge's reading carries the turn's facts."""
    accept = ACCEPT if accept is None else accept
    why = []
    if not isinstance(j, dict):
        return False, ["judge reply not an object"]
    if j.get("kind") not in accept.get(facts["act"], set()):
        why.append(f"kind {j.get('kind')!r} for act {facts['act']}")

    def num(x):
        try:
            return float(str(x).replace(",", "").replace("$", ""))
        except (TypeError, ValueError):
            return None
    if facts.get("could_pay"):
        a = num(j.get("amount"))
        if a is None or abs(a - facts["could_pay"]) > 0.05 * facts["could_pay"]:
            why.append(f"amount {j.get('amount')!r} vs {facts['could_pay']}")
    if facts.get("give_a_figure") is False and num(j.get("amount")) is not None:
        why.append("a figure was given")
    if facts.get("wait_weeks_about"):
        w = num(j.get("weeks"))
        if w is None or abs(w - facts["wait_weeks_about"]) > 1:
            why.append(f"weeks {j.get('weeks')!r} vs {facts['wait_weeks_about']}")
    if facts.get("done_by") and str(j.get("weekday") or "").strip().lower() != facts["done_by"].lower():
        why.append(f"weekday {j.get('weekday')!r} vs {facts['done_by']}")
    if facts.get("give_reason") is False and j.get("weekday"):
        why.append("a weekday was given")
    if (facts.get("discussed_with") or facts.get("who")) and j.get("household") is not True:
        why.append("household not mentioned")
    if facts.get("unnecessary_item"):
        words = [w for w in re.findall(r"[a-z]+", facts["unnecessary_item"].lower()) if len(w) > 3]
        if not any(w in str(j.get("item") or "").lower() for w in words):
            why.append(f"item {j.get('item')!r} vs {facts['unnecessary_item']}")
    return not why, why


class Judge:
    """The independent judge with a hard spend ceiling (ledger delta of tag b2 since this judge started)."""

    def __init__(self, model, ceiling_usd, client=None, ledger=None):
        self.ledger = ledger or Ledger()
        self.client = client or Client("b2", ledger=self.ledger, max_attempts=4)
        self.model, self.ceiling = model, ceiling_usd
        self.start = self.ledger.spent()[1].get("b2", 0.0)
        self.skipped = 0

    def spent(self):
        return self.ledger.spent()[1].get("b2", 0.0) - self.start

    def read(self, text):
        if self.spent() >= self.ceiling:
            self.skipped += 1
            return None
        msgs = [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": text}]
        try:
            r = self.client.chat(self.model, msgs, response_format={"type": "json_object"}, **JUDGE_PARAMS)
            return json.loads(r.text.strip().removeprefix("```json").strip("`"))
        except (TFError, ValueError) as e:
            return {"error": f"{type(e).__name__}: {e}"[:200]}


def fidelity(items, judge, workers=8):
    """items: [(facts, text)]. Share of lines whose facts the judge recovers; lines not judged (ceiling) are reported."""
    with ThreadPoolExecutor(workers) as ex:
        reads = list(ex.map(lambda it: judge.read(it[1]), items))
    judged = [(f, t, j) for (f, t), j in zip(items, reads) if j is not None and "error" not in j]
    errors = [j["error"] for j in reads if j is not None and "error" in j]
    oks = [faithful(f, j) for f, _, j in judged]
    old = [faithful(f, j, OLD_ACCEPT)[0] for f, _, j in judged]
    fails = Counter(w.split(" ")[0] for ok, why in oks if not ok for w in why)
    return {**_share_ci([ok for ok, _ in oks]), "share_old_mapping": round(sum(old) / len(old), 4) if old else None,
            "not_judged": len(items) - len(judged), "judge_errors": errors[:5], "failure_kinds": dict(fails),
            "judged": [{"facts": f, "text": t, "judge": j, "ok": ok} for (f, t, j), (ok, _) in zip(judged, oks)],
            "examples_failed": [{"facts": f, "text": t, "judge": j} for (f, t, j), (ok, _) in zip(judged, oks) if not ok][:12]}


def sample_turns(cards, n=300, seed=7):
    """n (card, BuyerState, spec, t) situations across the cards, for the v1-writer reference."""
    rng, out = random.Random(seed), []
    pool = [c for c in cards if c.type != "opt_out"]
    while len(out) < n:
        c = rng.choice(pool)
        spec = rng.choice({"channel": ["missed", "first"], "family": ["first", "again", "family_no"]}.get(c.type, ["first", "again"]))
        st = E.BuyerState(c.patience, rng.choice([c.clarity, "clear"]), True,
                          decided=c.type == "family" and spec == "family_no" and rng.random() < 0.5)
        t = c.first_reply_hours / 24 + (0 if spec == "first" else rng.uniform(1, 10))
        out.append((c, st, spec, t))
    return out


def reference(cards, judge, writer_client, n=300):
    """The v1 writer (Qwen3-30B, v1 instruction sentences) on n sampled turns, judged like the v2 lines."""
    w = E.LLMWriter(writer_client, MF.V1_BUYER_MODEL, E.BUYER_PARAMS[MF.V1_BUYER_MODEL])
    items = []

    def one(x):
        c, st, spec, t = x
        conv = Conversation(c.estimate(), Recipient(c.tz, c.state), c.open_slots(), trace=Trace(sink=lambda _: None))
        text, _, fb = w.write(c, st, spec, conv, t, random.Random(c.id))
        return E.intent(c, st, spec, t), text, fb
    with ThreadPoolExecutor(8) as ex:
        made = list(ex.map(one, sample_turns(cards, n)))
    items = [(f, t) for f, t, fb in made if not fb]
    return {"lines": len(made), "template_fallbacks": sum(fb for _, _, fb in made),
            "fidelity": fidelity(items, judge), "leakage": leakage([("?", t) for _, t in items])["L_any"]}


# ---- the decision rule ------------------------------------------------------------------------------------------------
def did(v1_final, v2_final, policy, base="smart_rules"):
    """Paired bootstrap of sum over customers of [(policy - base) in v2 - (policy - base) in v1]."""
    def diffs(rows):
        a, b = S.per_buyer(rows, policy, "net"), S.per_buyer(rows, base, "net")
        return {k: a[k] - b[k] for k in set(a) & set(b)}
    d1, d2 = diffs(v1_final), diffs(v2_final)
    rows = [{"buyer": k, "policy": "v2", "d": d2[k], "type": ""} for k in d2] + \
           [{"buyer": k, "policy": "v1", "d": d1[k], "type": ""} for k in d1]
    return S.paired_bootstrap(rows, "v2", "v1", metric="d")


def change(v1_final, v2_final, policy):
    """Paired bootstrap of the policy's own net, v2 - v1, summed over customers."""
    rows = [dict(r, policy="v2") for r in v2_final if r["policy"] == policy] + \
           [dict(r, policy="v1") for r in v1_final if r["policy"] == policy]
    return S.paired_bootstrap(rows, "v2", "v1")


def decide(v1_final, v2_final):
    out = {"D2_super_only": S.paired_bootstrap(v2_final, "super_only", "smart_rules"),
           "DiD_super_only": did(v1_final, v2_final, "super_only"),
           "D2_routed": S.paired_bootstrap(v2_final, "routed", "smart_rules"),
           "DiD_routed": did(v1_final, v2_final, "routed"),
           "D2_super_vl": S.paired_bootstrap(v2_final, "super_vl", "smart_rules"),
           "DiD_super_vl": did(v1_final, v2_final, "super_vl"),
           "change_v2_minus_v1": {p: change(v1_final, v2_final, p) for p in MF.V2_POLICIES}}
    out["primary_met"] = bool(out["D2_super_only"] and out["D2_super_only"]["lo"] > 0)
    out["robustness_met"] = bool(out["DiD_super_only"] and out["DiD_super_only"]["lo"] > 0)
    out["claims_allowed"] = ([("Super-only beats the rulebook on wording outside the simulator's vocabulary")] if out["primary_met"] else []) + \
        ([("the rulebook is more brittle than Super-only")] if out["robustness_met"] else [])
    out["verdict"] = "; ".join(out["claims_allowed"]) or "no advantage shown on live wording; the v1 headline stands"
    return out


# ---- commands ---------------------------------------------------------------------------------------------------------
def _load(d):
    rows, _ = R.read_lines(Path(d) / "rows.jsonl")
    outs, _ = R.read_lines(Path(d) / "outcomes.jsonl")
    report = json.loads((Path(d) / "run_report.json").read_text()) if (Path(d) / "run_report.json").exists() else {}
    return rows, outs, report


def integrity(rows, report):
    errored = [r for r in rows if r.get("status") == "errored"]
    return {"stop": report.get("stop"), "rows": len(rows), "clean": sum(map(R.is_clean, rows)), "errored": len(errored),
            "http_429": sum("429" in (r.get("error") or "") for r in rows),
            "off_arm": sum(r.get("off_arm_calls") or 0 for r in rows), "excluded": report.get("excluded_buyers"),
            "ok": report.get("stop") == "complete" and not errored and not report.get("excluded_buyers")
                  and not any(r.get("off_arm_calls") for r in rows)}


def template_share(turns):
    fb = [t["fallback"] for _, t in turns]
    return {"share": round(sum(fb) / len(fb), 4) if fb else None, "fallbacks": sum(fb), "messages": len(fb)}


def cmd_gates(d, judge=None, writer_client=None):
    m = json.loads(V2_MANIFEST.read_text())
    g = m["gates_v2"]
    rows, outs, report = _load(d)
    turns = v2_turns(outs)
    model_lines = [(typ, t["text"]) for typ, t in turns if not t["fallback"]]
    judge = judge or Judge(m["analysis"]["judge_model"], m["analysis"]["judge_ceiling_usd"])
    v1 = leakage(v1_lines(R.read_lines(V1_OUTCOMES)[0]))
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "integrity": integrity(rows, report),
           "template": template_share(turns), "leakage_v2": leakage(model_lines), "leakage_v1": v1,
           "fidelity_v2": fidelity([(t["facts"], t["text"]) for _, t in turns if not t["fallback"]], judge)}
    res["reference_v1_writer"] = reference(B.load(ROOT / "tournament" / "buyers.json"), judge,
                                           writer_client or judge.client)
    res["judge_spent_usd"], res["judge_skipped"] = round(judge.spent(), 4), judge.skipped
    L = res["leakage_v2"]["L_own"]["share"]
    F = res["fidelity_v2"]["share"]
    res["descriptive"] = {"L_own_with_amount": res["leakage_v2"]["L_own_with_amount"],
                          "L_own_v1_without_amount": v1["L_own"], "L_own_v1_with_amount": v1["L_own_with_amount"],
                          "would_pass_v1_minus_20pp": L is not None and v1["L_own"]["share"] is not None
                          and L <= round(v1["L_own"]["share"] - 0.20, 4)}
    res["gates"] = {"integrity": res["integrity"]["ok"],
                    "template": res["template"]["share"] is not None and res["template"]["share"] <= g["template_max"],
                    "L_own": L is not None and L <= g["L_own_max"],
                    "fidelity": F is not None and F >= g["fidelity_min"] and res["fidelity_v2"]["not_judged"] == 0}
    res["pass"] = all(res["gates"].values())
    (Path(d) / "gates.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: res[k] for k in ("gates", "pass")} | {"L_own": res["leakage_v2"]["L_own"], "L_own_v1": v1["L_own"],
                      "fidelity": {k: res["fidelity_v2"][k] for k in ("share", "lo", "hi", "n", "not_judged")},
                      "fidelity_old_mapping": res["fidelity_v2"]["share_old_mapping"],
                      "reference_fidelity": res["reference_v1_writer"]["fidelity"]["share"],
                      "reference_fidelity_old_mapping": res["reference_v1_writer"]["fidelity"]["share_old_mapping"],
                      "descriptive": res["descriptive"],
                      "template": res["template"]}, indent=1))
    return 0 if res["pass"] else 1


def cmd_analyze(d, judge=None):
    from second_ask.tournament import run as RUN
    m2, m1 = json.loads(V2_MANIFEST.read_text()), json.loads(V1_MANIFEST.read_text())
    if MF.sha256_file(ROOT / m2["baseline"]["rows"]) != m2["baseline"]["rows_sha256"]:
        raise SystemExit("v1 rows changed since the v2 manifest was written")
    v1_rows, _ = R.read_lines(ROOT / m2["baseline"]["rows"])
    v2_rows, outs, report = _load(d)
    v1_final, _ = R.final_rows(v1_rows, RUN.manifest_jobs(m1))
    v2_final, info = R.final_rows(v2_rows, RUN.manifest_jobs(m2))
    common = {r["buyer"] for r in v2_final}
    v1_final = [r for r in v1_final if r["buyer"] in common]  # paired: the customers kept in v2
    turns = v2_turns(outs)
    judge = judge or Judge(m2["analysis"]["judge_model"], m2["analysis"]["judge_ceiling_usd"])
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "kept": info, "integrity": integrity(v2_rows, report),
           "decision": decide(v1_final, v2_final), "template": template_share(turns),
           "leakage_v2": leakage([(typ, t["text"]) for typ, t in turns if not t["fallback"]]),
           "fidelity_v2": fidelity([(t["facts"], t["text"]) for _, t in turns if not t["fallback"]], judge)}
    res["judge_spent_usd"], res["judge_skipped"] = round(judge.spent(), 4), judge.skipped
    (Path(d) / "analysis.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({"verdict": res["decision"]["verdict"], "D2_super_only": res["decision"]["D2_super_only"],
                      "DiD_super_only": res["decision"]["DiD_super_only"], "L_own": res["leakage_v2"]["L_own"],
                      "fidelity": res["fidelity_v2"]["share"]}, indent=1))
    return 0


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    sys.exit({"gates": cmd_gates, "analyze": cmd_analyze}[cmd](*args))
