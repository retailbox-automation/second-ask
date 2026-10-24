"""The tournament environment: one simulated homeowner meets one policy.

Who decides what:
- `react` (code) decides every buyer action from the hidden card: book, reply, stay silent, or walk away.
- A writer only turns that action into words: `LLMWriter` uses a non-NVIDIA model on Token Factory, so Nemotron never
  grades itself; `TemplateWriter` is for dry runs and tests. The writer never sees the walkaway price.
- The policy sees only what a shop would see: the estimate, the messages and when they came, and how often the
  estimate was opened. The two text-blind rule baselines also get a coarse act ("ready", "objection" or "silence").
- An opt-out buyer's words come from code, never from the writer: a keyword (OPT_OUT_KEYWORDS) or a phrase from the
  published OPT_OUT_PHRASES, picked by the buyer's id. So every policy's outcome on them is fixed by the cards.
- Time only moves forward: the shop decides no earlier than its own last scheduled send, a customer message is never
  stamped before it, and a household that decides books the offer that had been delivered by then.
"""
import json, random, re
from dataclasses import dataclass, field
from datetime import timedelta

from .. import models as M
from ..agent import Conversation, Event, OwnerTapStub, apply_decision
from ..compliance import Recipient, opt_out_status
from ..tf_client import TFError
from ..trace import Step, Trace
from .buyers import T0

MAX_MOVES = 5                    # seller messages per conversation
HORIZON_DAYS = 75                # after this the estimate is dead
LATE_WINDOW_DAYS = 21            # a ready buyer nobody contacts hires someone else after this many days (buyers.py)
SELLER_LATENCY_DAYS = 10 / 1440  # the shop answers ten minutes after a customer message (then quiet hours apply)
OPT_OUT_KEYWORDS = ["STOP", "Stop", "stop please", "Unsubscribe", "STOP!!"]
OPT_OUT_PHRASES = ["Please take me off your list.", "Take me off your list and don't contact me again.",
                   "Please remove my number.", "Please don\u2019t text me again.", "No more messages, thanks.",
                   "Please don't reach out again."]  # every one is caught by compliance.opt_out_status (a test checks)
BUYER_PARAMS = {  # buyer-writer parameters per candidate (S1: gpt-oss cannot turn reasoning off)
    M.QWEN3_30B: {"temperature": 0.8, "top_p": 0.9, "max_tokens": 300},
    M.DEEPSEEK_V4_FLASH: {"temperature": 0.8, "top_p": 0.9, "max_tokens": 300, "reasoning_effort": "none"},
    M.GPT_OSS_120B: {"temperature": 0.8, "top_p": 0.9, "max_tokens": 1500, "reasoning_effort": "low"},
}
PAUSE_REASONS = ["money comes in later this month", "we are traveling", "after the holidays", "waiting on the insurance"]
SLOT_REASONS = {"HVAC": "the system is out and the house is hot", "plumbing": "we have no hot water",
                "roofing": "rain is in the forecast", "electrical": "breakers keep tripping"}


def day(dt):
    return (dt - T0).total_seconds() / 86400


def at(d):
    return T0 + timedelta(days=d)


@dataclass
class BuyerState:
    patience: int
    clarity: str
    seen: bool
    ready_like: bool = False      # a channel buyer who has now seen the estimate
    decided: bool = False         # family: has told the shop the household's answer
    opted_out_at: float | None = None
    notes: str = ""
    turns: list = field(default_factory=list)  # v2 writer: one record per message (facts, text, fallback)


@dataclass
class Reaction:
    kind: str                     # book, msg, deliberate, silent, lost
    t: float | None = None
    spec: str | None = None
    reason: str = ""


