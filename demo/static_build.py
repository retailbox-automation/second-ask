"""Static REPLAY site in demo/static/: hand-written index.html, app.css, app.js plus three generated files.

    python3 -m demo.static_build           write demo/static/data.js, results.html, robots.txt, _headers
    python3 -m demo.static_build check     exit 1 if any generated file differs from a fresh build

No server, no network, no keys: the page reads `window.SECOND_ASK` from data.js, so it also works from file://.
data.js carries every replay scenario of demo/data/replay.json unchanged (transcript, steps, flags, result, estimate)
and adds, per scenario, what the shop's own code says about it (second_ask/menu.py, the buyer card): the owner's floor,
the open crew slots, the quoted slot, and for each decision the menu the code built, the move picked from it and its
price. Each picked move is checked against the reconstructed menu and against the message that was sent (the amounts
in it); a mismatch stops the build. The main comparison shown on the page is parsed from RESULTS.md.
Output is deterministic: sorted keys, fixed separators, no timestamps.
"""
import html, json, re, sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo import replay  # noqa: E402
from second_ask.menu import amounts_in, build_menu, check_message, initial_offer  # noqa: E402
from second_ask.tournament import buyers as B  # noqa: E402
from second_ask.tournament.env import SELLER_LATENCY_DAYS, coarse_act  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
RESULTS = ROOT / "RESULTS.md"
GENERATED = ("data.js", "results.html", "robots.txt", "_headers")
SLOT_FMT = "%a %b %d, %I:%M %p"  # the clock the menu and the messages use (simulator time, printed as UTC)


def _slot(s):
    return {"id": s.id, "start": s.start.strftime("%Y-%m-%dT%H:%M"), "label": s.start.strftime(SLOT_FMT).replace(" 0", " ")}


def _label(step):
    """'tool: price' / 'json_object: opt_out' -> 'price' / 'opt_out' (the recorded note of a classify step)."""
    return step["note"].split(":", 1)[1].strip() if ":" in step["note"] else None


def _move(m, est):
    return {"id": m.id, "kind": m.kind, "description": m.description, "price": m.price, "dropped": sorted(m.dropped),
            "floor": est.floor(m.dropped), "list": est.list_price(m.dropped), "slot": _slot(m.slot) if m.slot else None,
            "channel": m.channel, "needs_owner": m.needs_owner, "proposes_booking": m.proposes_booking}


def effect(move, by, offer, est, ready):
    """The $ effect of the move against a discount-first reply on the same menu: what `shop_rule` (our stand-in for a
    naive shop, second_ask/tournament/policies.py ShopRule) would offer at this point: the slot to a customer who is
    ready (the simulator's coarse act, env.coarse_act), otherwise a concession to the package floor whenever the price
    is above it. `kept` = the move's price minus that offer (what the move does not give away); None when the
    discount-first reply would not discount here."""
    alt = by.get("concede_pkg_floor") if not ready and offer.price > est.floor(offer.dropped) else None
    if alt is None:
        return {"discount_first": None, "kept": None}
    return {"discount_first": {"id": alt.id, "price": alt.price, "description": alt.description},
            "kept": round(move.price - alt.price, 2)}


def spec(card, before):
    """The simulator's spec of the customer message being answered: a channel customer's first message is "missed",
    any customer's first is "first", later ones "again" (env.run_one)."""
    n = sum(e["who"] == "customer" for e in before)
    return "missed" if card.type == "channel" and n == 1 else ("first" if n == 1 else "again")


