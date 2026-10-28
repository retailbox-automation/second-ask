"""Second Read (v4) data: customer replies with known gold Facts, for the dev and test splits.

    python -m second_read.data sample [--n N] [--split dev|test]   gold sampling only, no calls; prints the mix
    python -m second_read.data dev [--n N]                          200 dev lines (seed "dev-20261028") -> data/second_read/
    python -m second_read.data test [--n N]                         600 test lines (seed "test-20261028"); refuses unless
                                                                    data/second_read/FREEZE.json is committed and its
                                                                    "dev_sha256" equals the live dev.jsonl

How a line is made:
1. Code (seeded) picks an estimate card from tournament/buyers.json, today's date (2026-11-02 + 1..10 days, kept before
   the quoted slot), and the gold Facts for the line's stratum (S1 one plain fact, S2 two or three compatible facts, S3
   one or two facts said the way people talk, S4 Spanish or Spanglish). About 8 % of lines are stop lines (only
   stop=true), half of them worded without the word "stop".
2. A non-NVIDIA writer (DeepSeek-V4-Flash, the v2 writer model and parameters) turns those points into one text message.
   It sees the estimate, today's date, the quoted visit and the points in plain words; never a field name, a reader's
   pattern or any reader logic. Each message is checked (JSON, non-empty, at most 400 characters, the stratum's wording
   checks); two tries, then the whole line is drawn again from the next seed (counted in writer.fallback). No templates.
3. Fidelity, descriptive only: an independent non-NVIDIA judge (Qwen3-235B, temperature 0) reads the finished message
   blind and returns Facts; agreement with gold is recorded per field. Lines are never dropped on fidelity.

Calls go through second_ask.tf_client (ledger tag b2). A Token Factory error after the client's retries is never
absorbed: a writer failure fails that line (listed in the summary, exit code 1); a judge failure is recorded on the
line's fidelity and counted in the summary.

Reading conventions every reader of this data should share (also given to the judge, see CONVENTIONS): "before the
14th" means the 13th is the latest day; "the end of next week" is Friday of next week; "a couple of weeks" is 2 weeks,
"about a month" 4, "about two months" 8;
"ready" only when they say they want to book now or ask when the crew can come (a price limit, a deadline or an item
to leave out alone is not; wanting it some weeks from now is wait_weeks, not ready).
"""
import argparse, hashlib, itertools, json, math, random, subprocess, sys, threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from second_ask.tf_client import Client, Ledger, TFError
from second_ask.tournament.buyers import SHOP_NAME, T0, load
from second_ask.tournament.env import V2_WRITER_MODEL, V2_WRITER_PARAMS
from second_ask.tournament.manifest import V2_JUDGE_MODEL

from .contract import FIELDS, SCHEMA, Facts, FactsError, parse
from second_read import prereg as P

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "tournament" / "buyers.json"
DATA = ROOT / "data" / "second_read"
FREEZE = DATA / "FREEZE.json"

SPLITS = {"dev": {"n": P.DEV_N, "seed": "dev-20261028", "spend_limit": 1.20},
          "test": {"n": P.TEST_N, "seed": P.TEST_SEED, "spend_limit": 3.00}}
STRATA = dict(P.STRATA_WEIGHTS)   # pre-registered in second_read/prereg.py (single source)
STOP_SHARE = P.STOP_SHARE
INFEASIBLE_SHARE = 0.15          # ceilings below the package floor
BEFORE_EARLIEST_SHARE = 0.30     # need_by dates before the earliest open slot (when there is room for one)
COMPETITOR_SHARE = 0.30          # ceilings worded as another company's all-in price they would book at
DECIDERS = ("wife", "husband", "partner", "landlord", "HOA", "dad", "mom")
TONES = ("short and to the point", "friendly and a bit chatty", "a little stressed", "polite", "casual, all lowercase")
KINDS = ("ceiling", "drop", "need_by", "wait", "decider", "ready")
CLASHES = ({"need_by", "wait"}, {"ready", "wait"}, {"ready", "decider"})  # facts one reply would not say together
WAIT_WORDS = {1: "about a week", 2: "a couple of weeks", 3: "about three weeks", 4: "about a month",
              5: "about five weeks", 6: "about six weeks", 7: "about seven weeks", 8: "about two months"}
