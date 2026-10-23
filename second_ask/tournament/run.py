"""Tournament runner. Every Token Factory call goes through second_ask.tf_client (ledger tag "b2").

    python3 -m second_ask.tournament.run generate            buyer cards -> tournament/buyers.json (no API calls)
    python3 -m second_ask.tournament.run dry-run             all policies x all buyers, fake models (no API calls)
    python3 -m second_ask.tournament.run writers             S8a: pick the buyer model (live, ~60 small calls)
    python3 -m second_ask.tournament.run smoke --buyer-model ID   live, 10 conversations
    python3 -m second_ask.tournament.run project             projected cost of the full run -> tournament/PROJECTION.md
    python3 -m second_ask.tournament.run manifest            tournament/MANIFEST.json + MANIFEST.sha256
    python3 -m second_ask.tournament.run full --approve SHA  the pre-registered run (refuses without the manifest hash)
"""
import argparse, json, random, statistics, sys, zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from .. import models as M
from ..agent import Conversation, Event
from ..classifier import classify, render_state
from ..compliance import Recipient
from ..tf_client import Client, Ledger
from ..trace import Trace
from . import buyers as B, env as E, manifest as MF, scoring as S
from .policies import ALL, LLM_POLICIES, RULE_POLICIES, FakeSimClient, make

OUT = MF.ROOT / "tournament"
BUYER_PARAMS = {  # buyer-writer parameters per candidate (S1: gpt-oss cannot turn reasoning off)
    M.QWEN3_30B: {"temperature": 0.8, "top_p": 0.9, "max_tokens": 300},
    M.DEEPSEEK_V4_FLASH: {"temperature": 0.8, "top_p": 0.9, "max_tokens": 300, "reasoning_effort": "none"},
    M.GPT_OSS_120B: {"temperature": 0.8, "top_p": 0.9, "max_tokens": 1500, "reasoning_effort": "low"},
}
SMOKE_POLICIES = ("shop_rule", "routed", "super_only", "ultra_only", "super_vl")
SAFETY = 1.3  # projected cost is multiplied by this


def cards():
    return B.load(OUT / "buyers.json")


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


def summarize(rows, cs, label):
    agg = S.aggregate(rows)
    print(f"\n{label}: per policy (mean over repeats)")
    print("| policy | bookings | rate | margin $ | given away $ | lost $ (price) | model $ | $/booking | net $ | violations | unsure |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for p, a in agg.items():
        upb = a["usd_per_booking"]["mean"] if a["usd_per_booking"] else 0
        print(f"| {p} | {a['bookings']['mean']:.0f} | {a['booking_rate']['mean']:.2f} | {a['margin']['mean']:,.0f} | "
              f"{a['given_away']['mean']:,.0f} | {a['lost']['mean']:,.0f} ({a['lost_price']['mean']:,.0f}) | "
              f"{a['model_usd']['mean']:.4f} | {upb:.5f} | {a['net']['mean']:,.0f} | {a['violations']['mean']:.0f} | "
              f"{a['unsure']['mean']:.0f} |")
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
    gates = S.sanity_gates(cs, rows)
    calls = {p: {"seller_calls_per_conv": statistics.mean(o.seller_calls for o in outs if o.policy == p),
                 "seller_msgs_per_conv": statistics.mean(o.moves for o in outs if o.policy == p),
                 "buyer_msgs_per_conv": statistics.mean(sum(e["who"] == "customer" for e in o.transcript)
                                                        for o in outs if o.policy == p)} for p in ALL}
    (d / "summary.json").write_text(json.dumps({"gates": gates, "calls": calls, "aggregate": S.aggregate(rows)}, indent=1))
    print("\nSanity gates:", json.dumps(gates, indent=1))
    print("Calls per conversation:", json.dumps(calls, indent=1))
    bad = gates["bookings_above_walkaway"] + gates["bookings_below_floor"]
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


def run_live(pairs, buyer_model, tag="b2"):
    client = Client(tag, max_attempts=4, fallbacks=M.FALLBACKS)
    writer = E.LLMWriter(client, buyer_model, BUYER_PARAMS[buyer_model])

    def one(job):
        c, name, rep = job
        out = E.run_one(c, make(name, client), writer, rep)
        print(f"[live] {name} {c.id} ({c.type}) booked={out.booked} price={out.price} moves={out.moves} "
              f"seller ${out.seller_cost:.5f} ({out.seller_calls} calls) buyer ${out.buyer_cost:.5f} end={out.end}")
        return c, out

    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(one, pairs))


