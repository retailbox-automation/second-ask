"""Simulated homeowners for the pre-registered tournament.

Every hidden parameter (walkaway price, objection type, patience, channel, readiness, deadlines) is drawn here, in code,
from a fixed seed. The buyer model never sees the walkaway price and never decides anything: it only writes the words
(see env.py). Prices are illustrative, not market data; the assumptions are listed in ASSUMPTIONS and in the manifest.
"""
import json, math, random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..menu import Estimate, LineItem, Slot

SEEDS = Path(__file__).resolve().parents[2] / "seeds"
T0 = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)  # Monday 09:00 New York (EST): every estimate is sent then
GENERATOR_SEED = 20261023
TYPE_MIX = {"price": 0.25, "timing_pause": 0.17, "timing_slot": 0.10, "family": 0.17, "channel": 0.15, "ready": 0.11,
            "opt_out": 0.05}
LABEL_OF = {"price": "price", "timing_pause": "timing", "timing_slot": "timing", "family": "family", "channel": "channel",
            "ready": "ready_to_book", "opt_out": "opt_out"}
ASSUMPTIONS = {
    "cost_ratio": "every line item costs the shop 55% of its price (gross margin 45%)",
    "max_discount": "owner's floor: 5-10% below list on jobs of $5,000 or more (shop aggregate in our evidence); "
                    "8-15% on smaller repairs (assumption)",
    "type_mix": "price is a minority of reasons (shop aggregate: price and competitor ~13% of recorded reasons; "
                "homeowner history: pauses, family decisions and missed messages more common); mix fixed in TYPE_MIX",
    "prices": "illustrative list-price ranges per job, rounded to $10; not market data",
}
COST_RATIO = 0.55
SHOP_NAME = "Sunrise Home Services"  # made up for the simulation; any real business with this name is unrelated
CATALOG = {  # trade -> [(job, list price range, [(optional item, price range)])]
    "HVAC": [("AC capacitor and contactor replacement", (450, 900), [("Surge protector", (150, 250))]),
             ("Furnace igniter and flame sensor", (300, 650), [("Annual maintenance plan", (150, 220))]),
             ("Replace 3-ton AC and air handler", (7800, 11500), [("Smart thermostat", (300, 450)),
                                                                  ("UV air purifier", (500, 800))])],
    "plumbing": [("Replace 40-gallon water heater", (1600, 2600), [("Expansion tank", (180, 300))]),
                 ("Fix leaking shower valve", (300, 650), []),
                 ("Repipe kitchen and bath supply lines", (3500, 6500), [("Whole-home shutoff valve", (200, 400))])],
    "roofing": [("Repair leak at chimney flashing", (800, 1800), [("Gutter cleaning", (150, 250))]),
                ("Replace damaged shingles on one slope", (1500, 3500), [("Ridge vent", (400, 900))]),
                ("Full asphalt roof replacement", (9000, 18000), [("Gutter guards", (900, 1800))])],
    "electrical": [("Replace 100A panel with 200A", (2800, 4200), [("Whole-home surge protection", (300, 600))]),
                   ("Add GFCI outlets in kitchen and baths", (250, 700), []),
                   ("Install EV charger circuit", (900, 1800), [("Load management module", (300, 600))])],
}
STYLE_HINTS = {  # CaSiNo strategy label -> how this homeowner tends to talk
    "small-talk": "adds a bit of friendly small talk", "self-need": "explains their own situation and needs",
    "other-need": "mentions what other people in the household need", "elicit-pref": "asks which option the shop recommends",
    "promote-coordination": "suggests working something out together", "vouch-fair": "appeals to what seems fair",
    "showing-empathy": "acknowledges the shop's side", "no-need": "says they could do without part of it",
    "uv-part": "suggests the work is worth less than quoted",
}