WORKERS = 8
MAX_TRIES = 2                    # writer calls per drawn line
MAX_REDRAWS = 6                  # lines drawn again after two failed tries, before the line fails loudly

WRITER_MODEL, WRITER_PARAMS = V2_WRITER_MODEL, dict(V2_WRITER_PARAMS)
JUDGE_MODEL = V2_JUDGE_MODEL  # the v2 fidelity judge (tools/v2_analysis.py), non-NVIDIA
JUDGE_PARAMS = {"temperature": 0.0, "max_tokens": 300}  # the v2 judge's settings; 300 tokens leave room for item names

CONVENTIONS = ("Work out dates from today's date. A latest day written as \"before the 14th\" means the 13th; \"by the "
               "end of next week\" means Friday of next week. \"A couple of weeks\" is 2 weeks, \"about a month\" is 4, "
               "\"about two months\" is 8. \"ready\" is true only when they say they want to book now or ask when the crew can "
               "come; naming a price limit, a deadline or an item to leave out is not ready, and wanting it some weeks from now "
               "is wait_weeks, not ready.")
WRITER_SYSTEM = ("You write one text message from a homeowner to a home-service company that sent them an estimate. You "
                 "are told what this homeowner wants to get across. Put it in their own words, the way a real person "
                 "texts: 1-3 sentences, no emojis. Get every point across and add nothing else: no other requests, "
                 "questions, amounts, dates, people or line items. Do not mention the visit day the company offered, and "
                 "do not turn a number of weeks into a date or a month. Ask something only if a point says to ask, and do not "
                 "say you want to go ahead or book unless a point says so. "
                 "Answer only with a JSON object: {\"message\": \"...\"}")


class PlatformError(RuntimeError):
    """A Token Factory call failed after the client's own retries."""


# ---- dates and words -------------------------------------------------------------------------------------------------
def ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def long_date(d):
    return f"{d.strftime('%A')}, {d.strftime('%B')} {d.day}"


def money(x):
    return f"${x:,.0f}"


def friday_next_week(today):
    return today - timedelta(days=today.weekday()) + timedelta(days=7 + 4)


# ---- gold sampling (code, seeded) -----------------------------------------------------------------------------------
@dataclass
class Sample:
    id: str
    split: str
    stratum: str
    seed: str
    card: object
    today: date
    quoted: date
    slots: list
    kinds: tuple                 # which facts this line carries ("stop" for a stop line)
    gold: Facts
    words: dict = field(default_factory=dict)   # how code wants each point worded (stratum specifics)
    meta: dict = field(default_factory=dict)

    @property
    def estimate(self):
        return self.card.estimate()

    @property
    def optional(self):
        return [i.name for i in self.estimate.items if i.optional]


def quota(n, weights):
    """Largest-remainder counts that sum to n (ties broken by key order)."""
    counts = {k: math.floor(n * w) for k, w in weights.items()}
    order = sorted(weights, key=lambda k: -(n * weights[k] - counts[k]))
    for k in order[: n - sum(counts.values())]:
        counts[k] += 1
    return counts


def plan(n, seed):
    """[(stratum, stop_style or None)] for n lines: strata by quota, stop lines spread over strata by quota, half of
    them worded without the word "stop"; order shuffled by the seed."""
    strata = quota(n, STRATA)
    stops = quota(round(n * STOP_SHARE), {s: c / n for s, c in strata.items()}) if n else {}
    styles = itertools.cycle(("keyword", "phrase"))
    rows = []
    for s, c in strata.items():
        k = stops.get(s, 0)
        rows += [(s, next(styles)) for _ in range(k)] + [(s, None) for _ in range(c - k)]
    random.Random(f"{seed}:plan").shuffle(rows)
    return rows


def combos(sizes, have_drop):
    out = []
    for k in sizes:
        for c in itertools.combinations(KINDS, k):
            s = set(c)
            if ("drop" in s and not have_drop) or any(x <= s for x in CLASHES):
                continue
            out.append(c)
    return out


