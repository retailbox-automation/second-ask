"""Tournament runner. Every Token Factory call goes through second_ask.tf_client (ledger tag "b2").

    python3 -m second_ask.tournament.run generate            buyer cards -> tournament/buyers.json (no API calls)
    python3 -m second_ask.tournament.run dry-run             all policies x all buyers, fake models (no API calls)
    python3 -m second_ask.tournament.run writers             S8a: pick the buyer model (live, ~60 small calls)
    python3 -m second_ask.tournament.run project [--smoke DIR] projected cost of the full run -> PROJECTION.md
    python3 -m second_ask.tournament.run manifest --buyer-model ID   tournament/MANIFEST.json + MANIFEST.sha256
    python3 -m second_ask.tournament.run smoke --approve SHA  live, the manifest's smoke_plan -> tournament/smoke-b/
    python3 -m second_ask.tournament.run full --approve SHA   the pre-registered run -> tournament/full/
    python3 -m second_ask.tournament.run results [--dir DIR]  results.json + RESULTS-tables.md from rows.jsonl only

smoke and full take every parameter from MANIFEST.json, refuse to start when the manifest does not verify against the
live code or when an SA_* / TF_BASE_URL / TF_CAPS / TF_LEDGER variable is set, and run through runner.run.
"""
import argparse, json, random, re, statistics, sys, threading, time, zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from .. import models as M
from ..agent import Conversation, Event
from ..classifier import classify, render_state
from ..compliance import Recipient
from ..tf_client import Client, Ledger
from ..trace import Trace
from . import buyers as B, env as E, manifest as MF, runner as R, scoring as S
from .policies import ALL, HYBRID, LLM_POLICIES, REPEATED, RULE_POLICIES, FakeSimClient, make

OUT = MF.ROOT / "tournament"
TESTS = {  # test -> (manifest, smoke directory, full-run directory)
    "v1": (OUT / "MANIFEST.json", OUT / "smoke-b", OUT / "full"),
    "v2": (OUT / "v2" / "MANIFEST.json", OUT / "v2" / "smoke", OUT / "v2" / "full"),
    "v3": (OUT / "v3" / "MANIFEST.json", OUT / "v3" / "smoke", OUT / "v3" / "full"),
}
BLIND = (MF.V2_TEST, MF.V3_TEST)  # tests whose smokes and runs print no outcomes until the pre-registered analysis
BUYER_PARAMS = E.BUYER_PARAMS
SMOKE_POLICIES = MF.SMOKE_REPEAT_POLICIES  # the phase-A smoke (0652f17)
SAFETY = 1.3  # projected cost is multiplied by this


def cards():
    return B.load(OUT / "buyers.json")


def shown(path):
    """A path as written in reports: relative to the repo when it is inside it."""
    path = Path(path).resolve()
    return str(path.relative_to(MF.ROOT)) if path.is_relative_to(MF.ROOT) else str(path)


def smoke_buyers(cs):
    """Pre-registered smoke pick: the first clear price buyer and the first family buyer in buyers.json."""
    return [next(c for c in cs if c.type == "price" and c.clarity == "clear"), next(c for c in cs if c.type == "family")]


def cmd_generate(_):
    OUT.mkdir(exist_ok=True)
    cs = B.generate()
    B.save(cs, OUT / "buyers.json")
    feas = sum(S.oracle(c)[1] for c in cs)
    print(f"{len(cs)} buyers -> {OUT / 'buyers.json'} (sha256 {MF.sha256_file(OUT / 'buyers.json')})")
    print("types:", {t: sum(c.type == t for c in cs) for t in B.TYPE_MIX}, f"feasible: {feas}")


