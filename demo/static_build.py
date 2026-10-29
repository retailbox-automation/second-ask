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
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo import replay  # noqa: E402
from second_ask.menu import amounts_in, build_menu, check_message, initial_offer  # noqa: E402
from second_ask.tournament import buyers as B  # noqa: E402
from second_ask.tournament.env import SELLER_LATENCY_DAYS, coarse_act  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
RESULTS = ROOT / "RESULTS.md"
MORNING = Path(__file__).resolve().parent / "data" / "read_first_morning.json"  # recorded runs of read_first/
TEST4 = ROOT / "data" / "second_read" / "test" / "results.json"                  # test 4 (v4), pre-registered
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


# ---------------------------------------------------------------------------------------------------------------------
# The first screen, "the owner's morning": every string the page shows, built from demo/data/read_first_morning.json
# (recorded runs of read_first/) and data/second_read/test/results.json (test 4). The browser only renders this.

GROUPS = ("ready", "tap", "checkin", "stopped")  # inbox order: ready to send, waiting for the owner's tap, check-ins, stopped
NUM_WORDS = {1: ("one", "a", "una", "un"), 2: ("two", "a couple of", "dos"), 3: ("three", "tres"), 4: ("four", "cuatro"),
             5: ("five", "cinco"), 6: ("six", "seis"), 7: ("seven", "siete"), 8: ("eight", "ocho")}
READY_CUES = r"\bbook\b|\bgo ahead\b|\bschedule\b|\bagend\w*"  # the words a "ready to book" read most often rests on
SNIPPET = 56  # characters of the reply shown in the inbox row
RESULTS_URL = "https://github.com/retailbox-automation/second-ask/blob/main/RESULTS.md"


def money(x):
    return f"${x:,.0f}" if abs(x - round(x)) < 0.005 else f"${x:,.2f}"


def usd_small(x):
    return "$0" if x == 0 else f"${x:.2g}"


def secs(x):
    return "under 1 ms" if x < 0.001 else (f"{x:.2f} s" if x >= 0.01 else f"{x * 1000:.0f} ms")


def day(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d:%a}, {d:%b} {d.day}"


def short_day(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d:%b} {d.day}"


def group_of(c):
    """The inbox group of a conversation, by a rule on the recorded decision."""
    if c["path"] == "stopped":
        return "stopped"
    if c["needs_owner"]:
        return "tap"
    return "checkin" if c["offer"]["timing"]["kind"] == "checkin" else "ready"


def fact_chips(facts):
    """[(key, chip text, kind, value)] for what the read found, in a fixed key order."""
    if not facts:
        return []
    out = []
    if facts.get("stop"):
        out.append(("stop", "asked to stop", "stop", True))
    if facts.get("ceiling_usd") is not None:
        out.append(("ceiling_usd", "ceiling " + money(facts["ceiling_usd"]), "money", facts["ceiling_usd"]))
    for name in facts.get("drop_items") or []:
        out.append(("drop_items", "drop: " + name, "drop", name))
    if facts.get("need_by"):
        out.append(("need_by", "need by " + short_day(facts["need_by"]), "date", facts["need_by"]))
    if facts.get("wait_weeks"):
        w = facts["wait_weeks"]
        out.append(("wait_weeks", f"wait {w} week" + ("" if w == 1 else "s"), "date", w))
    if facts.get("decider"):
        out.append(("decider", "decider: " + facts["decider"], "who", facts["decider"]))
    if facts.get("ready"):
        out.append(("ready", "ready to book", "ready", True))
    if not out:
        out.append(("none", "no price, date or stop in this reply", "none", None))
    return out