def pick_kinds(rng, stratum, have_drop):
    if stratum == "S1":
        return rng.choice(combos([1], have_drop))
    if stratum == "S2":
        return rng.choice(combos([2 if rng.random() < 0.55 else 3], have_drop))
    if stratum == "S3":  # at least one fact that can be said loosely (an amount, an item, a date, a wait)
        loose = {"ceiling", "drop", "need_by", "wait"}
        return rng.choice([c for c in combos([1 if rng.random() < 0.5 else 2], have_drop) if loose & set(c)])
    return rng.choice(combos([1 if rng.random() < 0.5 else 2], have_drop))  # S4


def _round_up(x, step):
    return int(math.ceil(x / step) * step)


def _round_down(x, step):
    return int(math.floor(x / step) * step)


def sample_ceiling(rng, est, dropped, stratum):
    lst, fl = est.list_price(dropped), est.floor(dropped)
    if rng.random() < INFEASIBLE_SHARE:
        c = _round_down(fl * rng.uniform(0.70, 0.95), 10)
        return float(c if 0 < c < fl else _round_down(fl - 1, 10)), False
    for step in ((50, 10) if stratum == "S3" else (10,)):
        lo, hi = _round_up(fl, step), _round_down(lst - 1, step)
        if lo <= hi:
            return float(rng.randrange(lo, hi + 1, step)), True
    return float(fl), True  # no round figure between floor and list: the floor itself


def sample_need_by(rng, today, slots, stratum, words):
    earliest = slots[0]
    if stratum == "S3" and rng.random() < 0.25:
        d = friday_next_week(today)
        words["need_by"] = "by the end of next week"
        return d, d < earliest
    if (earliest - today).days >= 2 and rng.random() < BEFORE_EARLIEST_SHARE:
        d = today + timedelta(days=rng.randint(1, (earliest - today).days - 1))
    else:
        lo = max((earliest - today).days, 1)
        d = today + timedelta(days=rng.randint(lo, max(lo, 16)))
    if stratum == "S3":
        forms = [f"by the {ordinal(d.day)}", f"before the {ordinal((d + timedelta(days=1)).day)}"]
        if (d - today).days <= 6:
            forms.append(f"by {d.strftime('%A')}")
        words["need_by"] = rng.choice(forms)
    return d, d < earliest


def sample_line(cards, split, seed, i, stratum, stop_style, redraw=0):
    """One line's card, dates and gold Facts. Deterministic in (seed, i, redraw)."""
    line_seed = f"{seed}:{i}:{redraw}"
    rng = random.Random(line_seed)
    card = rng.choice(cards)
    est = card.estimate()
    today = (T0 + timedelta(days=rng.randint(1, min(10, int(card.quoted_slot_day) - 1)))).date()
    quoted = est.default_slot.start.date()
    slots = [s.start.date() for s in card.open_slots() if s.start.date() > today]
    optional = [it.name for it in est.items if it.optional]
    words = {"tone": rng.choice(TONES)}
    meta = {}
    if stratum == "S4":
        words["language"] = rng.choice(("spanish", "spanglish"))
    if stop_style:
        words["stop"] = stop_style
        meta["stop_style"] = stop_style
        return Sample(f"{split}-{i + 1:04d}", split, stratum, line_seed, card, today, quoted, slots, ("stop",),
                      Facts(stop=True), words, meta)
    kinds = pick_kinds(rng, stratum, bool(optional))
    f = {}
    dropped = frozenset()
    if "drop" in kinds:
        k = 1 if len(optional) == 1 or rng.random() < 0.75 else 2
        dropped = frozenset(rng.sample(optional, k))
        f["drop_items"] = tuple(sorted(dropped))
    if "ceiling" in kinds:
        f["ceiling_usd"], meta["ceiling_feasible"] = sample_ceiling(rng, est, dropped, stratum)
        meta["package_floor"], meta["package_list"] = est.floor(dropped), est.list_price(dropped)
        words["ceiling"] = "competitor" if rng.random() < COMPETITOR_SHARE else "budget"
    if "need_by" in kinds:
        d, meta["need_by_before_earliest_slot"] = sample_need_by(rng, today, slots, stratum, words)
        f["need_by"] = d.isoformat()
    if "wait" in kinds:
        f["wait_weeks"] = rng.randint(1, 8)
    if "decider" in kinds:
        f["decider"] = rng.choice(DECIDERS)
    if "ready" in kinds:
        f["ready"] = True
    return Sample(f"{split}-{i + 1:04d}", split, stratum, line_seed, card, today, quoted, slots, kinds, Facts(**f),
                  words, meta)