def react(card, st, offer, move, t, replying, rng):
    """The buyer's response to one seller message, decided entirely from the hidden card.

    Buyers are lost to time (after `hire_by_days` they have hired someone else), to a closed conversation, or, while
    negotiating price, to offers that keep missing (`patience` = failed offers they tolerate). Reminders without anything
    new are ignored: they do not lose the job, they just do not win it. A household that has to talk it over decides
    `family_delay_days` after its first message whatever the shop sends (run_one schedules it); until then nothing books.
    """
    if st.opted_out_at is not None:
        return Reaction("silent")
    if card.hire_by_days is not None and t > card.hire_by_days:
        return Reaction("lost", reason="hired someone else first")
    sees = st.seen or card.reads_channel in ("both", move.channel) or (card.notice_day is not None and t >= card.notice_day)
    if not sees:
        return Reaction("silent")  # sent where this buyer does not look (yet)
    first_seen, st.seen = not st.seen, True
    if move.kind == "close_out":
        return Reaction("lost", reason="the shop closed the conversation")
    price_ok = offer.price <= card.walkaway
    delay = rng.uniform(1, 24) / 24
    typ = card.type
    if typ == "channel" and first_seen:
        if move.proposes_booking and price_ok:
            return Reaction("book", t)
        st.ready_like = True
        return Reaction("msg", t + delay, "missed")
    if typ == "timing_pause":
        if t >= card.ready_after_days and price_ok:
            return Reaction("book", t)  # ready now: any touch gets a yes
    elif typ == "timing_slot":
        if move.proposes_booking and day(offer.slot.start) <= card.need_by_days and price_ok:
            return Reaction("book", t)
    elif typ == "family" and not st.decided:
        if move.kind == "ask_why" and replying:
            return Reaction("msg", t + delay, "again")
        return Reaction("silent")  # still talking it over; a discount now only lowers the price they will book at
    elif move.proposes_booking and price_ok:  # price, decided family, ready, channel once seen
        return Reaction("book", t)
    negotiating = typ == "price" or (typ == "family" and st.decided)
    if negotiating and move.proposes_booking:
        st.patience -= 1  # an offer that still misses
        if st.patience <= 0:
            return Reaction("lost", reason="stopped negotiating")
    if move.kind == "ask_why":
        st.clarity = "clear"
    wants_action = typ in ("ready", "timing_slot") or st.ready_like
    if move.kind == "checkin" and not wants_action:
        return Reaction("silent")  # "I will check back in N days": they wait
    if typ == "timing_pause" and not (move.kind in ("ask_why", "answer") or move.proposes_booking):
        return Reaction("silent")  # a reminder before they are ready: nothing to say
    if move.kind in ("reminder", "checkin") and not replying:
        return Reaction("silent")  # unprompted reminders are ignored
    return Reaction("msg", t + delay, "again")