def main_table(agg):
    lines = ["| policy | buyers | K | bookings | rate | margin $ | given away $ (to full payers / menu gap) | "
             "lost $ (price) | model $ | $/booking | net $ | violations | unsure | off-arm | label fallback | "
             "platform failures |", "|---|" + "---|" * 15]
    for p, a in agg.items():
        upb = f"{a['usd_per_booking']:.5f}" if a["usd_per_booking"] is not None else "-"
        lines.append(f"| {p} | {a['n']} | {a['K']} | {a['bookings']:.1f} | {a['booking_rate']:.3f} | {a['margin']:,.0f} | "
                     f"{a['given_away']:,.0f} ({a['given_to_full_payers']:,.0f} / {a['menu_gap']:,.0f}) | "
                     f"{a['lost']:,.0f} ({a['lost_price']:,.0f}) | {a['model_usd']:.4f} | {upb} | {a['net']:,.0f} | "
                     f"{a['violations']:.1f} | {a['unsure']:.1f} | {a['off_arm_calls']:.1f} | "
                     f"{a['label_fallback_calls']:.1f} | {a['platform_failures']:.1f} |")
    return lines


def summarize(rows, cs, label):
    agg = S.aggregate(rows)
    print(f"\n{label}: per policy (each buyer's mean over its repeats, summed over buyers)")
    print("\n".join(main_table(agg)))
    return agg


def cmd_dry_run(_):
    cs = cards()
    rows, outs = [], []
    for name in ALL:
        for c in cs:
            client = FakeSimClient(seed=zlib.crc32(f"{name}/{c.id}".encode()))
            client.card = c
            out = E.run_one(c, make(name, client), E.TemplateWriter(), 0)
            outs.append(out)
            rows.append(S.score(c, out))
    d = OUT / "dryrun"
    d.mkdir(exist_ok=True)
    (d / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "examples.jsonl").write_text("".join(json.dumps(asdict(o)) + "\n" for o in outs if o.buyer in ("B001", "B002", "B003")))
    summarize(rows, cs, "DRY RUN (fake models, template buyers: machinery only, not results)")
    gates = S.sanity_gates(cs, rows, RULE_POLICIES)
    gates["chronology_errors"] = sum(o.chronology_errors for o in outs)
    calls = {p: {"seller_calls_per_conv": statistics.mean(o.seller_calls for o in outs if o.policy == p),
                 "seller_msgs_per_conv": statistics.mean(o.moves for o in outs if o.policy == p),
                 "buyer_msgs_per_conv": statistics.mean(sum(e["who"] == "customer" for e in o.transcript)
                                                        for o in outs if o.policy == p)} for p in ALL}
    (d / "summary.json").write_text(json.dumps({"gates": gates, "calls": calls, "aggregate": S.aggregate(rows)}, indent=1))
    print("\nSanity gates:", json.dumps(gates, indent=1))
    print("Calls per conversation:", json.dumps(calls, indent=1))
    bad = gates["bookings_above_walkaway"] + gates["bookings_below_floor"] + gates["chronology_errors"]
    return 1 if bad else 0