def sample_all(cards, split, n, seed):
    return [sample_line(cards, split, seed, i, s, stop) for i, (s, stop) in enumerate(plan(n, seed))]


# ---- the writer's prompt (no field names, no reader logic) ----------------------------------------------------------
def estimate_text(est):
    items = "; ".join(f"{i.name} {money(i.price)}{' (optional)' if i.optional else ''}" for i in est.items)
    return f"{est.title} ({est.trade}) from {SHOP_NAME}. Line items: {items}. Total {money(est.list_price())}."


def points(s):
    """What the homeowner wants to get across, in plain words, one sentence per point."""
    g, w, out = s.gold, s.words, []
    loose = s.stratum == "S3"
    if "stop" in s.kinds:
        if w["stop"] == "keyword":
            out.append("You want this company to stop texting you. Use the word STOP in the message.")
        else:
            out.append("You want this company to never contact you again. Do not use the word \"stop\".")
        return out
    for name in g.drop_items:
        if loose:
            out.append(f"You do not want or need the optional \"{name}\". Do not write its name: describe it in your own "
                       "everyday words instead.")
        else:
            out.append(f"You do not want or need the optional \"{name}\".")
    if g.ceiling_usd is not None:
        amt = f"{g.ceiling_usd:,.0f} dollars (write it in words or as a plain number, no dollar sign)" if loose \
            else money(g.ceiling_usd)
        what = "for the rest of the job" if g.drop_items else "for this job"
        if w.get("ceiling") == "competitor":
            out.append(f"Another company quoted you {amt} all-in {what}; you would book with whoever does it for that.")
        else:
            out.append(f"The most you will pay {what}: {amt}.")
    if g.need_by:
        d = date.fromisoformat(g.need_by)
        if loose:
            out.append(f"It must be done by {long_date(d)}. Say this date only as \"{w['need_by']}\".")
        else:
            out.append(f"It must be done by {long_date(d)}.")
    if g.wait_weeks:
        n = g.wait_weeks
        when = WAIT_WORDS[n] if loose else f"about {n} week{'s' if n > 1 else ''}"
        out.append(f"You do want the job done, but not until {when} from now.")
    if g.decider:
        who = "your HOA" if g.decider == "HOA" else f"your {g.decider}"
        out.append(f"{who[0].upper() + who[1:]} has to agree before you go ahead.")
    if g.ready:
        out.append("You want to book it now and ask when the crew can come.")
    return out


def style(s):
    if s.stratum == "S1":
        return ("Say it plainly and directly in simple English. If you mention a line item, use its exact name; write any "
                "amount with a dollar sign.")
    if s.stratum == "S2":
        return "Get all of these points into one natural message, in any order, the way people mix several things together."
    if s.stratum == "S3":
        return "Talk the way people really do, loosely, not like a form. Follow the wording notes in the points."
    if s.words.get("language") == "spanish":
        return ("Write the whole message in Spanish, the way a Spanish-speaking homeowner in Florida texts. You may keep "
                "line item names in English or translate them.")
    return "Write it in Spanglish: mix Spanish and English in the same message, the way many bilingual homeowners text."


def writer_messages(s):
    user = (f"Estimate you got: {estimate_text(s.estimate)}\n"
            f"Today is {long_date(s.today)}, {s.today.year}. The company offered to come on {long_date(s.quoted)}.\n"
            "What you want to get across in this one message:\n" + "\n".join(f"- {p}" for p in points(s))
            + f"\nHow to write it: {style(s)} Tone: {s.words['tone']}.")
    return [{"role": "system", "content": WRITER_SYSTEM}, {"role": "user", "content": user}]


def problems(s, msg):
    """Why a written message is not usable for this line ([] when it is)."""
    if not msg:
        return ["empty"]
    if len(msg) > 400:
        return ["too long"]
    low, why = msg.lower(), []
    if s.words.get("stop") == "keyword" and "stop" not in low:
        why.append("no stop word")
    if s.words.get("stop") == "phrase" and "stop" in low:
        why.append("stop word used")
    if s.stratum == "S3":
        why += [f"item name {n!r} used" for n in s.gold.drop_items if n.lower() in low]
        if s.gold.ceiling_usd is not None and "$" in msg:
            why.append("dollar sign used")
    return why