def instruction(card, st, spec, t, rng):
    """What the buyer says this turn, in plain words for the writer. Only the card's public facts and the anchor."""
    typ, clear = card.type, st.clarity == "clear"
    anchor = f"${card.anchor:,.0f}" if card.anchor else None
    weeks = max(1, round(((card.ready_after_days or t) - t) / 7))
    if spec == "missed":
        return f"Say sorry, you only just saw this (you mostly use {card.reads_channel}). It looks fine; ask when they can come."
    if spec == "family_no":
        dropped = [n for n, p, o in card.items if o]
        if dropped and card.walkaway >= sum(p for n, p, o in card.items if not o):
            return f"Say you talked it over with {card.family_who}, who thinks the {dropped[-1]} is not needed; otherwise you are fine with it."
        return f"Say you talked it over with {card.family_who} and the total is more than you want to spend; you could do about {anchor}."
    first = spec == "first"
    if typ == "price":
        if not clear:
            return "Say it is more than you expected. Do not give a number." if first else "Say it is still more than you hoped. No number."
        return (f"Say the total is more than you can do. Mention your {card.anchor_kind}: about {anchor}." if first
                else f"Say it is still more than you can do; you could do about {anchor}.")
    if typ == "timing_pause":
        reason = PAUSE_REASONS[int(card.id[1:]) % len(PAUSE_REASONS)]
        if not clear:
            return "Say you will think about it and get back to them. Do not say why or when."
        return (f"Say you want to do it, but not for about {weeks} week(s) because {reason}." if first
                else f"Say you are still not ready; maybe in about {weeks} week(s).")
    if typ == "timing_slot":
        reason = SLOT_REASONS.get(card.trade, "it is urgent")
        deadline = f"{at(card.need_by_days):%A}"
        if not clear:
            return "Say the timing does not really work for you. Do not say why." if first else "Say the timing is still the problem."
        return (f"Say their quoted slot ({at(card.quoted_slot_day):%A %b %d}) is too late: you need it done by {deadline} "
                f"because {reason}, and someone else can come sooner." if first else f"Say you really need it done by {deadline}.")
    if typ == "family":
        if st.decided:
            return f"Say it is still more than you want to spend; about {anchor} would work."
        if not clear:
            return "Say you need to think it over." if first else "Say you are still thinking it over."
        return f"Say {card.family_who} needs to look at it with you before you decide." if first else "Say you are still talking it over."
    if typ in ("ready", "channel"):
        return "Say it looks good and ask when they can come." if first else "Ask again when they can come."
    return "Say you are still thinking."


PAUSE_REASON_CODES = {"money comes in later this month": "money_arrives_later_this_month", "we are traveling": "travelling",
                      "after the holidays": "after_the_holidays", "waiting on the insurance": "waiting_on_insurance"}
SLOT_REASON_CODES = {"HVAC": "system_out_house_hot", "plumbing": "no_hot_water", "roofing": "rain_in_forecast",
                     "electrical": "breakers_keep_tripping"}


def intent(card, st, spec, t):
    """The facts instruction() gives the v1 writer for this turn, as data rather than the simulator's English sentences
    (v2 wording test). Same branches, same facts; a test checks that on every card, spec and state."""
    typ, clear, first = card.type, st.clarity == "clear", spec == "first"
    weeks = max(1, round(((card.ready_after_days or t) - t) / 7))
    if spec == "missed":
        return {"act": "saw_it_only_now", "usually_checks": card.reads_channel, "estimate": "fine", "wants": "a visit time"}
    if spec == "family_no":
        dropped = [n for n, p, o in card.items if o]
        if dropped and card.walkaway >= sum(p for n, p, o in card.items if not o):
            return {"act": "household_view", "discussed_with": card.family_who, "unnecessary_item": dropped[-1],
                    "rest_of_estimate": "fine"}
        return {"act": "household_view", "discussed_with": card.family_who, "total": "more than they want to spend",
                "could_pay": card.anchor}
    if typ == "price":
        if not clear:
            return {"act": "cost_concern", "repeat": not first, "give_a_figure": False}
        return ({"act": "cost_concern", "repeat": False, "could_pay": card.anchor, "figure_from": card.anchor_kind} if first
                else {"act": "cost_concern", "repeat": True, "could_pay": card.anchor})
    if typ == "timing_pause":
        if not clear:
            return {"act": "will_decide_later", "give_reason_or_time": False}
        reason = PAUSE_REASON_CODES[PAUSE_REASONS[int(card.id[1:]) % len(PAUSE_REASONS)]]
        return ({"act": "wants_it_later", "wait_weeks_about": weeks, "because": reason} if first
                else {"act": "still_not_ready", "wait_weeks_about": weeks})
    if typ == "timing_slot":
        deadline = f"{at(card.need_by_days):%A}"
        if not clear:
            return {"act": "schedule_problem", "give_reason": False} if first else {"act": "schedule_problem", "repeat": True}
        return ({"act": "needs_it_sooner", "quoted_visit": f"{at(card.quoted_slot_day):%A %b %d}", "done_by": deadline,
                 "because": SLOT_REASON_CODES.get(card.trade, "urgent"), "someone_else_can_come_sooner": True} if first
                else {"act": "needs_it_sooner", "repeat": True, "done_by": deadline})
    if typ == "family":
        if st.decided:
            return {"act": "household_view", "total": "still more than they want to spend", "could_pay": card.anchor}
        if not clear:
            return {"act": "needs_to_think", "repeat": not first}
        return ({"act": "household_must_review", "who": card.family_who} if first
                else {"act": "household_still_discussing"})
    if typ in ("ready", "channel"):
        return {"act": "go_ahead", "wants": "a visit time", "repeat": not first}
    return {"act": "still_thinking"}