def find_span(text, key, value):
    """(start, end) of the words in the reply that carry this fact, found by code (a regex per fact), or None."""
    pats = []
    if key == "ceiling_usd":
        n = int(round(value))
        num = f"(?:{n:,}|{n})".replace(",", ",?")
        pats = [r"\$\s?" + num + r"(?:\.00)?\b", r"\b" + num + r"\s?(?:dollars|d[oó]lares)\b", r"\b" + num + r"\b"]
    elif key == "drop_items":
        pats = [re.escape(value)] + [r"\b" + re.escape(w) + r"\b" for w in sorted(value.split(), key=len, reverse=True)
                                     if len(w) >= 4 and w.lower() not in {"with", "from", "front", "back", "replace", "install"}]
    elif key == "need_by":
        d = date.fromisoformat(value)
        wd, n = f"{d:%A}", d.day
        pats = [rf"(?:(?:{wd}|{wd[:3]})\.?,?\s+)?(?:{d:%B}|{d:%b})\.?\s+{n}(?:st|nd|rd|th)?\b",
                rf"\b(?:the\s+)?{n}(?:st|nd|rd|th)\b", rf"\b{wd}\b"]
    elif key == "wait_weeks":
        words = "|".join(re.escape(w) for w in (str(value),) + NUM_WORDS.get(value, ()))
        pats = [rf"\b(?:{words})\s+(?:weeks?|semanas?)\b"]
    elif key == "decider":
        pats = [r"\b" + re.escape(value) + r"\b"]
    elif key == "ready":
        pats = [READY_CUES]
    for p in pats:
        m = re.search(p, text, re.I)
        if m:
            return m.span()
    return None


def highlighted(text, chips):
    """The reply cut into segments; the segment found for chip i carries hl=i. A find that overlaps an earlier one is
    dropped (that chip gets no line)."""
    spans = []
    for i, (key, _, _, value) in enumerate(chips):
        sp = find_span(text, key, value) if key not in ("stop", "none") else None
        if sp and not any(sp[0] < b and a < sp[1] for a, b, _ in spans):
            spans.append((sp[0], sp[1], i))
    spans.sort()
    segs, at = [], 0
    for a, b, i in spans:
        if a > at:
            segs.append({"text": text[at:a], "hl": None})
        segs.append({"text": text[a:b], "hl": i})
        at = b
    if at < len(text):
        segs.append({"text": text[at:], "hl": None})
    return segs, {i for _, _, i in spans}


def snippet(text):
    if len(text) <= SNIPPET:
        return text
    return text[:SNIPPET].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"


def _fact_value(key, v):
    if key == "ceiling_usd":
        return "no ceiling" if v is None else "ceiling " + money(v)
    if key == "stop":
        return "a request to stop" if v else "no stop"
    if key == "ready":
        return "ready to book" if v else "not ready"
    if key == "drop_items":
        return "drop " + ", ".join(v) if v else "no items dropped"
    if key == "need_by":
        return "need by " + short_day(v) if v else "no deadline"
    if key == "wait_weeks":
        return f"wait {v} weeks" if v else "no wait"
    if key == "decider":
        return "decider " + v if v else "no one else decides"
    return str(v)


def v4_markers(c):
    """The two honest markers from the frozen v4 run (rows.jsonl): which read missed this reply, and how."""
    out, v4 = [], c["v4"]
    for arm, kind, tag, who in (("super", "misread", "v4 check: Nemotron misread this one", "Nemotron 3 Super"),
                                ("rules", "rules", "v4 check: the rulebook missed this one", "The rulebook")):
        if v4[arm]["match"] is False:
            diffs = [k for k in v4["true_facts"] if v4[arm]["facts"].get(k) != v4["true_facts"][k]]
            said = "; ".join(f"read {_fact_value(k, v4[arm]['facts'].get(k))}, the reply says "
                             f"{_fact_value(k, v4['true_facts'][k])}" for k in diffs)
            detail = f"{who} in test 4: {said}."
            p = v4["perfect_read_offer"]
            if p and p.get("stop"):
                detail += " A perfect read stops the conversation."
            elif p:
                detail += f" A perfect read offers {money(p['price'])}."
            out.append({"kind": kind, "text": tag, "detail": detail})
    return out