class MeteredClient:
    """Counts cost per model; a TFError after the client's retries becomes a PlatformError (never a silent default)."""

    def __init__(self, client):
        self.client, self.lock = client, threading.Lock()
        self.cost, self.calls = defaultdict(float), Counter()

    def chat(self, model, messages, **params):
        try:
            r = self.client.chat(model, messages, **params)
        except TFError as e:
            raise PlatformError(f"{model}: {e}") from e
        with self.lock:
            self.cost[model] += r.cost
            self.calls[model] += 1
        return r


def _json_message(r):
    try:
        obj = json.loads(r.text.strip().removeprefix("```json").strip("`"))
        return str(obj["message"]).strip()
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return ""


def write(client, s, model=WRITER_MODEL, params=None):
    """(text or None, tries, problems of each failed try)."""
    msgs, notes = writer_messages(s), []
    for t in range(1, MAX_TRIES + 1):
        r = client.chat(model, msgs, response_format={"type": "json_object"}, **(WRITER_PARAMS if params is None else params))
        msg = _json_message(r)
        why = problems(s, msg) if msg else ["not a JSON message"]
        if not why:
            return msg, t, notes
        notes.append(why)
    return None, MAX_TRIES, notes


# ---- fidelity (descriptive) -----------------------------------------------------------------------------------------
_TYPES = {"stop": "true or false", "ceiling_usd": "number or null", "drop_items": "list of strings",
          "need_by": "string or null", "wait_weeks": "integer or null", "decider": "string or null", "ready": "true or false"}


def judge_messages(s, text):
    fields = "\n".join(f"\"{k}\" ({_TYPES[k]}): {SCHEMA['properties'][k]['description']}" for k in FIELDS)
    system = ("You read one text message that a homeowner sent to a home-service company about an estimate. Report what "
              "the message says as a JSON object with exactly these fields:\n" + fields + "\nFor drop_items use the exact "
              "names of the estimate's optional line items. " + CONVENTIONS + " Answer only with the JSON object.")
    user = (f"Estimate: {estimate_text(s.estimate)}\nToday is {long_date(s.today)}, {s.today.year} "
            f"({s.today.isoformat()}). The company offered to come on {long_date(s.quoted)}.\nMessage: {text}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def judge(client, s, text, model=JUDGE_MODEL):
    """(Facts dict or None, error or None)."""
    try:
        r = client.chat(model, judge_messages(s, text), response_format={"type": "json_object"}, **JUDGE_PARAMS)
    except PlatformError as e:
        return None, f"platform: {e}"[:300]
    try:
        obj = json.loads(r.text.strip().removeprefix("```json").strip("`"))
        if isinstance(obj, dict) and isinstance(obj.get("wait_weeks"), float) and obj["wait_weeks"].is_integer():
            obj["wait_weeks"] = int(obj["wait_weeks"])
        return parse(obj, s.optional).to_dict(), None
    except (json.JSONDecodeError, FactsError, AttributeError) as e:
        return None, f"unreadable: {type(e).__name__}: {e}"[:300]


def agreement(gold, read):
    """Per-field agreement: ceiling within 2 %, dates and weeks exact, items as case-insensitive sets, booleans exact,
    decider by presence (the offer only asks whether someone else must agree)."""
    if read is None:
        return False, {k: False for k in FIELDS}

    def ceiling(a, b):
        return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= 0.02 * a)
    f = {"stop": gold["stop"] == read["stop"], "ceiling_usd": ceiling(gold["ceiling_usd"], read["ceiling_usd"]),
         "drop_items": {x.lower() for x in gold["drop_items"]} == {x.lower() for x in read["drop_items"]},
         "need_by": gold["need_by"] == read["need_by"], "wait_weeks": gold["wait_weeks"] == read["wait_weeks"],
         "decider": (gold["decider"] is None) == (read["decider"] is None), "ready": gold["ready"] == read["ready"]}
    return all(f.values()), f