class TemplateWriter:
    """Deterministic buyer text for dry runs and tests (no model)."""
    model = None

    def write(self, card, st, spec, conv, t, rng):
        return instruction(card, st, spec, t, rng).replace("Say ", "").replace("Ask ", "Asks ").rstrip("."), None, False


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text):
    """Every number written in the text, with or without $ ("11,000" is 11000, never 1000)."""
    out = []
    for tok in _NUMBER.findall(text or ""):
        try:
            out.append(float(tok.replace(",", "").rstrip(".")))
        except ValueError:
            continue
    return out


def leaks_walkaway(card, est, msg):
    """The message states the hidden walkaway. A walkaway equal to a number the buyer can see (a line item, a package
    total, their own anchor) cannot be told apart from quoting the card, so it never counts as a leak."""
    if card.walkaway <= 0:
        return False
    opt = [i.name for i in est.items if i.optional]
    visible = {i.price for i in est.items} | {est.list_price(frozenset(opt[:k])) for k in range(len(opt) + 1)} \
        | {est.list_price(frozenset([o])) for o in opt} | ({card.anchor} if card.anchor else set())
    if any(abs(v - card.walkaway) < 0.5 for v in visible):
        return False
    return any(abs(v - card.walkaway) < 0.5 for v in numbers_in(msg))


class LLMWriter:
    """Buyer text from a non-NVIDIA model on Token Factory. Validated; one retry; then the template (counted)."""
    SYSTEM = ("You write one text message from a homeowner to a home-service shop that sent them an estimate. You do not "
              "decide anything: the card says what the homeowner does this turn. Write it the way a real homeowner "
              "texts: 1-3 short sentences, plain, no emojis. Say only what 'This turn' says: do not ask for a visit, a "
              "call, a time, details or documents, and do not accept or refuse anything, unless 'This turn' says so. "
              "Use only numbers that appear on the card. Keep short private notes for your next message (under 40 "
              'words). Answer only with a JSON object: {"message": "...", "notes": "..."}')

    def __init__(self, client, model, params):
        self.client, self.model, self.params = client, model, params

    def write(self, card, st, spec, conv, t, rng):
        est = conv.estimate
        history = "\n".join(f"{'Shop' if e.who == 'shop' else 'You'}: {e.text}" for e in conv.events[-6:]) or "(none yet)"
        user = (f"Job: {est.title} ({est.trade}); quoted total ${est.list_price():,.0f}; line items: "
                + "; ".join(f"{i.name} ${i.price:,.0f}{' (optional)' if i.optional else ''}" for i in est.items)
                + f".\nConversation so far:\n{history}\nYour notes from before: {st.notes or '(none)'}\n"
                f"How you tend to talk: {', '.join(card.style) or 'plainly'}.\nThis turn: {instruction(card, st, spec, t, rng)}")
        messages = [{"role": "system", "content": self.SYSTEM}, {"role": "user", "content": user}]
        steps = []
        for _ in range(2):
            try:
                r = self.client.chat(self.model, messages, response_format={"type": "json_object"}, **self.params)
            except TFError as e:
                steps.append(Step.platform_failure("buyer", self.model, e))
                continue
            try:
                obj = json.loads(r.text.strip().removeprefix("```json").strip("`"))
                msg = str(obj["message"]).strip()
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
                obj, msg = {}, ""
            leak = leaks_walkaway(card, est, msg)
            ok = 0 < len(msg) <= 400 and not leak
            steps.append(Step.from_reply("buyer", r, ok=ok, note="leak" if leak else ("" if ok else "invalid")))
            if ok:
                st.notes = str(obj.get("notes", ""))[:300]
                return msg, steps, False
        text, _, _ = TemplateWriter().write(card, st, spec, conv, t, rng)
        return text, steps, True