@dataclass
class Card:
    id: str
    type: str
    archetype: str
    style: list
    casino_ref: str
    trade: str
    title: str
    items: list                   # [name, price, optional]
    max_discount: float
    channel: str                  # where the estimate was sent
    reads_channel: str            # the channel the buyer actually reads ("both" unless type == channel)
    quoted_slot_day: float
    open_slot_days: list
    walkaway: float               # hidden: the most they would pay for the job (any package)
    anchor: float | None          # the number they say out loud (below the walkaway), if any
    anchor_kind: str | None
    patience: int                 # pushes without what they need before they hire someone else
    first_reply_hours: float
    clarity: str                  # "clear" or "vague" first message
    ready_after_days: float | None = None
    need_by_days: float | None = None
    family_delay_days: float | None = None
    family_who: str | None = None
    opt_out_style: str | None = None
    hire_by_days: float | None = None  # hidden: after this day they have hired someone else
    notice_day: float | None = None    # channel buyers: from this day they also see the channel the estimate went to
    tz: str = "America/New_York"
    state: str = "FL"
    notes: dict = field(default_factory=dict)

    def estimate(self):
        items = tuple(LineItem(n, float(p), round(p * COST_RATIO, 2), bool(o)) for n, p, o in self.items)
        return Estimate(self.id, SHOP_NAME, self.trade, self.title, items, self.max_discount, T0,
                        self.channel, Slot(f"{self.id}-quoted", T0 + timedelta(days=self.quoted_slot_day)))

    def open_slots(self):
        return [Slot(f"{self.id}-d{d:g}", T0 + timedelta(days=d)) for d in sorted(set(self.open_slot_days))]


def _r10(x):
    return int(round(x / 10) * 10)


def _quota(n):
    counts = {t: math.floor(n * w) for t, w in TYPE_MIX.items()}
    for t in sorted(TYPE_MIX, key=lambda t: -(n * TYPE_MIX[t] - counts[t]))[: n - sum(counts.values())]:
        counts[t] += 1
    return counts


