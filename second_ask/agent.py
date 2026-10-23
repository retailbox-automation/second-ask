"""The Second Ask agent: label -> menu -> choose -> owner tap -> send; then a Super tool loop books the job.

All state lives in a Conversation (one per customer, one per demo visitor); the agent object holds only the client and
the configuration, so many conversations can run at once.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime

from . import models as M
from .chooser import Choice, choose, default_move
from .classifier import Label, classify, render_state
from .compliance import Recipient, gate, opt_out_status
from .menu import build_menu, initial_offer, template_message, validate
from .tf_client import TFError, tool_args
from .trace import Step, Trace


@dataclass
class Event:
    at: datetime
    who: str          # "shop" or "customer"
    channel: str
    text: str
    move_id: str | None = None


@dataclass
class Conversation:
    estimate: object
    recipient: Recipient
    open_slots: list                       # this shop's open crew slots (Slot)
    events: list = field(default_factory=list)
    views: int = 0                         # times the customer opened the estimate
    offer: object = None
    sent_times: list = field(default_factory=list)
    status: str = "open"                   # open, booked, closed, opted_out
    jobs: list = field(default_factory=list)
    trace: Trace = field(default_factory=Trace)

    def __post_init__(self):
        if self.offer is None:
            self.offer = initial_offer(self.estimate)

    def last_customer_text(self):
        for e in reversed(self.events):
            if e.who == "customer":
                return e.text
            if e.who == "shop":
                return None
        return None

    def slots_after(self, now):
        return [s for s in self.open_slots if s.start > now]


@dataclass
class Decision:
    move: object | None
    message: str | None            # final text with disclosure, or None when nothing is sent
    send_at: datetime | None
    needs_owner: bool
    effect: dict                   # price before/after, change, margin after: shown first in the UI
    label: Label | None
    choice: Choice | None
    menu: list
    flags: list                    # where the agent was unsure or overridden
    stop_reason: str | None = None

    def headline(self):
        if self.move is None:
            return f"No message: {self.stop_reason}"
        e = self.effect
        return (f"{self.move.id}: {self.move.description} | ${e['price_after']:,.0f} "
                f"({e['change']:+,.0f} vs current), margin ${e['margin_after']:,.0f}"
                + (" | owner tap needed" if self.needs_owner else ""))


def _stop(reason, label=None, flags=()):
    return Decision(None, None, None, False, {}, label, None, [], list(flags), reason)


class OwnerTapStub:
    """Stands in for the owner's one-tap approval of a concession. Records every request; approves by default."""

    def __init__(self, approve=True):
        self.approve, self.requests = approve, []

    def __call__(self, conv, move):
        validate(move, conv.estimate)  # the floor is checked again at the moment of approval
        self.requests.append((conv.estimate.id, move.id, move.price))
        return self.approve


@dataclass(frozen=True)
class AgentConfig:
    classifier_model: str = M.SUPER      # S2 rule: Lightning failed the 8/10 bar, so the default label model is Super
    classifier_method: str = "tool"
    chooser: object = "super_nothink"    # a chooser.CONFIGS name or a (model, params) pair
    booker_model: str = M.SUPER
    shop_state: str = "FL"


class Agent:
    def __init__(self, client, config=AgentConfig()):
        self.client, self.cfg = client, config

    def decide(self, conv, now):
        est, flags = conv.estimate, []
        if conv.status != "open" or conv.recipient.opted_out:
            return _stop(f"conversation is {conv.status}")
        last = conv.last_customer_text()
        status = opt_out_status(last) if last else None
        if status == "opt_out":
            conv.recipient.opted_out, conv.status = True, "opted_out"
            return _stop("customer asked to stop (opt-out keyword); no further messages")
        if status == "ambiguous":
            flags.append("an opt-out word appears inside a sentence: owner should glance at it")
        state_text = render_state(est, conv.events, now, conv.views)
        label = classify(self.client, self.cfg.classifier_model, state_text, conv.trace, self.cfg.classifier_method)
        if not label.valid:
            flags.append(f"label failed after retries ({label.attempts}); code default move used")
        elif label.confidence == "low":
            flags.append(f"label {label.label} with low confidence: {label.evidence}")
        if label.label == "opt_out":
            conv.recipient.opted_out, conv.status = True, "opted_out"
            return _stop("label model read an opt-out; no further messages", label, flags)
        max_checkin = None
        if conv.offer.channel == "sms" and not conv.recipient.written_consent:
            max_checkin = 90 - (now - est.sent_at).days
        menu = build_menu(est, conv.offer, conv.slots_after(now), max_checkin_days=max_checkin)
        if label.valid:
            choice = choose(self.client, self.cfg.chooser, state_text, label, menu, est, conv.offer, conv.trace)
        else:
            move = default_move(None, menu)
            choice = Choice(move, template_message(move, est, conv.offer), "no valid label", None, False, True)
        if not choice.valid:
            flags.append(f"move choice failed ({choice.problems}); code default {choice.move.id} used")
        elif choice.template_used:
            flags.append(f"model's message replaced by the template: {choice.problems}")
        move = validate(choice.move, est)
        g = gate(choice.message, move.channel, conv.recipient, est.shop, self.cfg.shop_state, est.sent_at, now,
                 conv.sent_times)
        flags += g.reasons
        effect = {"price_before": conv.offer.price, "price_after": move.price, "change": move.price - conv.offer.price,
                  "margin_after": move.price - est.cost(move.dropped)}
        if not g.ok:
            return Decision(move, None, None, move.needs_owner, effect, label, choice, menu, flags,
                            "blocked by compliance: " + "; ".join(g.reasons))
        return Decision(move, g.text, g.send_at, move.needs_owner, effect, label, choice, menu, flags)

    def apply(self, conv, decision, owner):
        """Owner tap (for concessions), then record the sent message. Returns True when a message went out."""
        if decision.move is None or decision.message is None:
            return False
        if decision.needs_owner and not owner(conv, decision.move):
            decision.flags.append("owner declined the concession; nothing sent")
            return False
        move = decision.move
        conv.events.append(Event(decision.send_at, "shop", move.channel, decision.message, move.id))
        conv.sent_times.append(decision.send_at)
        conv.offer = move.offer(conv.offer)
        if move.kind == "close_out":
            conv.status = "closed"
        return True

    def book(self, conv, now, max_rounds=8):
        """Super tool loop: find a slot, book it at the agreed price, record the job. Returns the job dict or None."""
        est, offer = conv.estimate, conv.offer
        tools = booking_tools()
        messages = [{"role": "system", "content": f"You are the booking assistant of {est.shop}. Use the tools to book "
                                                  f"the accepted estimate: find a slot, book it at the agreed price, then "
                                                  f"record the job. Now: {now:%Y-%m-%d %H:%M} UTC."},
                    {"role": "user", "content": f"Estimate {est.id} ({est.title}) was accepted at ${offer.price:,.0f}. "
                                                f"Agreed slot: {offer.slot.start:%Y-%m-%d %H:%M} UTC if it is still open, "
                                                f"otherwise the earliest open slot after it."}]
        params = {**M.SAMPLING, "max_tokens": 12000, **M.NONEMPTY}
        for _ in range(max_rounds):
            try:
                reply = self.client.chat(self.cfg.booker_model, messages, tools=tools, tool_choice="auto", **params)
            except TFError as e:
                conv.trace.add(Step("book", self.cfg.booker_model, ok=False, note=str(e)[:200]))
                return None
            names = [tc["function"]["name"] for tc in reply.tool_calls]
            conv.trace.add(Step.from_reply("book", reply, note=",".join(names) or "no tool call"))
            if not reply.tool_calls:
                return conv.jobs[-1] if conv.status == "booked" else None
            messages.append({"role": "assistant", "content": reply.content or "", "tool_calls": reply.tool_calls})
            for tc in reply.tool_calls:
                result = run_booking_tool(conv, tc, now)
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": json.dumps(result)})
            if conv.jobs and conv.jobs[-1].get("recorded"):
                return conv.jobs[-1]
        return conv.jobs[-1] if conv.status == "booked" else None