V2_WRITER = "v2-free"
V2_WRITER_MODEL = M.DEEPSEEK_V4_FLASH
V2_WRITER_PARAMS = {"temperature": 1.0, "top_p": 0.95, "max_tokens": 300, "reasoning_effort": "none"}
V2_SYSTEM = ("You write one text message from a homeowner to a home-service company that sent them an estimate. The facts "
             "this homeowner wants to get across in this message are given as a small JSON object. Put them in your own "
             "words, the way this particular person would text: 1-3 sentences, natural and specific to them, no emojis. Get "
             "every fact across and add nothing the facts do not say: no new requests, no new numbers, no agreeing or "
             "refusing. Write figures and dates however this person would. Keep short private notes for your next message "
             '(under 40 words). Answer only with a JSON object: {"message": "...", "notes": "..."}')


class FreeWordingWriter:
    """v2 buyer text: a non-NVIDIA model writes the turn's facts (intent) in its own words. It never sees the simulator's
    instruction sentences or any policy's rules. Validated like v1 (length, walkaway leak); two tries, then the v1
    template, counted. Every message is logged on the buyer state (facts, text, fallback) for the wording checks."""
    name = V2_WRITER

    def __init__(self, client, model=V2_WRITER_MODEL, params=None):
        self.client, self.model, self.params = client, model, dict(V2_WRITER_PARAMS if params is None else params)

    def prompt(self, card, st, spec, conv, t):
        est = conv.estimate
        history = "\n".join(f"{'Company' if e.who == 'shop' else 'You'}: {e.text}" for e in conv.events[-6:]) or "(none yet)"
        facts = intent(card, st, spec, t)
        user = (f"Job: {est.title} ({est.trade}); estimate total ${est.list_price():,.0f}; line items: "
                + "; ".join(f"{i.name} ${i.price:,.0f}{' (optional)' if i.optional else ''}" for i in est.items)
                + f".\nMessages so far:\n{history}\nYour notes from before: {st.notes or '(none)'}\n"
                f"How this person tends to talk: {', '.join(card.style) or 'plainly'}.\n"
                f"Facts for this message: {json.dumps(facts)}")
        return [{"role": "system", "content": V2_SYSTEM}, {"role": "user", "content": user}], facts

    def write(self, card, st, spec, conv, t, rng):
        messages, facts = self.prompt(card, st, spec, conv, t)
        steps = []
        for _ in range(2):
            try:
                r = self.client.chat(self.model, messages, response_format={"type": "json_object"}, **self.params)
            except TFError as e:
                steps.append(Step.platform_failure("buyer", self.model, e))
                continue
            try:
                obj = json.loads(r.text.strip().removeprefix("```json").strip("`"))
                msg = str(obj["message"]).strip()
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
                obj, msg = {}, ""
            leak = leaks_walkaway(card, conv.estimate, msg)
            ok = 0 < len(msg) <= 400 and not leak
            steps.append(Step.from_reply("buyer", r, ok=ok, note="leak" if leak else ("" if ok else "invalid")))
            if ok:
                st.notes = str(obj.get("notes", ""))[:300]
                st.turns.append({"t": round(t, 4), "spec": spec, "clarity": st.clarity, "decided": st.decided,
                                 "facts": facts, "text": msg, "fallback": False})
                return msg, steps, False
        text, _, _ = TemplateWriter().write(card, st, spec, conv, t, rng)
        st.turns.append({"t": round(t, 4), "spec": spec, "clarity": st.clarity, "decided": st.decided, "facts": facts,
                         "text": text, "fallback": True})
        return text, steps, True


