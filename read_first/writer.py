"""Nemotron Super writes the message around a move the code already chose. The text cannot change the offer.

One call, reasoning off (as the shipped label step), through a named tool. The text is checked by
`second_ask.menu.check_message` (every $ amount and % must belong to the move; length) and by
`read_first.decide.date_problems` (every date, weekday or span must be the move's slot day or check-in); any problem,
a missing tool call or a platform error gives the move's code template (`second_ask.menu.template_message`).
"""
from dataclasses import dataclass, field

from second_ask import models as M
from second_ask.classifier import render_state
from second_ask.menu import check_message, template_message
from second_ask.tf_client import TFError, tool_args
from second_ask.trace import Step

from .decide import date_problems

WRITER_MODEL = M.SUPER
TOOL_NAME = "write_message"
SYSTEM = """You write one short text message from {shop}, a home-service shop, to a customer who received an estimate.
The shop's own code has already decided the offer from what the customer said. You cannot change it: do not add, change or hint at any other price, percentage, discount, item, date, weekday or time span.
Answer what the customer actually said, in plain friendly words, and put the offer in it.
Plain text, under 300 characters, no greeting line of its own, no emoji, no signature. Do not say you are a person; the shop adds its own disclosure line.
Call {tool} exactly once."""
TOOL = {"type": "function", "function": {
    "name": TOOL_NAME, "description": "The text message to send to the customer.",
    "parameters": {"type": "object", "properties": {"message": {"type": "string", "description": "Under 300 characters."}},
                   "required": ["message"], "additionalProperties": False}}}
PARAMS = {**M.SAMPLING, **M.REASONING_OFF, "max_tokens": 512}


def _money(x):
    return f"${x:,.0f}"


def describe(move, est, offer, facts=None, timing=None):
    """The fixed offer in words, with every amount and date the message may use."""
    items = ", ".join(i.name for i in est.items if i.name not in move.dropped)
    lines = [f"Items: {items}.", f"Price: {_money(move.price)} (standing price {_money(offer.price)}, "
                                 f"estimate total {_money(est.list_price())})."]
    if move.dropped:
        lines.append("Left out at the customer's request: " + ", ".join(sorted(move.dropped)) + ".")
    kind = {"answer": "Answer the customer; nothing changes.",
            "ask_why": "Ask one short, friendly question about what is holding them back; offer nothing.",
            "reminder": "Remind them the estimate is still open; nothing new.",
            "checkin": "No pressure and no price change.",
            "offer_slot": "Offer to book the slot below.", "earlier_slot": "Offer to book the slot below.",
            "resend": "Resend the estimate summary.", "drop": "Offer the job without the item(s) left out.",
            "concede": "Offer the lower price; the owner approves it before it is sent.",
            "close_out": "Thank them and stop following up."}[move.kind]
    lines.append("Move: " + kind)
    if move.slot is not None:
        lines.append(f"Slot: {move.slot.start:%A %b %d} (you may name this day; no other date or weekday).")
    if move.checkin_days is not None:
        lines.append(f"Check back in {move.checkin_days} days (you may say exactly that span; no other).")
    if timing and timing.get("kind") == "slot" and timing.get("meets_deadline") is False:
        lines.append("That slot is the earliest open one and it is later than the customer needs: say so plainly, "
                     "without naming their date.")
    if facts is not None and facts.decider:
        lines.append(f"The customer wants to talk it over with {facts.decider} first: respect that.")
    if move.needs_owner:
        lines.append("Do not call it final or guaranteed: the owner confirms the price.")
    return "\n".join(lines)


def request(conv, move, now, facts=None, timing=None):
    est = conv.estimate
    user = (f"{render_state(est, conv.events, now, conv.views)}\n\nThe shop's fixed offer (decided by code):\n"
            f"{describe(move, est, conv.offer, facts, timing)}")
    messages = [{"role": "system", "content": SYSTEM.format(shop=est.shop, tool=TOOL_NAME)},
                {"role": "user", "content": user}]
    return messages, {**PARAMS, "tools": [TOOL], "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}}}


@dataclass
class Written:
    text: str
    template_used: bool
    problems: list = field(default_factory=list)
    model: str | None = None
    seconds: float = 0.0
    usd: float = 0.0


def write(client, conv, move, now, trace, customer_amounts=(), facts=None, timing=None, model=WRITER_MODEL):
    """The message for `move`: Super's text if it passes every check, else the template. Adds one "write" step."""
    est = conv.estimate
    template = template_message(move, est, conv.offer)
    messages, params = request(conv, move, now, facts, timing)
    try:
        reply = client.chat(model, messages, **params)
    except TFError as e:
        trace.add(Step.platform_failure("write", model, e))
        return Written(template, True, [f"HTTP {e.status}"], model)
    calls = [tc for tc in reply.tool_calls if tc["function"].get("name") == TOOL_NAME]
    problems = []
    text = ""
    if len(calls) != 1:
        problems.append(f"{len(calls)} {TOOL_NAME} tool calls")
    else:
        try:
            args = tool_args(calls[0])
        except ValueError as e:
            args, problems = {}, [f"arguments not JSON: {e}"]
        text = str(args.get("message") or "").strip() if isinstance(args, dict) else ""
        if not problems:
            problems += check_message(text, move, est, conv.offer, customer_amounts)
            problems += date_problems(text, move)
    trace.add(Step.from_reply("write", reply, ok=not problems, note="; ".join(problems)[:200] or "text passed the checks"))
    if problems:
        return Written(template, True, problems, reply.model, reply.seconds, reply.cost)
    return Written(text, False, [], reply.model, reply.seconds, reply.cost)