def offer_box(c):
    """What the shop's code decided, as short pieces, checked against the estimate: never below the floor."""
    o, est, mv = c["offer"], c["estimate"], c.get("move") or {}
    lst = sum(i["price"] for i in est["items"] if i["name"] not in o["dropped"])
    if o["price"] >= lst - 0.005:
        floor_txt = "list price, no discount"
    elif not o["dropped"]:
        if o["price"] < est["floor"] - 0.005:
            raise SystemExit(f"{c['id']}: offer {o['price']} is below the floor {est['floor']}")
        floor_txt = money(lst - o["price"]) + " below list, " + (
            "at your floor" if abs(o["price"] - est["floor"]) < 0.005 else "within your floor")
    else:
        raise SystemExit(f"{c['id']}: a discount on a smaller package; its floor is not in the recorded data")
    parts = [money(o["price"])] + [f"{n.lower()} dropped" for n in o["dropped"]]
    t = o["timing"]
    if t["kind"] == "slot":
        when = ("earlier slot " if mv.get("kind") == "earlier_slot" else "") + day(t["day"])
        if t.get("meets_deadline") is not None and (c["facts"] or {}).get("need_by"):
            when += (" (meets " if t["meets_deadline"] else " (misses ") + short_day(c["facts"]["need_by"]) + ")"
        parts.append(when)
    elif t["kind"] == "checkin":
        parts.append(f"check back in {t['days']} days")
    parts.append(floor_txt)
    return parts


def status_chip(c, g):
    o = c["offer"]
    if g == "stopped":
        return "Stopped"
    if g == "tap":
        return "Tap to approve · " + money(o["price"])
    if g == "checkin":
        return f"Check back in {o['timing']['days']} days"
    return f"Ready · {money(o['price'])} · {day(o['timing']['day'])}"


def steps_strip(c, g):
    """The steps with who did each and the recorded seconds and $ of that stage."""
    out = []
    for s in c["stages"]:
        meta = secs(s["seconds"]) + " · " + usd_small(s["usd"])
        if s["stage"] == "read":
            out.append({"who": "model", "text": "Read · " + s["actor"], "meta": meta})
        elif s["stage"] == "decide":
            out.append({"who": "code", "text": "Stop · code" if g == "stopped" else "Price and date · code", "meta": meta})
        elif s["stage"] == "write":
            if c["writer"]["template_used"]:
                out.append({"who": "code", "text": "Draft · code template", "meta": "draft by " + s["actor"] + " refused · " + meta})
            else:
                out.append({"who": "model", "text": "Draft · " + s["actor"], "meta": meta})
        else:
            raise SystemExit(f"{c['id']}: unknown stage {s['stage']}")
    if not c["stages"]:
        out.append({"who": "code", "text": "Stop words · code", "meta": "no model call"})
    if g != "stopped":
        out.append({"who": "you", "text": "Send · you", "meta": "after your tap" if c["needs_owner"] else "one click"})
    return out


def open_item(c, g, markers):
    chips = fact_chips(c["facts"])
    segs, linked = highlighted(c["reply"], chips)
    read = next((s for s in c["stages"] if s["stage"] == "read"), None)
    item = {"title": c["estimate"]["title"], "pick_reason": "Why this reply is here: " + c["pick_reason"] + ".",
            "markers": markers, "segments": segs, "group": g, "steps": steps_strip(c, g),
            "read": None if read is None else {"label": read["actor"] + " read", "seconds": secs(read["seconds"]),
                                               "chips": [{"text": t, "kind": k, "linked": i in linked}
                                                         for i, (_, t, k, _) in enumerate(chips)]}}
    if g == "stopped":
        who = (f"Stopped by {read['actor']}'s read; the shop's stop words did not match." if read else
               "Stopped by the shop's code: its stop words matched before any model call.")
        item["stopped"] = {"head": "Stopped: no more messages", "who": who, "reason": c["stop_reason"]}
        return item
    write = next(s for s in c["stages"] if s["stage"] == "write")
    item["decided"] = {"head": "Your code decided", "parts": offer_box(c)}
    item["draft"] = {"head": ("Draft replaced by the code template: " + "; ".join(c["writer"]["problems"])
                              if c["writer"]["template_used"] else f"Draft, written by {write['actor']}, checked by your code"),
                     "template": c["writer"]["template_used"], "text": c["message"]}
    item["buttons"] = {"primary": "Approve and send" if c["needs_owner"] else "Send", "secondary": "Change",
                       "note": "A discount: it goes out only after your tap." if c["needs_owner"] else None}
    return item