WRITERS = {"v1": LLMWriter, V2_WRITER: FreeWordingWriter}


@dataclass
class Outcome:
    buyer: str
    type: str
    trade: str
    policy: str
    repeat: int
    booked: bool
    price: float | None
    dropped: list
    end: str
    moves: int
    seller_cost: float
    seller_calls: int
    buyer_cost: float
    buyer_calls: int
    writer_fallbacks: int
    violations: int               # messages sent after the buyer asked to stop
    unsure: int                   # decisions the agent flagged (low confidence, fallbacks, template, ambiguous opt-out)
    transcript: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    flags: list = field(default_factory=list)   # what the agent flagged on each message it sent (the "unsure" list)
    # Integrity counters (manifest: integrity). A clean conversation has 0 platform failures and 0 off-arm calls.
    off_arm_calls: int = 0        # a call to a model outside the policy's arm for that step, or answered by another model
    label_fallback_calls: int = 0  # label calls answered by the fallback label model (part of the policy, reported)
    platform_failures: int = 0    # Token Factory failed after the client's retries (TFError) in any step
    chronology_errors: int = 0    # transcript events out of time order (must be 0)
    buyer_turns: list = field(default_factory=list)  # v2 writer only: facts and text of every buyer message


def coarse_act(card, spec):
    if spec in ("missed",) or (card.type == "ready" and spec in ("first", "again")):
        return "ready"
    return "objection"


def opt_out_phrase(card):
    """The published phrase an opt-out buyer of style "phrase" writes: chosen by id, never by a model."""
    return OPT_OUT_PHRASES[int(card.id[1:]) % len(OPT_OUT_PHRASES)]


def integrity(steps, arm, buyer_model):
    """(off_arm_calls, label_fallback_calls, platform_failures) over a conversation's steps (Step objects)."""
    off = fallback = failed = 0
    for s in steps:
        if s.platform_error:
            failed += 1
            continue
        if s.served_model and s.served_model != s.model:
            off += 1
        elif s.step == "buyer":
            off += s.model != buyer_model
        elif s.step == "classify" and s.model in arm.get("label_fallback", ()):
            fallback += 1
        elif s.model not in arm.get(s.step, ()):
            off += 1
    return off, fallback, failed