# ---- one line, a whole split ----------------------------------------------------------------------------------------
def make_line(client, cards, s, plan_row, seed, i):
    fallback, failed = 0, []
    while True:
        text, tries, notes = write(client, s)
        if text is not None:
            break
        failed.append({"seed": s.seed, "problems": notes})
        fallback += 1
        if fallback > MAX_REDRAWS:
            raise PlatformError(f"{s.id}: no usable message after {fallback} draws: {failed[-1]}")
        s = sample_line(cards, s.split, seed, i, plan_row[0], plan_row[1], redraw=fallback)
    read, err = judge(client, s, text)
    agree, fields = agreement(s.gold.to_dict(), read)
    return {"id": s.id, "split": s.split, "stratum": s.stratum, "card_id": s.card.id, "today": s.today.isoformat(),
            "weekday": s.today.strftime("%A"), "quoted_slot": s.quoted.isoformat(),
            "open_slots": [d.isoformat() for d in s.slots], "text": text, "gold": s.gold.to_dict(),
            "writer": {"model": WRITER_MODEL, "attempts": tries, "fallback": fallback},
            "fidelity": {"judge_model": JUDGE_MODEL, "facts": read, "agree": agree, "agree_fields": fields,
                         **({"error": err} if err else {})},
            "seed": s.seed, "kinds": list(s.kinds), "meta": {**s.meta, **({"redrawn": failed} if failed else {})}}


class SpendGuard:
    """Stops new work once the ledger's spend on `tag` since this guard started exceeds `limit` dollars."""

    def __init__(self, ledger, tag, limit):
        self.ledger, self.tag, self.limit = ledger, tag, limit
        self.start = ledger.spent()[1].get(tag, 0.0)

    def spent(self):
        return self.ledger.spent()[1].get(self.tag, 0.0) - self.start

    def exceeded(self):
        return self.spent() > self.limit