def proof_strip(t4):
    sup, rules = t4["arms"]["super"]["match_rate_pct"], t4["arms"]["rules"]["match_rate_pct"]
    return {"text": f"Test 4, pre-registered, {t4['lines']} synthetic replies: same offer as a perfect read, "
                    f"Nemotron 3 Super {sup} %, hardened rules {rules} % ({t4['verdict']}). Tests 1–3: when Nemotron chose "
                    f"the price, it never beat the rules, so prices stay in code.",
            "link": "See the tests", "href": RESULTS_URL}


def morning_view(m, t4):
    convs, s = m["conversations"], m["summary"]
    if len(convs) != s["replies_overnight"]:
        raise SystemExit("read_first_morning.json: conversations and summary disagree")
    by_group = {g: sorted((c for c in convs if group_of(c) == g), key=lambda c: c["id"]) for g in GROUPS}
    checks = {"ready_to_send_without_tap": len(by_group["ready"]) + len(by_group["checkin"]),
              "waiting_for_owner_tap": len(by_group["tap"]), "stop_requests_honoured": len(by_group["stopped"])}
    for k, n in checks.items():
        if s[k] != n:
            raise SystemExit(f"read_first_morning.json: summary {k} = {s[k]}, the conversations give {n}")
    for c in by_group["tap"]:  # "discounts wait for your tap": every tap row is a price below list
        if c["offer"]["price"] >= sum(i["price"] for i in c["estimate"]["items"] if i["name"] not in c["offer"]["dropped"]):
            raise SystemExit(f"{c['id']}: waits for a tap but is not a discount")
    n, read_n = len(convs), sum(1 for c in convs if any(st["stage"] == "read" for st in c["stages"]))
    actor = next(st["actor"] for c in convs for st in c["stages"] if st["stage"] == "read")
    who_read = (f"{actor} read every reply." if read_n == n else
                f"{actor} read {read_n} of {n} replies; the shop's stop words caught the other "
                f"{'one' if n - read_n == 1 else n - read_n} first.")
    rows = []
    for g in GROUPS:
        for c in by_group[g]:
            mk = v4_markers(c)
            rows.append({"id": c["id"], "group": g, "title": c["estimate"]["title"], "snippet": snippet(c["reply"]),
                         "chip": status_chip(c, g), "pick_reason": c["pick_reason"],
                         "markers": [{"kind": x["kind"], "text": x["text"]} for x in mk], "open": open_item(c, g, mk)})
    return {
        "brand": "Second Ask",
        "pills": ["Replay of recorded runs · synthetic replies", "NVIDIA Nemotron on Nebius Token Factory"],
        "headline": f"Good morning. {n} customers replied overnight. Your offers are ready.",
        "subline": who_read + " The shop's code set every price and date, never below your floor. Discounts wait for your tap.",
        "tiles": [{"key": "replies_overnight", "value": str(s["replies_overnight"]), "label": "replies overnight"},
                  {"key": "ready_to_send_without_tap", "value": str(s["ready_to_send_without_tap"]),
                   "label": "offers ready to send without a tap"},
                  {"key": "waiting_for_owner_tap", "value": str(s["waiting_for_owner_tap"]), "label": "discounts wait for your tap"},
                  {"key": "stop_requests_honoured", "value": str(s["stop_requests_honoured"]),
                   "label": "asked to stop · no more messages"},
                  {"key": "total_usd", "value": usd_small(s["total_usd"]), "label": "of Nemotron for the whole morning"}],
        "rows": rows, "default_open": by_group["ready"][0]["id"] if by_group["ready"] else rows[0]["id"],
        "hl_note": "highlights matched by code",
        "toast": "Replay: nothing is sent",
        "proof": proof_strip(t4),
        "earlier": "Tests 1–3: the earlier design, where Nemotron chose the move",
    }


def data():
    store = replay.load()
    morning = morning_view(json.loads(MORNING.read_text()), json.loads(TEST4.read_text()))
    cards = {c.id: c for c in B.load(replay.BUYERS)}
    return {"notice": "Seeded demo shop and simulated homeowners. Replays are real recorded conversations from our "
                      "pre-registered tournament (manifest 1c231403); all $ are simulated margin on illustrative prices.",
            "source": store["source"], "rule": store["rule"],
            "results": main_comparison(RESULTS.read_text()),
            "scenarios": [scenario(sc, cards[sc["buyer"]]) for sc in store["scenarios"]], "morning": morning}


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