def run_one(card, policy, writer, repeat):
    """Simulate one conversation. The environment's randomness (reply delays, views) is seeded by the buyer only, so
    every policy and every repeat meets the same buyer; repeats differ only through the models' sampling."""
    rng = random.Random(card.id)
    est = card.estimate()
    conv = Conversation(est, Recipient(card.tz, card.state), card.open_slots(), trace=Trace(sink=lambda _: None))
    st = BuyerState(card.patience, card.clarity, seen=card.reads_channel == "both")
    conv.views = 0 if card.type == "channel" else 1 + rng.randrange(3)
    pending = [] if card.type == "channel" else [(card.first_reply_hours / 24, "first")]
    next_check = 3.0 if card.type == "channel" else None
    owner, moves, end, violations, unsure, buyer_steps, fallbacks, flag_log = OwnerTapStub(), 0, None, 0, 0, [], 0, []
    booked = False
    delivered = [(float("-inf"), conv.offer)]  # (send day, offer in force from then on)
    last_send = 0.0                            # day of the shop's latest scheduled message
    while end is None:
        pending.sort()
        if pending and (next_check is None or pending[0][0] <= next_check):
            t, spec = pending.pop(0)
            if t > HORIZON_DAYS:
                end = "horizon"
                break
            if card.hire_by_days is not None and t > card.hire_by_days:
                end = "hired someone else first"
                break
            in_force = next(o for d, o in reversed(delivered) if d <= t)
            t = max(t, last_send)  # a message the shop has queued goes out first; the customer's is stamped after it
            if spec == "family_decided":
                st.decided = True
                if in_force.price <= card.walkaway:  # the household books what it had been offered when it decided
                    booked, end = True, "booked"
                    conv.offer = in_force
                    conv.events.append(Event(at(t), "customer", est.channel, "We talked it over. Let's book it."))
                    break
                spec = "family_no"
            if card.type == "opt_out" and spec == "first":
                st.opted_out_at = t
                text = rng.choice(OPT_OUT_KEYWORDS) if card.opt_out_style == "keyword" else opt_out_phrase(card)
            else:
                text, steps, fb = writer.write(card, st, spec, conv, t, rng)
                buyer_steps += steps or []
                fallbacks += fb
            reply_channel = est.channel if card.reads_channel == "both" else card.reads_channel
            conv.events.append(Event(at(t), "customer", reply_channel, text))
            if card.type == "family" and spec == "first":
                pending.append((t + card.family_delay_days, "family_decided"))  # the household decides on its own clock
            now, hint, replying = t + SELLER_LATENCY_DAYS, ("objection" if card.type == "opt_out" else coarse_act(card, spec)), True
        else:
            if next_check is None:
                end = "nothing scheduled"
                break
            now, hint, replying, next_check = next_check, "silence", False, None
        now = max(now, last_send)  # the shop does not decide before its own queued message has gone out
        if now > HORIZON_DAYS:
            end = "horizon"
            break
        if moves >= MAX_MOVES:
            end = "move cap"
            break
        last = conv.last_customer_text()
        if last and opt_out_status(last) == "opt_out":  # shared code layer: keyword opt-outs stop every policy
            conv.recipient.opted_out = True
        decision = policy.act(conv, at(now), hint)
        if decision.move is None or decision.message is None:
            end = "policy stopped: " + (decision.stop_reason or "")
            break
        notes = [f for f in decision.flags if not f.startswith("delayed to")]
        unsure += bool(notes)  # sent despite a flag
        if notes:
            flag_log.append({"day": round(day(decision.send_at), 3), "move": decision.move.id, "flags": notes})
        if not apply_decision(conv, decision, owner):
            end = "owner declined"
            break
        moves += 1
        sent = day(decision.send_at)
        last_send = max(last_send, sent)
        delivered.append((sent, conv.offer))
        if st.opted_out_at is not None:
            violations += 1
        r = react(card, st, conv.offer, decision.move, sent, replying, rng)
        if r.kind == "book":
            booked, end = True, "booked"
        elif r.kind == "lost":
            end = r.reason
        elif r.kind in ("msg", "deliberate"):
            pending.append((r.t, r.spec))
        if end is None:
            next_check = sent + (decision.move.checkin_days or policy.next_check_days)
    seller = conv.trace.steps
    arm = policy.arm() if hasattr(policy, "arm") else {}
    off_arm, label_fb, failed = integrity(seller + buyer_steps, arm, getattr(writer, "model", None))
    days = [day(e.at) for e in conv.events]
    return Outcome(card.id, card.type, card.trade, policy.name, repeat, booked,
                   conv.offer.price if booked else None, sorted(conv.offer.dropped) if booked else [], end, moves,
                   round(sum(s.cost for s in seller), 6), sum(1 for s in seller if s.model),
                   round(sum(s.cost for s in buyer_steps), 6), sum(1 for s in buyer_steps if s.model), fallbacks,
                   violations, unsure,
                   [{"day": round(day(e.at), 3), "who": e.who, "channel": e.channel, "text": e.text, "move": e.move_id}
                    for e in conv.events],
                   [s.__dict__ for s in seller] + [dict(s.__dict__, step="buyer") for s in buyer_steps], flag_log,
                   off_arm, label_fb, failed, sum(b < a - 1e-9 for a, b in zip(days, days[1:])), st.turns)