def run(split, n, seed, client, guard, out_dir, cards, workers=WORKERS, stem=None):
    rows = plan(n, seed)
    samples = [sample_line(cards, split, seed, i, st, stop) for i, (st, stop) in enumerate(rows)]
    errors, skipped = [], []

    def one(i):
        if guard.exceeded():
            skipped.append(samples[i].id)
            return None
        try:
            return make_line(client, cards, samples[i], rows[i], seed, i)
        except PlatformError as e:
            errors.append({"id": samples[i].id, "error": str(e)[:300]})
            print(f"[data] {samples[i].id} FAILED: {str(e)[:200]}", file=sys.stderr)
            return None
    with ThreadPoolExecutor(workers) as ex:
        lines = [x for x in ex.map(one, range(n)) if x is not None]
    lines.sort(key=lambda x: x["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or split
    path = out_dir / f"{stem}.jsonl"
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
    summ = summary(lines, split, n, seed, client, guard, errors, skipped, path)
    (out_dir / f"{stem}-summary.json").write_text(json.dumps(summ, indent=1, ensure_ascii=False) + "\n")
    return lines, summ


def _share(flags):
    return round(sum(flags) / len(flags), 4) if flags else None


def summary(lines, split, n, seed, client, guard, errors, skipped, path):
    by = defaultdict(list)
    for x in lines:
        by[x["stratum"]].append(x)

    def block(xs):
        judged = [x for x in xs if x["fidelity"]["facts"] is not None]
        return {"lines": len(xs), "stop_lines": sum(x["gold"]["stop"] for x in xs),
                "facts": dict(Counter(k for x in xs for k in x["kinds"] if k != "stop")),
                "fidelity_agree": _share([x["fidelity"]["agree"] for x in xs]),
                "fidelity_agree_judged": _share([x["fidelity"]["agree"] for x in judged]),
                "fidelity_by_field": {k: _share([x["fidelity"]["agree_fields"][k] for x in judged]) for k in FIELDS},
                "judge_errors": len(xs) - len(judged)}
    meta = [x["meta"] for x in lines]
    return {"split": split, "seed": seed, "planned": n, "written": len(lines), "failed": errors, "skipped_by_spend_guard":
            skipped, "strata_weights": STRATA, "stop_share": STOP_SHARE, "infeasible_share": INFEASIBLE_SHARE,
            "writer": {"model": WRITER_MODEL, "params": WRITER_PARAMS,
                       "attempts": dict(Counter(x["writer"]["attempts"] for x in lines)),
                       "lines_redrawn": sum(1 for x in lines if x["writer"]["fallback"]),
                       "redraws": sum(x["writer"]["fallback"] for x in lines)},
            "judge": {"model": JUDGE_MODEL, "params": JUDGE_PARAMS},
            "overall": block(lines), "per_stratum": {s: block(by[s]) for s in STRATA},
            "stop_lines_without_word": sum(m.get("stop_style") == "phrase" for m in meta),
            "ceilings": {"feasible": sum(m.get("ceiling_feasible") is True for m in meta),
                         "below_floor": sum(m.get("ceiling_feasible") is False for m in meta)},
            "need_by_before_earliest_slot": sum(m.get("need_by_before_earliest_slot") is True for m in meta),
            "spend_usd": {"by_model": {k: round(v, 5) for k, v in client.cost.items()},
                          "total": round(sum(client.cost.values()), 5), "calls": dict(client.calls),
                          "ledger_b2_delta": round(guard.spent(), 5), "limit": guard.limit},
            "file": path.name, "sha256": sha256(path), "code_sha256": sha256(Path(__file__))}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---- the test split's guard -----------------------------------------------------------------------------------------
def freeze_ok(root=ROOT, data_dir=None):
    """(ok, reason): FREEZE.json exists, is committed (tracked, no staged or unstaged change against HEAD), and its
    "dev_sha256" equals the live dev.jsonl."""
    data_dir = Path(data_dir) if data_dir else root / "data" / "second_read"
    fz, dev = data_dir / "FREEZE.json", data_dir / "dev.jsonl"
    if not fz.exists():
        return False, f"{fz} does not exist"
    rel = str(fz.resolve().relative_to(Path(root).resolve()))
    git = ["git", "-C", str(root)]
    if subprocess.run(git + ["ls-files", "--error-unmatch", rel], capture_output=True).returncode != 0:
        return False, f"{rel} is not committed"
    if subprocess.run(git + ["diff", "--quiet", "HEAD", "--", rel], capture_output=True).returncode != 0:
        return False, f"{rel} differs from the committed version"
    try:
        want = json.loads(fz.read_text())["dev_sha256"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        return False, f"{rel} has no dev_sha256 ({type(e).__name__})"
    if not dev.exists():
        return False, f"{dev} does not exist"
    if sha256(dev) != want:
        return False, f"dev.jsonl sha256 {sha256(dev)} != FREEZE dev_sha256 {want}"
    return True, "ok"


# ---- CLI ------------------------------------------------------------------------------------------------------------
def main(argv=None, client=None, ledger=None, root=ROOT):
    ap = argparse.ArgumentParser(prog="python -m second_read.data")
    ap.add_argument("cmd", choices=("sample", "dev", "test"))
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--split", choices=tuple(SPLITS), default="dev", help="for `sample` only")
    a = ap.parse_args(argv)
    cards = load(CARDS)
    if a.cmd == "sample":
        cfg = SPLITS[a.split]
        ss = sample_all(cards, a.split, a.n or cfg["n"], cfg["seed"])
        print(json.dumps({"strata": Counter(s.stratum for s in ss), "stop": sum(s.gold.stop for s in ss),
                          "kinds": Counter(k for s in ss for k in s.kinds)}, indent=1))
        return 0
    if a.cmd == "test":
        ok, why = freeze_ok(root)
        if not ok:
            print(f"[data] refusing to write the test split: {why}", file=sys.stderr)
            return 2
    cfg = SPLITS[a.cmd]
    n = a.n or cfg["n"]
    ledger = ledger or Ledger()
    metered = MeteredClient(client or Client("b2", ledger=ledger, max_attempts=4))
    guard = SpendGuard(ledger, "b2", cfg["spend_limit"])
    stem = a.cmd if n == cfg["n"] else f"{a.cmd}-smoke{n}"
    lines, summ = run(a.cmd, n, cfg["seed"], metered, guard, root / "data" / "second_read", cards, stem=stem)
    print(json.dumps({k: summ[k] for k in ("written", "planned", "failed", "skipped_by_spend_guard", "writer",
                                            "spend_usd")}, indent=1))
    print(json.dumps({"overall": summ["overall"]["fidelity_agree"],
                      **{s: b["fidelity_agree"] for s, b in summ["per_stratum"].items()}}))
    return 0 if len(lines) == n else 1


if __name__ == "__main__":
    sys.exit(main())