def decisions(sc, card):
    """One entry per recorded decision: label steps, choose steps, and for a sent message the code's menu at that moment.

    The menu is rebuilt the way the agent built it (agent.Agent.decide): the standing offer after the previous moves,
    the open slots after the decision time, the 90-day SMS check-in limit. The decision time is the customer's message
    plus the shop's ten-minute latency when the shop was answering, otherwise the send time (a scheduled check-in)."""
    est = card.estimate()
    offer, shop_msgs = initial_offer(est), [e for e in sc["transcript"] if e["who"] == "shop"]
    out = []
    for k, group in enumerate(replay.decisions(sc)):
        cls = [s for s in group if s["step"] == "classify"]
        cho = [s for s in group if s["step"] == "choose"]
        d = {"n": k + 1, "label": _label(cls[-1]) if cls else None, "label_steps": cls, "choose_steps": cho,
             "flags": [f for x in sc["flags"] for f in x["flags"]
                       if k < len(shop_msgs) and abs(x["day"] - shop_msgs[k]["day"]) < 1e-9]}
        if k >= len(shop_msgs):  # a decision that sent nothing: the conversation stopped here
            d["stopped"] = sc["result"]["end"]
            out.append(d)
            continue
        msg = shop_msgs[k]
        before = [e for e in sc["transcript"] if e["day"] <= msg["day"] and e is not msg]
        replying = bool(before) and before[-1]["who"] == "customer"
        said = [a for e in before if e["who"] == "customer" for a in amounts_in(e["text"])]
        now_day = min(msg["day"], before[-1]["day"] + SELLER_LATENCY_DAYS) if replying else msg["day"]
        now = B.T0 + timedelta(days=now_day)
        max_checkin = 90 - (now - est.sent_at).days if offer.channel == "sms" else None
        menu = build_menu(est, offer, [s for s in card.open_slots() if s.start > now], max_checkin_days=max_checkin)
        by = {m.id: m for m in menu}
        if msg["move"] not in by:
            raise SystemExit(f"{sc['id']} decision {k + 1}: move {msg['move']} is not on the rebuilt menu {sorted(by)}")
        move = by[msg["move"]]
        body = re.sub(r"^\[[^\]]*\]\s*|\s*Reply STOP to opt out\.\s*$", "", msg["text"])
        bad = [p for p in check_message(body, move, est, offer, said) if p.startswith("amount")]
        if bad:
            raise SystemExit(f"{sc['id']} decision {k + 1}: the sent message does not match move {move.id}: {bad}")
        d.update({"day": msg["day"], "replying_to": before[-1]["text"] if replying else None,
                  "price_before": offer.price, "move": _move(move, est), "menu": [_move(m, est) for m in menu],
                  "effect": effect(move, by, offer, est, replying and coarse_act(card, spec(card, before)) == "ready")})
        out.append(d)
        offer = move.offer(offer)
    return out, offer


def scenario(sc, card):
    est = card.estimate()
    ds, final = decisions(sc, card)
    return {**sc, "decisions_view": ds, "code": {
        "list": est.list_price(), "floor": est.floor(), "max_discount": est.max_discount, "channel": est.channel,
        "quoted_slot": _slot(est.default_slot), "open_slots": [_slot(s) for s in card.open_slots()],
        "final_offer_slot": _slot(final.slot), "items": [{"name": i.name, "price": i.price, "optional": i.optional}
                                                                 for i in est.items]}}


def main_comparison(text):
    """The pre-registered main comparison as written in RESULTS.md (first table under '## Main comparison')."""
    sec = text.split("## Main comparison", 1)[1].split("\n## ", 1)[0]
    rows = {}
    for line in sec.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] in ("routed - smart_rules", "super_only - smart_rules"):
            rows[cells[0]] = {"total": cells[1], "per_customer": cells[2], "reading": cells[3]}
    if set(rows) != {"routed - smart_rules", "super_only - smart_rules"}:
        raise SystemExit("RESULTS.md: main comparison table not found")
    headline = text.split("\n\n", 2)[1].strip().strip("*")
    return {"rows": rows, "headline": headline, "label_default": "Super" if "Super labels by default" in headline else None}


