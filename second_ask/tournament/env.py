"""The tournament environment: one simulated homeowner meets one policy.

Who decides what:
- `react` (code) decides every buyer action from the hidden card: book, reply, stay silent, or walk away.
- A writer only turns that action into words: `LLMWriter` uses a non-NVIDIA model on Token Factory, so Nemotron never
  grades itself; `TemplateWriter` is for dry runs and tests. The writer never sees the walkaway price.
- The policy sees only what a shop would see: the estimate, the messages and when they came, and how often the
  estimate was opened. Rule baselines also get a coarse act ("ready", "objection" or "silence") for free.
"""
import json, random
from dataclasses import dataclass, field
from datetime import timedelta

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
    info_at: float | None = None  # family: when a written breakdown or a smaller option arrived
    decided: bool = False         # family: has told the shop the household's answer
    opted_out_at: float | None = None
    notes: str = ""


@dataclass
class Reaction:
    kind: str                     # book, msg, deliberate, silent, lost
    t: float | None = None
    spec: str | None = None
    reason: str = ""


INFO_KINDS = ("answer", "drop", "concede", "offer_slot", "earlier_slot", "resend")  # restate the job and price in writing


def react(card, st, offer, move, t, replying, rng):
    """The buyer's response to one seller message, decided entirely from the hidden card.

    Buyers are lost to time (after `hire_by_days` they have hired someone else), to a closed conversation, or, while
    negotiating price, to offers that keep missing (`patience` = failed offers they tolerate). Reminders without anything
    new are ignored: they do not lose the job, they just do not win it.
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
        if st.info_at is None and move.kind in INFO_KINDS:
            st.info_at = t  # something in writing to show the household
            return Reaction("deliberate", t + card.family_delay_days, "family_decided")
    elif move.proposes_booking and price_ok:  # price, decided family, ready, channel once seen
        return Reaction("book", t)
    negotiating = typ == "price" or (typ == "family" and st.decided)
    if negotiating and move.proposes_booking:
        st.patience -= 1  # an offer that still misses
        if st.patience <= 0:
            return Reaction("lost", reason="stopped negotiating")
    if move.kind == "ask_why":
        st.clarity = "clear"
    if typ == "family" and st.info_at is not None and not st.decided:
        return Reaction("silent")  # still talking it over
    if typ == "timing_pause" and not (move.kind in ("ask_why", "answer") or move.proposes_booking):
        return Reaction("silent")  # a check-in or reminder before they are ready: nothing to say
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
    if spec == "opt_out":
        return ("Ask them to take you off their list and not contact you again. Do not use the words stop, quit, end, "
                "cancel, unsubscribe, revoke or opt out.")
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


class TemplateWriter:
    """Deterministic buyer text for dry runs and tests (no model)."""
    model = None

    def write(self, card, st, spec, conv, t, rng):
        return instruction(card, st, spec, t, rng).replace("Say ", "").replace("Ask ", "Asks ").rstrip("."), None, False


class LLMWriter:
    """Buyer text from a non-NVIDIA model on Token Factory. Validated; one retry; then the template (counted)."""
    SYSTEM = ("You write one text message from a homeowner to a home-service shop that sent them an estimate. You do not "
              "decide anything: the card says what the homeowner does this turn. Write it the way a real homeowner "
              "texts: 1-3 short sentences, plain, no emojis. Use only numbers that appear on the card. Keep short private "
              "notes for your next message (under 40 words). Answer only with a JSON object: "
              '{"message": "...", "notes": "..."}')

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
                steps.append(Step("buyer", self.model, ok=False, note=str(e)[:200]))
                continue
            try:
                obj = json.loads(r.text.strip().removeprefix("```json").strip("`"))
                msg = str(obj["message"]).strip()
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
                obj, msg = {}, ""
            leak = card.walkaway > 0 and any(s in msg for s in (f"{card.walkaway:,.0f}", f"{card.walkaway:.0f}"))
            ok = 0 < len(msg) <= 400 and not leak
            steps.append(Step.from_reply("buyer", r, ok=ok, note="leak" if leak else ("" if ok else "invalid")))
            if ok:
                st.notes = str(obj.get("notes", ""))[:300]
                return msg, steps, False
        text, _, _ = TemplateWriter().write(card, st, spec, conv, t, rng)
        return text, steps, True


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


def coarse_act(card, spec):
    if spec in ("missed",) or (card.type == "ready" and spec in ("first", "again")):
        return "ready"
    return "objection"


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
    owner, moves, end, violations, unsure, buyer_steps, fallbacks = OwnerTapStub(), 0, None, 0, 0, [], 0
    booked = False
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
            if spec == "family_decided":
                st.decided = True
                if conv.offer.price <= card.walkaway:
                    booked, end = True, "booked"
                    conv.events.append(Event(at(t), "customer", est.channel, "We talked it over. Let's book it."))
                    break
                spec = "family_no"
            if card.type == "opt_out" and spec == "first":
                st.opted_out_at = t
                if card.opt_out_style == "keyword":
                    text = rng.choice(OPT_OUT_KEYWORDS)
                else:
                    text, steps, fb = writer.write(card, st, "opt_out", conv, t, rng)
                    buyer_steps += steps or []
                    fallbacks += fb
            else:
                text, steps, fb = writer.write(card, st, spec, conv, t, rng)
                buyer_steps += steps or []
                fallbacks += fb
            reply_channel = est.channel if card.reads_channel == "both" else card.reads_channel
            conv.events.append(Event(at(t), "customer", reply_channel, text))
            now, hint, replying = t + SELLER_LATENCY_DAYS, ("objection" if card.type == "opt_out" else coarse_act(card, spec)), True
        else:
            if next_check is None:
                end = "nothing scheduled"
                break
            now, hint, replying, next_check = next_check, "silence", False, None
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
        unsure += any(not f.startswith("delayed to") for f in decision.flags)  # sent despite a flag
        if not apply_decision(conv, decision, owner):
            end = "owner declined"
            break
        moves += 1
        sent = day(decision.send_at)
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
    return Outcome(card.id, card.type, card.trade, policy.name, repeat, booked,
                   conv.offer.price if booked else None, sorted(conv.offer.dropped) if booked else [], end, moves,
                   round(sum(s.cost for s in seller), 6), sum(1 for s in seller if s.model),
                   round(sum(s.cost for s in buyer_steps), 6), sum(1 for s in buyer_steps if s.model), fallbacks,
                   violations, unsure,
                   [{"day": round(day(e.at), 3), "who": e.who, "channel": e.channel, "text": e.text, "move": e.move_id}
                    for e in conv.events],
                   [s.__dict__ for s in seller] + [dict(s.__dict__, step="buyer") for s in buyer_steps])