def booking_tools():
    def fn(name, desc, props=None, required=()):
        return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
            "type": "object", "properties": props or {}, **({"required": list(required)} if required else {})}}}
    return [fn("get_shop_hours", "The shop's working hours and time zone."),
            fn("find_slots", "Open crew slots on or after a date.",
               {"earliest_date": {"type": "string", "description": "YYYY-MM-DD"}, "duration_hours": {"type": "number"}},
               ("earliest_date", "duration_hours")),
            fn("book_job", "Book an open slot for the accepted estimate at the agreed price.",
               {"slot_id": {"type": "string"}, "estimate_id": {"type": "string"}, "price": {"type": "number"}},
               ("slot_id", "estimate_id", "price")),
            fn("record_job", "Write the booked job to the shop's job log.",
               {"job_id": {"type": "string"}, "note": {"type": "string"}}, ("job_id", "note"))]


def run_booking_tool(conv, tc, now):
    """Execute one booking tool call against this conversation's calendar. Code enforces price and floor."""
    est, offer = conv.estimate, conv.offer
    name = tc["function"]["name"]
    try:
        args = tool_args(tc)
    except ValueError:
        return {"error": "arguments are not valid JSON"}
    if name == "get_shop_hours":
        return {"hours": "Mon-Sat 08:00-18:00", "time_zone": "America/New_York"}
    if name == "find_slots":
        try:
            day = datetime.fromisoformat(str(args.get("earliest_date"))).date()
        except ValueError:
            return {"error": "earliest_date must be YYYY-MM-DD"}
        return {"slots": [{"slot_id": s.id, "start": s.start.isoformat(), "hours": s.hours}
                          for s in sorted(conv.slots_after(now), key=lambda s: s.start) if s.start.date() >= day][:5]}
    if name == "book_job":
        slot = next((s for s in conv.slots_after(now) if s.id == args.get("slot_id")), None)
        if slot is None:
            return {"error": f"slot {args.get('slot_id')!r} is not open"}
        if args.get("estimate_id") != est.id:
            return {"error": f"estimate_id must be {est.id}"}
        try:
            price = float(args.get("price"))
        except (TypeError, ValueError):
            return {"error": "price must be a number"}
        if abs(price - offer.price) > 0.005:
            return {"error": f"price must be the agreed ${offer.price:,.2f}"}
        if price < est.floor(offer.dropped):
            return {"error": "price is below the owner's floor"}
        conv.open_slots = [s for s in conv.open_slots if s.id != slot.id]
        job = {"job_id": f"J-{est.id}-{len(conv.jobs) + 1}", "slot_id": slot.id, "start": slot.start.isoformat(),
               "price": price, "items": [i.name for i in est.items if i.name not in offer.dropped], "recorded": False}
        conv.jobs.append(job)
        conv.status = "booked"
        return {"job_id": job["job_id"], "start": job["start"], "price": price}
    if name == "record_job":
        job = next((j for j in conv.jobs if j["job_id"] == args.get("job_id")), None)
        if job is None:
            return {"error": f"no job {args.get('job_id')!r}"}
        job["recorded"], job["note"] = True, str(args.get("note", ""))[:300]
        return {"ok": True}
    return {"error": f"unknown tool {name}"}