def cmd_writers(_):
    """S8a: the three non-NVIDIA candidates write the first message for the same 10 cards; Lightning labels each."""
    cs = [c for c in cards() if c.type != "opt_out"][:10]
    client = Client("b2", max_attempts=4)
    jobs = [(m, c) for m in BUYER_PARAMS for c in cs]

    def one(job):
        model, c = job
        w = E.LLMWriter(client, model, BUYER_PARAMS[model])
        st = E.BuyerState(c.patience, c.clarity, seen=True)
        est = c.estimate()
        conv = Conversation(est, Recipient(c.tz, c.state), c.open_slots(), trace=Trace(sink=lambda _: None))
        spec = "missed" if c.type == "channel" else "first"
        text, steps, fallback = w.write(c, st, spec, conv, c.first_reply_hours / 24, random.Random(c.id))
        leak = sum(s.note == "leak" for s in steps or [])
        conv.events.append(Event(E.at(c.first_reply_hours / 24), "customer", est.channel, text))
        now = E.at(c.first_reply_hours / 24 + E.SELLER_LATENCY_DAYS)
        lab = classify(client, M.LIGHTNING, render_state(est, conv.events, now, 0 if c.type == "channel" else 1),
                       Trace(sink=lambda _: None), "json_object")
        gold = {B.LABEL_OF[c.type]} | ({"ready_to_book"} if c.type == "channel" else set())
        rec = {"model": model, "buyer": c.id, "type": c.type, "spec": spec, "text": text, "fallback": fallback,
               "leak": leak, "label": lab.label, "label_ok": lab.label in gold,
               "cost": sum(s.cost for s in steps or []), "seconds": sum(s.seconds for s in steps or []),
               "calls": len(steps or [])}
        print(f"[writers] {model.split('/')[-1]} {c.id} {c.type}: fallback={fallback} leak={leak} label={lab.label} "
              f"${rec['cost']:.5f} | {text[:90]}")
        return rec

    with ThreadPoolExecutor(6) as ex:
        recs = list(ex.map(one, jobs))
    d = MF.ROOT / "evidence" / "spikes" / "s8_buyer_writers"
    d.mkdir(parents=True, exist_ok=True)
    (d / "raw.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    print("\n| Candidate | valid (no fallback) | leaks | Lightning label agrees | $ per message | mean s |\n|---|---|---|---|---|---|")
    table = {}
    for m in BUYER_PARAMS:
        rs = [r for r in recs if r["model"] == m]
        table[m] = {"valid": sum(not r["fallback"] for r in rs), "leaks": sum(r["leak"] for r in rs),
                    "label": sum(r["label_ok"] for r in rs), "usd": statistics.mean(r["cost"] for r in rs),
                    "s": statistics.mean(r["seconds"] for r in rs)}
        t = table[m]
        print(f"| `{m}` | {t['valid']}/10 | {t['leaks']} | {t['label']}/10 | {t['usd']:.6f} | {t['s']:.2f} |")
    ok = [m for m, t in table.items() if t["valid"] >= 9 and t["leaks"] == 0]
    pick = min(ok, key=lambda m: (round(table[m]["usd"], 6), -table[m]["label"])) if ok else None
    (d / "pick.json").write_text(json.dumps({"table": table, "pick": pick}, indent=1))
    print(f"\nPick (cheapest with >=9/10 valid and 0 leaks): {pick}")


def conversation_costs(outcomes, dry):
    """$ per conversation by policy (shop and buyer calls).
    - At least 10 clean smoke conversations of the policy: their mean.
    - Otherwise, an LLM policy when some LLM policy has at least 10 (the proxy, same agent loop and rhythm): the proxy's
      shop calls per conversation x this policy's smoke $ per shop call, plus the proxy's buyer $ per conversation.
    - Otherwise: (dry-run shop calls per conversation x smoke $ per shop call) + (dry-run buyer messages per
      conversation x smoke $ per buyer message x 1.1 for retries)."""
    clean = defaultdict(list)
    buyer_msgs = []
    for o in outcomes:
        if not o.get("off_arm_calls") and not o.get("platform_failures"):
            clean[o["policy"]].append(o)
        buyer_msgs += [s["cost"] for s in o["steps"] if s["step"] == "buyer" and s["model"] and not s.get("platform_error")]
    buyer_call = statistics.mean(buyer_msgs) if buyer_msgs else 0.0
    proxy = max(LLM_POLICIES, key=lambda p: (len(clean.get(p, [])), p))
    proxy = proxy if len(clean.get(proxy, [])) >= 10 else None
    conv = {}
    for p in ALL:
        os_ = clean.get(p, [])
        if len(os_) >= 10:
            conv[p] = {"usd": statistics.mean(o["seller_cost"] + o["buyer_cost"] for o in os_),
                       "method": f"measured: mean of {len(os_)} clean smoke conversations"}
            continue
        calls = sum(o["seller_calls"] for o in os_)
        per_call = sum(o["seller_cost"] for o in os_) / calls if calls else 0.0
        if p in LLM_POLICIES and proxy:
            px = clean[proxy]
            seller = statistics.mean(o["seller_calls"] for o in px) * per_call
            buyer = statistics.mean(o["buyer_cost"] for o in px)
            how = f"{proxy}'s calls per conversation ({len(px)} smoke conversations) x this policy's $ per shop call"
        else:
            seller = dry[p]["seller_calls_per_conv"] * per_call
            buyer = dry[p]["buyer_msgs_per_conv"] * buyer_call * 1.1
            how = "dry-run calls x smoke prices"
        conv[p] = {"usd": seller + buyer, "per_shop_call": per_call,
                   "method": f"{how} ({len(os_)} smoke conversations of its own)"}
    return conv, buyer_call


def cmd_project(a):
    smoke_dir = Path(a.smoke).resolve() if a.smoke else OUT / "smoke"
    dest = OUT if not a.smoke else smoke_dir
    dry = json.loads((OUT / "dryrun" / "summary.json").read_text())["calls"]
    outcomes, _ = R.read_lines(smoke_dir / "outcomes.jsonl")
    conv, buyer_call = conversation_costs(outcomes, dry)
    n = len(cards())

    def total(n_all, k, sub):  # every policy once on n_all buyers, the repeated ones k-1 more times on `sub`
        return SAFETY * sum(conv[p]["usd"] * (n_all + ((k - 1) * sub if p in REPEATED else 0)) for p in ALL)

    options = [("A", n, 3, n), ("B", n, 3, 100), ("C", n, 3, 60), ("D", n, 1, 0), ("E", 120, 3, 120)]
    rows = [{"option": o, "buyers": nb, "K_llm": k, "subsample_for_K": sub, "projected_usd": round(total(nb, k, sub), 2)}
            for o, nb, k, sub in options]
    pick = next((r for r in rows if r["projected_usd"] <= a.budget), None)
    proj = {"smoke": shown(smoke_dir), "buyer_usd_per_message": buyer_call, "safety_factor": SAFETY,
            "per_conversation_usd": conv, "dry_run_calls": dry, "options": rows, "budget_usd": a.budget, "pick": pick}
    (dest / "projection.json").write_text(json.dumps(proj, indent=1))
    lines = ["# Projected cost of the full tournament", "",
             f"Smoke: `{proj['smoke']}`. Per policy, $ per conversation is the mean over at least 10 clean smoke "
             "conversations when there are that many; otherwise (shop calls per conversation from the dry run) x "
             "($ per shop call in the smoke) + (buyer messages per conversation from the dry run) x ($ per buyer "
             f"message in the smoke) x 1.1. Totals x {SAFETY}. The rule policies have no shop-side model cost.", "",
             "| Policy | $ per conversation | method |", "|---|---|---|"]
    lines += [f"| {p} | {conv[p]['usd']:.5f} | {conv[p]['method']} |" for p in ALL]
    lines += ["", f"Buyer message: ${buyer_call:.6f} each (smoke).", "",
              "| Option | buyers | K | buyers with K repeats | projected $ |", "|---|---|---|---|---|"]
    lines += [f"| {r['option']} | {r['buyers']} | {r['K_llm']} | {r['subsample_for_K']} | {r['projected_usd']:.2f} |" for r in rows]
    lines += ["", f"Budget for the full run: ${a.budget:.2f}. Proposed: option {pick['option'] if pick else 'none fits'}."]
    (dest / "PROJECTION.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def cmd_manifest(a):
    if a.test == "v2":
        return manifest_v2()
    if a.test == "v3":
        return manifest_v3()
    proj = json.loads((OUT / "projection.json").read_text())
    pick = proj["pick"]
    cs = cards()
    order = [c.id for c in cs]
    random.Random(B.GENERATOR_SEED + 1).shuffle(order)
    sub = sorted(order[: pick["subsample_for_K"]]) if pick["subsample_for_K"] < len(cs) else "all"
    smoke = {"buyers": [c.id for c in smoke_buyers(cs)], "policies": list(SMOKE_POLICIES),
             "outcomes": "tournament/smoke/outcomes.jsonl",
             "note": "phase A smoke (0652f17). It ran before three simulator fixes it exposed (the buyer writer invented "
                     "requests; a family buyer answered every check-in, looping to the message cap; family "
                     "deliberation waited for an 'info' message). smoke_plan re-runs these 10 conversations on the "
                     "frozen code."}
    m = MF.build("tournament/buyers.json", pick["buyers"], pick["K_llm"], sub, a.buyer_model, smoke,
                 {"file": "tournament/PROJECTION.md", "projected_usd": pick["projected_usd"],
                  "option": pick["option"], "budget_usd": proj["budget_usd"]})
    h = MF.write(m)
    print(f"MANIFEST.json sha256 {h}")


def manifest_v2():
    """tournament/v2/MANIFEST.json: v1's buyers, K and subsample; projection from v1's measured $ per conversation."""
    v1 = json.loads(TESTS["v1"][0].read_text())
    outs, _ = R.read_lines(TESTS["v1"][2] / "outcomes.jsonl")
    by = defaultdict(list)
    for o in outs:
        by[o["policy"]].append(o["seller_cost"] + o["buyer_cost"])
    per = {p: statistics.mean(v) for p, v in by.items()}
    m0 = {"buyers": v1["buyers"], "policies": {p: {} for p in MF.V2_POLICIES}, "repeats": v1["repeats"],
          "run_policies": list(MF.V2_POLICIES)}
    jobs = manifest_jobs(m0)
    usd = SAFETY * sum(per[p] for _, p, _ in jobs)
    proj = {"basis": "v1 measured $ per conversation (tournament/full/outcomes.jsonl) x the v2 plan", "jobs": len(jobs),
            "safety_factor": SAFETY, "projected_usd": round(usd, 2), "run_ceiling_usd": MF.V2_RUN_CEILING_USD}
    m = MF.build(v1["buyers"]["file"], v1["buyers"]["n"], v1["repeats"]["k"], v1["repeats"]["subsample"],
                 MF.V1_BUYER_MODEL, None, proj, v2=True)
    TESTS["v2"][0].parent.mkdir(parents=True, exist_ok=True)
    h = MF.write(m, TESTS["v2"][0])
    print(f"tournament/v2/MANIFEST.json sha256 {h}; projected ${proj['projected_usd']} for {len(jobs)} conversations")
    return 0


def manifest_v3():
    """tournament/v3/MANIFEST.json: v1's buyers and writer model; K = 5 on every buyer. Projection (an upper bound): every
    hybrid conversation costs what a whole v1 super_only conversation cost, every smart_rules one what a v1 smart_rules
    conversation cost."""
    v1 = json.loads(TESTS["v1"][0].read_text())
    outs, _ = R.read_lines(TESTS["v1"][2] / "outcomes.jsonl")
    by = defaultdict(list)
    for o in outs:
        by[o["policy"]].append(o["seller_cost"] + o["buyer_cost"])
    per = {"smart_rules": statistics.mean(by["smart_rules"]), HYBRID: statistics.mean(by["super_only"])}
    n = v1["buyers"]["n"] * MF.V3_K
    usd = SAFETY * n * (per["smart_rules"] + per[HYBRID])
    proj = {"basis": "v1 measured $ per conversation (tournament/full/outcomes.jsonl): smart_rules for smart_rules, "
                     "super_only for the hybrid (an upper bound: the hybrid calls the model only on rules 8 and 9)",
            "usd_per_conversation": {k: round(v, 6) for k, v in per.items()}, "jobs": 2 * n, "safety_factor": SAFETY,
            "projected_usd": round(usd, 2), "run_ceiling_usd": MF.V3_RUN_CEILING_USD}
    m = MF.build(v1["buyers"]["file"], v1["buyers"]["n"], MF.V3_K, "all", v1["buyer_simulator"]["model"], None, proj,
                 v3=True)
    TESTS["v3"][0].parent.mkdir(parents=True, exist_ok=True)
    h = MF.write(m, TESTS["v3"][0])
    print(f"tournament/v3/MANIFEST.json sha256 {h}; projected ${proj['projected_usd']} (upper bound) for {proj['jobs']} "
          f"conversations")
    return 0


def guard(approve, environ=None, test="v1"):
    """The frozen manifest, or None (with the reason printed) when a live command must not start."""
    bad = R.env_overrides(environ)
    if bad:
        print(f"refusing: {', '.join(bad)} set; they would change models, endpoint, caps or ledger behind the manifest. "
              f"Unset them: every parameter comes from MANIFEST.json")
        return None
    path = TESTS[test][0]
    recorded, problems = MF.verify(path)
    if approve != recorded or problems:
        print(f"refusing: --approve must equal the recorded manifest hash {recorded}; problems: {problems}")
        return None
    return json.loads(path.read_text())


def live_conversations(m, ledger):
    """converse(card, policy, repeat) for the runner, built only from the manifest: no model fallbacks, platform
    failures abort the conversation, the buyer writer and its parameters as registered."""
    p = m["platform"]
    client = R.StrictClient(Client(p["ledger"]["tag"], ledger=ledger, max_attempts=p["client"]["max_attempts"],
                                   fallbacks=dict(p["client"]["fallbacks"])))
    bs = m["buyer_simulator"]
    writer = E.WRITERS[bs.get("writer", "v1")](client, bs["model"], bs["params"])
    shared = ({}, threading.Lock()) if "common_random_numbers" in bs else None

    def converse(card, policy, rep):
        w = E.SharedWriter(writer, rep, *shared) if shared else writer
        return E.run_one(card, make(policy, client), w, rep)
    converse.writer = writer
    converse.shared = shared
    return converse


def spent_since_start(ledger, out, tag, sha):
    """(tag spend at the first start of this run directory, callable: spend since then). Kept across resumes."""
    out.mkdir(parents=True, exist_ok=True)
    st = out / "run_state.json"
    if st.exists():
        state = json.loads(st.read_text())
        if state.get("manifest") != sha:
            raise SystemExit(f"refusing: {out} was started under manifest {state.get('manifest')}, not {sha}; "
                             f"use a new directory")
        start = state["ledger_tag_usd_at_start"]
    else:
        start = ledger.spent()[1].get(tag, 0.0)
        st.write_text(json.dumps({"ledger_tag_usd_at_start": start, "tag": tag, "manifest": sha,
                                  "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=1) + "\n")
    return start, lambda: ledger.spent()[1].get(tag, 0.0) - start


def blind_log(line):
    """v2 runs are blind to policy outcomes until the pre-registered analysis: progress lines keep the conversation's
    key and status, not whether it booked, how it ended or what it cost."""
    print(re.sub(r" booked=.*$", "", line))


def manifest_jobs(m):
    ids = [c.id for c in cards()]
    assert len(ids) == m["buyers"]["n"], "buyers.json does not hold the manifest's n buyers"
    pols = [p for p in ALL + (HYBRID,) if p in (m.get("run_policies") or m["policies"])]
    return R.plan(ids, pols, m["repeats"]["repeated"], m["repeats"]["k"], m["repeats"]["subsample"])


def cmd_smoke(a):
    m = guard(a.approve, test=a.test)
    if m is None:
        return 2
    p, out = m["platform"], TESTS[a.test][1]
    ledger = Ledger(p["ledger"]["path"], caps=p["caps_usd"])
    _, spent = spent_since_start(ledger, out, p["ledger"]["tag"], a.approve)
    jobs = [tuple(j) for j in m["smoke_plan"]["jobs"]]
    blind = m.get("test") in BLIND
    report = R.run(jobs, {c.id: c for c in cards()}, live_conversations(m, ledger), out,
                   concurrency=m["budget"]["concurrency"], ultra_concurrency=m["budget"]["ultra_concurrency"],
                   ceiling_usd=m["smoke_plan"]["ceiling_usd"], spent=spent, log=blind_log if blind else print)
    rows, _ = R.read_lines(out / "rows.jsonl")
    clean = [r for r in rows if R.is_clean(r)]
    if clean and not blind:  # v2: no outcome table (blind until tools/v2_analysis.py after the full run)
        summarize(clean, cards(), "SMOKE (mechanics and cost only, not results)")
    print(json.dumps(report, indent=1))
    return 0 if report["stop"] == "complete" else 3


def cmd_full(a):
    m = guard(a.approve, test=a.test)
    if m is None:
        return 2
    p, b, out = m["platform"], m["budget"], TESTS[a.test][2]
    ledger = Ledger(p["ledger"]["path"], caps=p["caps_usd"])
    tag = p["ledger"]["tag"]
    start, spent = spent_since_start(ledger, out, tag, a.approve)
    limit = min(b["run_ceiling_usd"], p["caps_usd"][tag] - start)
    accept = False
    cp_file = out / "checkpoint.json"
    if a.accept_projection is not None and cp_file.exists():
        cp = json.loads(cp_file.read_text())
        accept = cp["projected_total_usd"] <= a.accept_projection <= p["caps_usd"][tag] - start
        if not accept:
            print(f"refusing --accept-projection {a.accept_projection}: it must cover the projected "
                  f"${cp['projected_total_usd']} and fit what is left of the {tag} cap (${p['caps_usd'][tag] - start:.2f})")
            return 2
        limit = a.accept_projection
    checkpoint = {"after_buyers": b["checkpoint"]["after_buyers"], "safety": b["checkpoint"]["safety"],
                  "limit_usd": limit, "accept_failed": accept}
    report = R.run(manifest_jobs(m), {c.id: c for c in cards()}, live_conversations(m, ledger), out,
                   concurrency=b["concurrency"], ultra_concurrency=b["ultra_concurrency"], checkpoint=checkpoint,
                   ceiling_usd=limit, spent=spent, log=blind_log if m.get("test") in BLIND else print)
    print(json.dumps(report, indent=1))
    write_results(out, m)
    return 0 if report["stop"] == "complete" else 3


def results(rows, m, cs, jobs):
    """Every number RESULTS.md may quote, computed from the run's rows, its planned jobs and the manifest only."""
    final, info = R.final_rows(rows, jobs)
    res = {"rows": len(rows), "kept": info}
    if not final:
        return res
    kept = {r["buyer"] for r in final}
    sub = kept if m["repeats"]["subsample"] == "all" else kept & set(m["repeats"]["subsample"])
    res["aggregate"] = S.aggregate(final, sub)
    pols = sorted({r["policy"] for r in final})
    res["comparisons"] = {f"{p} - {base}": S.paired_bootstrap(final, p, base)
                          for base in ("smart_rules", "shop_rule") if base in pols for p in pols if p != base}
    if {"routed", "super_only"} <= set(pols):
        res["label_default"] = S.label_default(final)
    if "routed" in pols:
        res["losing_cells"] = S.losing_cells(final, "routed")
    kept_cards = [cs[b] for b in sorted(kept)]
    if sum(c.type == "price" for c in kept_cards) >= 3:
        res["sanity_gates"] = S.sanity_gates(kept_cards, final, RULE_POLICIES)
    opt = defaultdict(lambda: defaultdict(lambda: {"violations": 0.0, "n": 0}))
    for p in pols:
        for b, v in S.per_buyer(final, p, "violations").items():
            if cs[b].type == "opt_out":
                cell = opt[p][cs[b].opt_out_style]
                cell["violations"] += v
                cell["n"] += 1
    res["opt_out_violations"] = {p: dict(v) for p, v in opt.items()}
    integ = defaultdict(lambda: defaultdict(float))
    for r in rows:  # every attempt, errored ones included
        g = integ[r["policy"]]
        g["attempts"] += 1
        g["errored"] += r.get("status") == "errored"
        for k in ("platform_failures", "off_arm_calls", "label_fallback_calls", "chronology_errors", "writer_fallbacks"):
            g[k] += r.get(k) or 0
    res["integrity_all_attempts"] = {p: dict(v) for p, v in integ.items()}
    return res


FLAG_KINDS = (("low confidence", "label with low confidence"), ("label failed", "label failed"),
              ("move choice failed", "move choice failed: code default"), ("replaced by the template", "message replaced "
              "by the template"), ("opt-out word appears", "opt-out word inside a sentence"))


def unsure_flags(outcomes, final):
    """Per policy: how often each kind of 'unsure' flag was raised on a sent message, in the conversations kept."""
    kept = {(r["buyer"], r["policy"], r["repeat"], r["attempt"]) for r in final}
    out = defaultdict(lambda: defaultdict(int))
    for o in outcomes:
        if (o["buyer"], o["policy"], o["repeat"], o.get("attempt")) not in kept:
            continue
        for entry in o.get("flags") or []:
            for f in entry["flags"]:
                out[o["policy"]][next((name for key, name in FLAG_KINDS if key in f), "other")] += 1
    return {p: dict(v) for p, v in out.items()}


def write_results(out, m):
    rows, bad = R.read_lines(out / "rows.jsonl")
    jobs = manifest_jobs(m)
    res = results(rows, m, {c.id: c for c in cards()}, jobs) | {"unreadable_row_lines": bad}
    res["unsure_flags"] = unsure_flags(R.read_lines(out / "outcomes.jsonl")[0], R.final_rows(rows, jobs)[0])
    (out / "results.json").write_text(json.dumps(res, indent=1) + "\n")
    lines = [f"# Tables computed from `{shown(out)}/rows.jsonl` by `run results` (do not edit by hand)", "",
             f"Rows {res['rows']}; buyers kept {res['kept']['buyers_kept']} of {res['kept']['buyers_planned']}; "
             f"excluded {res['kept']['excluded']}; incomplete {len(res['kept']['incomplete'])}; errored rows "
             f"{res['kept']['errored_rows']}; re-runs {res['kept']['reruns']}.", ""]
    if "aggregate" in res:
        lines += ["## Per policy (each buyer's mean over its repeats, summed over buyers)", ""] + main_table(res["aggregate"])
        lines += ["", "## Bands over repeats on the common subsample", "",
                  "| policy | buyers | repeats | net mean [min, max] | margin mean [min, max] | bookings mean [min, max] |",
                  "|---|---|---|---|---|---|"]
        for p, a in res["aggregate"].items():
            bd = a["band"]
            cell = lambda k: f"{bd[k]['mean']:,.0f} [{bd[k]['min']:,.0f}, {bd[k]['max']:,.0f}]" if k in bd else "-"  # noqa: E731
            lines.append(f"| {p} | {bd['n']} | {bd['K']} | {cell('net')} | {cell('margin')} | {cell('booked')} |")
        lines += ["", "## Paired bootstrap of the summed per-buyer net difference (95 %)", "",
                  "| comparison | point $ | interval $ | buyers |", "|---|---|---|---|"]
        lines += [f"| {k} | {v['point']:,.0f} | [{v['lo']:,.0f}, {v['hi']:,.0f}] | {v['buyers']} |"
                  for k, v in res["comparisons"].items() if v]
        if "losing_cells" in res:
            lines += ["", "## Cells where routed earns less than the best policy in the cell", "",
                      "| type | trade | n | routed net/buyer | best | best net/buyer | gap | interval | clear loss |",
                      "|---|---|---|---|---|---|---|---|---|"]
            lines += [f"| {c['type']} | {c['trade']} | {c['n']} | {c['policy_net']:,.0f} | {c['best']} | "
                      f"{c['best_net']:,.0f} | {c['gap']:,.0f} | {c['ci']} at {c['level']} | {c['clear_loss']} |"
                      for c in res["losing_cells"]]
    (out / "RESULTS-tables.md").write_text("\n".join(lines) + "\n")
    return res


def cmd_results(a):
    write_results(Path(a.dir).resolve() if a.dir else TESTS[a.test][2], json.loads(TESTS[a.test][0].read_text()))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="second_ask.tournament.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "dry-run", "writers"):
        sub.add_parser(name)
    s = sub.add_parser("smoke")
    s.add_argument("--approve", required=True)
    s.add_argument("--test", choices=sorted(TESTS), default="v1")
    p = sub.add_parser("project")
    p.add_argument("--budget", type=float, default=12.0)
    p.add_argument("--smoke", help="smoke directory (default: tournament/smoke, the phase-A smoke)")
    mf = sub.add_parser("manifest")
    mf.add_argument("--buyer-model", choices=sorted(BUYER_PARAMS), default=MF.V1_BUYER_MODEL)
    mf.add_argument("--test", choices=sorted(TESTS), default="v1")
    f = sub.add_parser("full")
    f.add_argument("--approve", required=True)
    f.add_argument("--test", choices=sorted(TESTS), default="v1")
    f.add_argument("--accept-projection", type=float, default=None,
                   help="after a failed checkpoint: continue with this ceiling (must cover the projection)")
    r = sub.add_parser("results")
    r.add_argument("--dir")
    r.add_argument("--test", choices=sorted(TESTS), default="v1")
    a = ap.parse_args(argv)
    return {"generate": cmd_generate, "dry-run": cmd_dry_run, "writers": cmd_writers, "smoke": cmd_smoke,
            "project": cmd_project, "manifest": cmd_manifest, "full": cmd_full, "results": cmd_results}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
