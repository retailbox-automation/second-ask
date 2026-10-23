"""The 20 negotiation states used by spikes S3 (label) and S7 (move choice).

Written by hand before either spike ran. Each state has a gold label set (the first is the primary label; others are
also accepted) and a gold set of acceptable move ids from the menu the code builds. Running this file writes
evidence/spikes/states.json with every state's menu, so the gold sets can be checked against the real menus.
Shops, prices and messages are invented for the test; no real customer data.
"""
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from second_ask.agent import Conversation, Event  # noqa: E402
from second_ask.compliance import Recipient  # noqa: E402
from second_ask.menu import Estimate, LineItem, Slot, build_menu  # noqa: E402
from second_ask.trace import Trace  # noqa: E402

T0 = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 New York: every estimate was sent then
QUOTED = Slot("quoted", T0 + timedelta(days=8, hours=-1))  # Tuesday Oct 27, 09:00 New York
OPEN = [Slot("thu", T0 + timedelta(days=3, hours=-1)), Slot("fri", T0 + timedelta(days=4, hours=-1)),
        Slot("thu2", T0 + timedelta(days=10, hours=-1)), Slot("sat2", T0 + timedelta(days=12, hours=-1))]


def item(name, price, optional=False):
    return LineItem(name, price, round(price * 0.55, 2), optional)


ESTIMATES = {
    "hvac_repair": ("Cool Air Heating & Cooling", "HVAC", "AC not cooling: replace capacitor and contactor",
                    (item("Capacitor and contactor replacement", 560), item("Surge protector", 180, True))),
    "water_heater": ("Blue Line Plumbing", "plumbing", "Replace 40-gallon gas water heater",
                     (item("Water heater replacement", 1950), item("Expansion tank", 240, True))),
    "roof_repair": ("Ridgeway Roofing", "roofing", "Repair leak at chimney flashing",
                    (item("Chimney flashing repair", 1150), item("Gutter cleaning", 190, True))),
    "panel": ("Bright Circuit Electric", "electrical", "Upgrade 100A panel to 200A",
              (item("200A panel upgrade", 3400), item("Whole-home surge protection", 420, True))),
    "hvac_system": ("Cool Air Heating & Cooling", "HVAC", "Replace 3-ton AC and air handler",
                    (item("3-ton AC and air handler", 8900), item("Smart thermostat", 380, True),
                     item("UV air purifier", 650, True))),
    "shower_valve": ("Blue Line Plumbing", "plumbing", "Fix leaking shower valve", (item("Shower valve repair", 420),)),
}