def cmd_smoke(a):
    cs = smoke_buyers(cards())
    pairs = [(c, p, 0) for c in cs for p in SMOKE_POLICIES]
    assert len(pairs) <= 10
    res = run_live(pairs, a.buyer_model)
    d = OUT / "smoke"
    d.mkdir(exist_ok=True)
    (d / "outcomes.jsonl").write_text("".join(json.dumps(asdict(o)) + "\n" for _, o in res))
    rows = [S.score(c, o) for c, o in res]
    (d / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    summarize(rows, cs, "LIVE SMOKE (2 buyers x 5 policies)")
    total, by_tag = Ledger().spent()
    print(f"ledger: total ${total:.4f}, b2 ${by_tag.get('b2', 0):.4f}")


def cmd_project(a):
    dry = json.loads((OUT / "dryrun" / "summary.json").read_text())["calls"]
    smoke = [json.loads(l) for l in (OUT / "smoke" / "outcomes.jsonl").read_text().splitlines()]
    per_call, per_buyer_msg = {}, []
    for p in SMOKE_POLICIES:
        os_ = [o for o in smoke if o["policy"] == p]
        calls = sum(o["seller_calls"] for o in os_)
        per_call[p] = sum(o["seller_cost"] for o in os_) / calls if calls else 0.0
        per_buyer_msg += [s["cost"] for o in os_ for s in o["steps"] if s["step"] == "buyer" and s["model"]]
    buyer_call = statistics.mean(per_buyer_msg) if per_buyer_msg else 0.0
    conv = {}
    for p in ALL:
        src = p if p in per_call else "shop_rule"
        seller = dry[p]["seller_calls_per_conv"] * per_call.get(src, 0.0)
        buyer = dry[p]["buyer_msgs_per_conv"] * buyer_call * 1.1  # +10%: retries
        conv[p] = {"seller": seller * SAFETY, "buyer": buyer * SAFETY}
    n = len(cards())

    def total(n_all, k_llm, sub):
        t = 0.0
        for p in ALL:
            per = conv[p]["seller"] + conv[p]["buyer"]
            t += n_all * per if p in RULE_POLICIES else n_all * per + (k_llm - 1) * sub * per
        return t

    options = [("A", n, 3, n), ("B", n, 3, 100), ("C", n, 3, 60), ("D", n, 1, 0), ("E", 120, 3, 120)]
    rows = [{"option": o, "buyers": nb, "K_llm": k, "subsample_for_K": sub, "projected_usd": round(total(nb, k, sub), 2)}
            for o, nb, k, sub in options]
    pick = next((r for r in rows if r["projected_usd"] <= a.budget), None)
    proj = {"per_call_usd_from_smoke": per_call, "buyer_usd_per_message": buyer_call, "safety_factor": SAFETY,
            "per_conversation_usd": conv, "dry_run_calls": dry, "options": rows, "budget_usd": a.budget, "pick": pick}
    (OUT / "projection.json").write_text(json.dumps(proj, indent=1))
    lines = ["# Projected cost of the full tournament", "",
             "Method: (shop-side calls per conversation, from the dry run over all buyers) x ($ per shop call, measured "
             f"in the live smoke) + (buyer messages per conversation) x ($ per buyer message, smoke) x 1.1, all x {SAFETY}.",
             "Rule policies have no shop-side model cost; their buyer text costs the same as for the LLM policies.", "",
             "| Policy | shop calls/conv (dry run) | $ per shop call (smoke) | projected $ per conversation |", "|---|---|---|---|"]
    for p in ALL:
        src = p if p in per_call else "shop_rule"
        lines.append(f"| {p} | {dry[p]['seller_calls_per_conv']:.2f} | {per_call.get(src, 0):.5f} | "
                     f"{conv[p]['seller'] + conv[p]['buyer']:.5f} |")
    lines += ["", f"Buyer message: ${buyer_call:.6f} each (smoke).", "",
              "| Option | buyers | K for LLM policies | buyers with K repeats | projected $ |", "|---|---|---|---|---|"]
    lines += [f"| {r['option']} | {r['buyers']} | {r['K_llm']} | {r['subsample_for_K']} | {r['projected_usd']:.2f} |" for r in rows]
    lines += ["", f"Budget for the full run: ${a.budget:.2f}. Proposed: option {pick['option'] if pick else 'none fits'}."]
    (OUT / "PROJECTION.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def cmd_manifest(a):
    proj = json.loads((OUT / "projection.json").read_text())
    pick = proj["pick"]
    cs = cards()
    order = [c.id for c in cs]
    random.Random(B.GENERATOR_SEED + 1).shuffle(order)
    sub = sorted(order[: pick["subsample_for_K"]]) if pick["subsample_for_K"] < len(cs) else "all"
    smoke = {"buyers": [c.id for c in smoke_buyers(cs)], "policies": list(SMOKE_POLICIES),
             "outcomes": "tournament/smoke/outcomes.jsonl",
             "note": "ran before three simulator fixes it exposed (the buyer writer invented requests; a family buyer "
                     "answered every check-in, looping to the message cap; family deliberation waited for an 'info' "
                     "message). Per-call prices are unaffected. Proposed first step of the full run: re-run these 10 "
                     "conversations on the frozen code and compare."}
    m = MF.build(OUT / "buyers.json", pick["buyers"], pick["K_llm"], sub, a.buyer_model, BUYER_PARAMS[a.buyer_model],
                 smoke, {"file": "tournament/PROJECTION.md", "projected_usd": pick["projected_usd"],
                         "option": pick["option"], "budget_usd": proj["budget_usd"]})
    h = MF.write(m)
    print(f"MANIFEST.json sha256 {h}")


def cmd_full(a):
    recorded, problems = MF.verify()
    if a.approve != recorded or problems:
        print(f"refusing: --approve must equal the recorded manifest hash {recorded}; problems: {problems}")
        return 2
    print("full run is phase B; not implemented in phase A")
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(prog="second_ask.tournament.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "dry-run", "writers"):
        sub.add_parser(name)
    s = sub.add_parser("smoke")
    s.add_argument("--buyer-model", required=True, choices=sorted(BUYER_PARAMS))
    p = sub.add_parser("project")
    p.add_argument("--budget", type=float, default=12.0)
    mf = sub.add_parser("manifest")
    mf.add_argument("--buyer-model", required=True, choices=sorted(BUYER_PARAMS))
    f = sub.add_parser("full")
    f.add_argument("--approve", required=True)
    a = ap.parse_args(argv)
    return {"generate": cmd_generate, "dry-run": cmd_dry_run, "writers": cmd_writers, "smoke": cmd_smoke,
            "project": cmd_project, "manifest": cmd_manifest, "full": cmd_full}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