def generate(n=200, seed=GENERATOR_SEED):
    rng = random.Random(seed)
    cases = json.loads((SEEDS / "home_cases.json").read_text())["cases"]
    profiles = json.loads((SEEDS / "casino_profiles.json").read_text())["profiles"]
    types = [t for t, k in _quota(n).items() for _ in range(k)]
    rng.shuffle(types)
    cards = []
    for i, typ in enumerate(types):
        trade = rng.choice(sorted(CATALOG))
        job, (lo, hi), opts = rng.choice(CATALOG[trade])
        items = [[job, _r10(rng.uniform(lo, hi)), False]] + [[o, _r10(rng.uniform(a, b)), True] for o, (a, b) in opts]
        total = sum(p for _, p, _ in items)
        max_discount = rng.choice([0.05, 0.06, 0.07, 0.08, 0.10]) if total >= 5000 else rng.choice([0.08, 0.10, 0.12, 0.15])
        channel = "email" if rng.random() < 0.6 else "sms"
        quoted = rng.randint(6, 10)
        open_days = sorted(rng.sample([d for d in range(1, 15) if d != quoted], 4) + [quoted])
        prof = rng.choice(profiles)
        style = [STYLE_HINTS[s] for s in prof["strategies"] if s in STYLE_HINTS][:3]
        arche = [c for c in cases if c["objection"] == typ]
        case = rng.choice(arche) if arche and rng.random() < 0.5 else None
        seeds = case["seeds"] if case else {}
        first_h = round(rng.uniform(1, 48), 2)
        card = dict(id=f"B{i + 1:03d}", type=typ, archetype=case["id"] if case else "generic", style=style,
                    casino_ref=f"dialogue {prof['dialogue_id']} / {prof['participant']}", trade=trade, title=job,
                    items=items, max_discount=max_discount, channel=channel, reads_channel="both",
                    quoted_slot_day=float(quoted), open_slot_days=[float(d) for d in open_days], walkaway=0.0,
                    anchor=None, anchor_kind=None, patience=2, first_reply_hours=first_h,
                    clarity="vague" if rng.random() < 0.3 else "clear")
        ratio = lambda a, b: rng.uniform(*seeds.get("walkaway_ratio", (a, b)))  # noqa: E731
        if typ == "price":
            card["walkaway"] = _r10(total * ratio(0.80, 0.97))
            card["anchor"] = _r10(card["walkaway"] * rng.uniform(0.88, 0.97))
            card["anchor_kind"] = seeds.get("anchor_kind") or rng.choice(["competitor quote", "budget"])
            card["patience"] = seeds.get("patience") or rng.choice([1, 2, 2, 3])
            card["hire_by_days"] = round(first_h / 24 + rng.uniform(0.25, 3.0), 2)  # shopping around: decides fast
        elif typ == "timing_pause":
            card["walkaway"] = _r10(total * rng.uniform(1.0, 1.2))
            card["ready_after_days"] = round(first_h / 24 + rng.uniform(*seeds.get("ready_after_days", (7, 45))), 2)
            card["patience"] = seeds.get("patience") or rng.choice([1, 2, 2, 3, 3])
            card["hire_by_days"] = round(card["ready_after_days"] + 21, 2)  # ready, and nobody called: hires someone
        elif typ == "timing_slot":
            card["walkaway"] = _r10(total * rng.uniform(1.0, 1.15))
            lo, hi = seeds.get("need_by_days", (1, 3))
            if rng.random() < 0.75:  # the shop has a slot early enough
                early = float(math.ceil(first_h / 24 + 0.5))
                need_by = early + rng.uniform(0, hi - lo)
                card["open_slot_days"] = sorted(set(card["open_slot_days"]) | {early})
            else:  # it does not: every open slot is after the buyer's deadline
                need_by = first_h / 24 + rng.uniform(lo, hi)
                card["open_slot_days"] = [d for d in card["open_slot_days"] if d > need_by]
            card["need_by_days"] = round(need_by, 2)
            card["patience"] = seeds.get("patience") or rng.choice([1, 2, 2])
            card["hire_by_days"] = card["need_by_days"]  # someone who comes sooner gets the job
        elif typ == "family":
            card["walkaway"] = _r10(total * ratio(0.85, 1.12))
            card["family_delay_days"] = round(rng.uniform(*seeds.get("family_delay_days", (1, 7))), 2)
            card["family_who"] = rng.choice(["my wife", "my husband", "my partner", "my landlord", "my dad"])
            card["hire_by_days"] = round(first_h / 24 + rng.uniform(14, 40), 2)
            if card["walkaway"] < total:  # what they will say after talking it over, if the total is too much
                card["anchor"], card["anchor_kind"] = _r10(card["walkaway"] * rng.uniform(0.9, 0.97)), "budget"
            card["patience"] = seeds.get("patience") or rng.choice([2, 3])
        elif typ == "channel":
            card["walkaway"] = _r10(total * rng.uniform(1.0, 1.15))
            card["reads_channel"] = "sms" if channel == "email" else "email"
            card["patience"] = seeds.get("patience") or 2
            card["notice_day"] = round(rng.uniform(5, 30), 2)   # e.g. finally opens that inbox
            card["hire_by_days"] = round(rng.uniform(6, 25), 2)
        elif typ == "ready":
            card["walkaway"] = _r10(total * rng.uniform(1.0, 1.2))
            card["clarity"] = "clear"
            card["hire_by_days"] = round(first_h / 24 + rng.uniform(1, 5), 2)
        elif typ == "opt_out":
            card["walkaway"] = 0.0
            card["opt_out_style"] = "keyword" if rng.random() < 0.7 else "phrase"
            card["clarity"] = "clear"
        card["tz"] = "America/New_York" if rng.random() < 0.85 else "America/Chicago"
        cards.append(Card(**card))
    return cards


def save(cards, path):
    Path(path).write_text(json.dumps([asdict(c) for c in cards], indent=0))


def load(path):
    return [Card(**d) for d in json.loads(Path(path).read_text())]