# id, estimate, channel, days since sent, views, [(hours after sending, who, text)], gold labels, gold moves
STATES = [
    ("S01", "hvac_repair", "email", 2, 2, [(47, "customer", "Another company quoted $650 for the same repair. Can you get closer to that?")],
     ["price"], ["drop_surge_protector", "concede_pkg_floor", "concede_pkg_5"]),
    ("S02", "water_heater", "email", 3, 3, [(70, "customer", "Got a quote for $1,600 installed. Yours is way more than that.")],
     ["price"], ["answer", "concede_core_floor", "drop_expansion_tank"]),
    ("S03", "panel", "email", 2, 4, [(46, "customer", "Our budget is $3,600 max. If you can do that we'll book.")],
     ["price"], ["concede_pkg_floor", "drop_whole_home_surge_protection"]),
    ("S04", "roof_repair", "sms", 1, 1, [(23, "customer", "Honestly that's a lot more than we expected.")],
     ["price"], ["drop_gutter_cleaning", "ask_why"]),
    ("S05", "hvac_system", "email", 3, 2, [(71, "customer", "Do we really need the UV purifier and the smart thermostat? That's a lot of extra money.")],
     ["price", "other"], ["drop_all_optional", "drop_uv_air_purifier", "drop_smart_thermostat", "answer"]),
    ("S06", "water_heater", "sms", 2, 1, [(47, "customer", "We want to do it, but not until after the 15th of next month when we get paid. Can it wait till then?")],
     ["timing"], ["checkin_30"]),
    ("S07", "hvac_repair", "sms", 3, 1, [(70, "customer", "We're away for the next ten days. Can we pick this up when we're back?")],
     ["timing"], ["checkin_14"]),
    ("S08", "hvac_repair", "sms", 2, 2, [(46, "customer", "The AC is out and it's 92 in the house. Your slot is next week and someone else can come tomorrow.")],
     ["timing"], ["earlier_slot_1"]),
    ("S09", "roof_repair", "email", 2, 2, [(47, "customer", "Rain is coming this weekend. Can you get here before Saturday?")],
     ["timing"], ["earlier_slot_1", "earlier_slot_2"]),
    ("S10", "panel", "email", 3, 3, [(70, "customer", "My husband wants to go over it with me this weekend before we decide.")],
     ["family"], ["answer", "checkin_3", "checkin_7"]),
    ("S11", "hvac_system", "email", 4, 3, [(94, "customer", "My wife and I need to talk it over. She's not sure about the UV purifier.")],
     ["family", "price"], ["drop_uv_air_purifier", "answer"]),
    ("S12", "shower_valve", "sms", 2, 1, [(45, "customer", "I rent, so the landlord has to approve repairs. I sent him your quote.")],
     ["family"], ["answer", "checkin_3", "checkin_7"]),
    ("S13", "hvac_repair", "email", 4, 0, [],
     ["channel"], ["resend_sms"]),
    ("S14", "roof_repair", "sms", 5, 0, [],
     ["channel"], ["resend_email"]),
    ("S15", "panel", "email", 6, 1, [(143, "customer", "Sorry, just saw this. I hardly check email. Can you text me instead?")],
     ["channel"], ["resend_sms"]),
    ("S16", "hvac_repair", "sms", 1, 1, [(22, "customer", "Looks good. When can you come?")],
     ["ready_to_book"], ["offer_slot", "earlier_slot_1", "earlier_slot_2"]),
    ("S17", "water_heater", "email", 2, 2, [(44, "customer", "OK let's do it. Does the price include hauling away the old tank?")],
     ["ready_to_book", "other"], ["offer_slot", "answer", "earlier_slot_1", "earlier_slot_2"]),
    ("S18", "roof_repair", "email", 2, 2, [(45, "customer", "What kind of warranty comes with the flashing repair?")],
     ["other"], ["answer"]),
    ("S19", "panel", "sms", 3, 1, [(68, "customer", "We already went with another electrician, thanks.")],
     ["other"], ["close_out"]),
    ("S20", "hvac_system", "email", 9, 3, [(2 * 24, "shop", "Hi, just checking in on your AC replacement estimate. Any questions?"),
                                           (8 * 24, "customer", "Still thinking.")],
     ["other", "timing", "family", "price"], ["ask_why"]),
]


def build(state):
    sid, key, channel, days, views, msgs, gold_labels, gold_moves = state
    shop, trade, title, items = ESTIMATES[key]
    est = Estimate(f"E-{sid}", shop, trade, title, items, 0.07, T0, channel, QUOTED)
    events = [Event(T0 + timedelta(hours=h), who, channel, text) for h, who, text in msgs]
    conv = Conversation(est, Recipient("America/New_York", "FL"), list(OPEN), events=events, views=views,
                        trace=Trace(sink=lambda _: None))
    now = T0 + timedelta(days=days, hours=1)
    return conv, now, gold_labels, gold_moves


def all_states():
    out = []
    for st in STATES:
        conv, now, gl, gm = build(st)
        menu = build_menu(conv.estimate, conv.offer, conv.slots_after(now))
        ids = [m.id for m in menu]
        missing = [g for g in gm if g not in ids]
        if missing:
            raise SystemExit(f"{st[0]}: gold moves {missing} are not on the menu {ids}")
        out.append({"id": st[0], "conv": conv, "now": now, "gold_labels": gl, "gold_moves": gm, "menu": menu})
    return out


if __name__ == "__main__":
    rows = [{"id": s["id"], "estimate": s["conv"].estimate.id, "trade": s["conv"].estimate.trade,
             "now": s["now"].isoformat(), "messages": [(e.who, e.text) for e in s["conv"].events],
             "gold_labels": s["gold_labels"], "gold_moves": s["gold_moves"],
             "menu": [{"id": m.id, "price": m.price, "description": m.description} for m in s["menu"]]}
            for s in all_states()]
    out = Path(__file__).resolve().parents[1] / "evidence" / "spikes" / "states.json"
    out.write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} states, gold moves all on their menus -> {out}")