def data():
    store = replay.load()
    cards = {c.id: c for c in B.load(replay.BUYERS)}
    return {"notice": "Seeded demo shop and simulated homeowners. Replays are real recorded conversations from our "
                      "pre-registered tournament (manifest 1c231403); all $ are simulated margin on illustrative prices.",
            "source": store["source"], "rule": store["rule"],
            "results": main_comparison(RESULTS.read_text()),
            "scenarios": [scenario(sc, cards[sc["buyer"]]) for sc in store["scenarios"]]}


def _inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f"<a href=\"{m.group(2)}\">{m.group(1)}</a>"
                  if m.group(2).startswith("#") else m.group(1), s)


def _slug(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def markdown(md):
    """The small Markdown subset RESULTS.md uses: headings, paragraphs, tables, lists, code blocks."""
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j]), quote=False) + "</code></pre>")
            i = j + 1
        elif m := re.match(r"(#{1,4}) (.*)", ln):
            n, t = len(m.group(1)), m.group(2)
            out.append(f"<h{n} id=\"{_slug(t)}\">{_inline(t)}</h{n}>")
            i += 1
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.fullmatch(r"\|[\s|:-]+\|", lines[i].strip()):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], rows[1:]
            out.append("<div class=\"t\"><table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table></div>")
        elif re.match(r"(\d+\.|-) ", ln):
            tag = "ol" if ln[0].isdigit() else "ul"
            items = []
            while i < len(lines) and (re.match(r"(\d+\.|-) ", lines[i]) or (lines[i].startswith("  ") and items)):
                if lines[i].startswith("  "):
                    items[-1] += " " + lines[i].strip()
                else:
                    items.append(re.sub(r"^(\d+\.|-) ", "", lines[i]))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
        elif ln.strip():
            para = [ln]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"(#|\||```|\d+\. |- )", lines[i]):
                para.append(lines[i])
                i += 1
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
        else:
            i += 1
    return "\n".join(out)


RESULTS_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>Second Ask: tournament results</title>
<style>
body{{margin:0;background:#f6f3ee;color:#1d2230;font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Helvetica,Arial,sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:32px 20px 64px}} a{{color:#2f7fbf}}
.back{{display:inline-block;margin-bottom:18px;font-weight:600;text-decoration:none}}
h1{{font-size:34px;line-height:1.15;margin:0 0 16px}} h2{{font-size:24px;margin:36px 0 10px}}
code{{background:#ece7df;border-radius:4px;padding:0 4px;font-size:.9em;overflow-wrap:anywhere}}
pre{{background:#1d2230;color:#eef1f7;border-radius:10px;padding:14px 16px;overflow-x:auto}} pre code{{background:none;padding:0}}
.t{{overflow-x:auto;background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(20,24,40,.08);margin:12px 0}}
table{{border-collapse:collapse;font-size:14px;min-width:100%}} th,td{{padding:8px 10px;border-bottom:1px solid #eee8de;text-align:left;vertical-align:top}}
th{{background:#faf8f4}} p,li{{overflow-wrap:anywhere}}
</style></head><body><main><a class="back" href="index.html">&larr; Back to the replay demo</a>
{body}
</main></body></html>
"""


def files():
    js = "window.SECOND_ASK = " + json.dumps(data(), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + ";\n"
    return {"data.js": js, "results.html": RESULTS_PAGE.format(body=markdown(RESULTS.read_text())),
            "robots.txt": "User-agent: *\nDisallow: /\n",
            "_headers": "/*\n  X-Robots-Tag: noindex, nofollow\n"}  # Cloudflare Pages response headers


if __name__ == "__main__":
    built = files()
    if (sys.argv[1] if len(sys.argv) > 1 else "build") == "check":
        stale = [n for n, t in built.items() if not (STATIC / n).exists() or (STATIC / n).read_text() != t]
        print("generated files up to date:", not stale, *stale)
        sys.exit(1 if stale else 0)
    STATIC.mkdir(exist_ok=True)
    for n, t in built.items():
        (STATIC / n).write_text(t)
    print("wrote", ", ".join(f"demo/static/{n} ({len(t):,} bytes)" for n, t in built.items()))
