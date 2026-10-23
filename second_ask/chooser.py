"""Pick the next move from the menu the code built, and write the message. The model never sets a price.

The chosen id must be on the menu; the message is checked for amounts or percents that are not part of the move and
replaced by the move's template if it fails. An invalid choice is retried once, then the code default for the label
is used and flagged.
"""
from dataclasses import dataclass, field

from . import models as M
from .menu import check_message, template_message
from .tf_client import TFError, tool_args
from .trace import Step

SYSTEM = """You are the follow-up assistant of {shop}, a home-service shop. A customer received an estimate and has not booked.
You get the estimate, the conversation, a label for why the customer seems quiet, and a menu of moves the shop's code allows.
Every price on the menu already respects the owner's floor; you cannot set any other price.
Pick the one move most likely to win the job without giving away money the customer did not need:
- Answer what the customer actually raised. A discount does not help a customer who is not ready yet, needs a different slot, missed the message or is waiting on a family decision; it only gives away margin.
- A generic reminder with nothing new rarely works.
- If price is the issue, prefer dropping an optional item or the smallest concession that plausibly closes the gap. A cheaper quote below the floor cannot be matched.
- If they are not ready yet, agree on when to check back, matching what they said.
- If they need it sooner, offer an earlier slot. If they may have missed the estimate, resend it on the other channel. If they are ready, offer the slot.
Then write the message: plain text, under 300 characters, friendly and specific, no prices or percentages other than the chosen move's. Do not say you are a person; the shop adds its own disclosure line.
Call choose_move exactly once."""

DEFAULTS = {"price": "answer", "timing": "checkin_14", "family": "answer", "channel": "resend",
            "ready_to_book": "offer_slot", "other": "ask_why", None: "ask_why"}

CONFIGS = {  # name -> (model, params). Ultra with tools requires reasoning on (NVIDIA model card).
    "super_think": (M.SUPER, {**M.SAMPLING, "max_tokens": 12000, **M.NONEMPTY}),
    "super_nothink": (M.SUPER, {**M.SAMPLING, "max_tokens": 1024, **M.REASONING_OFF}),
    "ultra_think": (M.ULTRA, {**M.SAMPLING, "max_tokens": 16000, **M.THINK_TOOLS}),
    "super_vl_think": (M.SUPER_VL, {**M.SAMPLING, "max_tokens": 12000, **M.NONEMPTY}),
    "super_vl_nothink": (M.SUPER_VL, {**M.SAMPLING, "max_tokens": 1024, **M.REASONING_OFF}),
}


@dataclass
class Choice:
    move: object
    message: str
    reason: str
    model: str | None
    valid: bool                 # the model's choice was on the menu
    template_used: bool         # the model's message failed the check and was replaced
    problems: list = field(default_factory=list)


def default_move(label, menu):
    """The code's own move for a label, used when the model gives no valid choice."""
    by_id = {m.id: m for m in menu}
    if label == "channel":
        return next((m for m in menu if m.kind == "resend"), by_id["ask_why"])
    return by_id.get(DEFAULTS.get(label, "ask_why")) or by_id["ask_why"]


def tool_for(menu):
    return {"type": "function", "function": {
        "name": "choose_move", "description": "Choose the shop's next move and write the message to the customer.",
        "parameters": {"type": "object", "properties": {
            "move_id": {"type": "string", "enum": [m.id for m in menu]},
            "message": {"type": "string", "description": "Text to send, under 300 characters."},
            "reason": {"type": "string", "description": "One sentence: why this move, citing the customer's words."}},
            "required": ["move_id", "message", "reason"]}}}


def render_menu(menu, est, offer):
    cur = offer.price
    rows = []
    for m in menu:
        margin = m.price - est.cost(m.dropped)
        rows.append(f"- {m.id}: {m.description} [price ${m.price:,.0f}, change vs current ${m.price - cur:+,.0f}, "
                    f"margin ${margin:,.0f}{', needs owner tap' if m.needs_owner else ''}]")
    return "\n".join(rows)


def request(shop, state_text, label, menu, est, offer):
    user = (f"{state_text}\n\nLabel: {label.label or 'unavailable'} (confidence {label.confidence or '-'}; "
            f"evidence: {label.evidence or '-'})\nCurrent offer: ${offer.price:,.0f}.\n\nMenu:\n{render_menu(menu, est, offer)}")
    return [{"role": "system", "content": SYSTEM.format(shop=shop)}, {"role": "user", "content": user}], [tool_for(menu)]


def choose(client, config, state_text, label, menu, est, offer, trace):
    model, params = CONFIGS[config] if isinstance(config, str) else config
    messages, tools = request(est.shop, state_text, label, menu, est, offer)
    by_id = {m.id: m for m in menu}
    problems = []
    for attempt in range(2):
        try:
            reply = client.chat(model, messages, tools=tools, tool_choice="auto", **params)
        except TFError as e:
            problems.append(f"HTTP {e.status}")
            trace.add(Step("choose", model, ok=False, note=str(e)[:200]))
            continue
        calls = [tc for tc in reply.tool_calls if tc["function"]["name"] == "choose_move"]
        try:
            args = tool_args(calls[0]) if calls else {}
        except ValueError as e:
            args, problems = {}, problems + [f"arguments not JSON: {e}"]
        move = by_id.get(args.get("move_id"))
        trace.add(Step.from_reply("choose", reply, ok=move is not None, note=args.get("move_id") or "no valid choose_move call"))
        if move is None:
            problems.append(f"invalid choice {args.get('move_id')!r}" if calls else "no choose_move call")
            continue
        text = str(args.get("message") or "")
        msg_problems = check_message(text, move, est, offer)
        if msg_problems:
            return Choice(move, template_message(move, est, offer), str(args.get("reason") or ""), reply.model, True, True,
                          problems + msg_problems)
        return Choice(move, text.strip(), str(args.get("reason") or ""), reply.model, True, False, problems)
    move = default_move(label.label, menu)
    return Choice(move, template_message(move, est, offer), f"code default for label {label.label!r}", None, False, True,
                  problems)
