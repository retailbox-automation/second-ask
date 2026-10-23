"""Why is the customer quiet? One label per customer message (or per silence), from a Nemotron model.

Output comes through a named tool (or a JSON object) and is validated; an invalid answer is retried once on the same
model, then sent to the fallback model. A label the code cannot validate is never used.
"""
import json
from dataclasses import dataclass, field

from . import models as M
from .tf_client import TFError
from .trace import Step

LABELS = ("price", "timing", "family", "channel", "ready_to_book", "opt_out", "other")
GUIDE = """- price: the total is the problem: too expensive, a cheaper quote elsewhere, a budget ceiling, asks for a discount.
- timing: about when, not whether: not ready yet (waiting for a date, money, season, travel), or needs the job sooner than the quoted slot.
- family: someone else has to agree (spouse, partner, parent, landlord, HOA), or they want to talk it over at home first.
- channel: they have not seen the estimate: no reply and the estimate was never opened, or they say they missed it or prefer another way to be contacted.
- ready_to_book: wants to go ahead or asks when the crew can come.
- opt_out: asks the shop to stop messaging them.
- other: anything else (a question about the work, not interested, already hired someone, unclear)."""
SYSTEM = ("You read a homeowner's reply (or silence) to a home-service estimate and decide why they have not booked yet. "
          "Choose exactly one label:\n" + GUIDE + "\nRest the label on what the customer actually said and on the facts "
          "given (days since the estimate, how often it was opened). Silence alone is not a price objection. "
          "confidence is \"low\" when the message could fit two labels.")
SCHEMA = {"type": "object", "properties": {
    "label": {"type": "string", "enum": list(LABELS)},
    "confidence": {"type": "string", "enum": ["high", "low"]},
    "evidence": {"type": "string", "description": "The customer's words or the facts the label rests on, under 20 words."}},
    "required": ["label", "confidence", "evidence"], "additionalProperties": False}
TOOL = {"type": "function", "function": {"name": "label_reply", "description": "Record why the customer has not booked yet.",
                                         "parameters": SCHEMA}}
METHODS = ("tool", "json_object", "json_schema")


@dataclass
class Label:
    label: str | None
    confidence: str | None
    evidence: str
    model: str | None
    method: str
    valid: bool
    attempts: list = field(default_factory=list)  # (model, problem or "ok") per call


def render_state(est, events, now, views):
    """The text the label model sees: estimate, timing facts, and the conversation so far."""
    lines = [f"Estimate {est.id} from {est.shop} ({est.trade}): {est.title}.",
             "Line items: " + "; ".join(f"{i.name} ${i.price:,.0f}{' (optional)' if i.optional else ''}" for i in est.items)
             + f". Total ${est.list_price():,.0f}.",
             f"Sent by {est.channel} {(now - est.sent_at).days} days ago; opened {views} time(s). "
             f"Quoted slot: {est.default_slot.start:%a %b %d}. Now: {now:%a %b %d %H:%M} UTC.", "", "Conversation:"]
    if not events:
        lines.append("(no messages yet; the customer has not replied)")
    for e in events:
        who = "Shop" if e.who == "shop" else "Customer"
        lines.append(f"[{e.at:%b %d %H:%M} {e.channel}] {who}: {e.text}")
    if events and events[-1].who == "shop":
        lines.append("(no reply from the customer since the shop's last message)")
    return "\n".join(lines)


def request(model, state_text, method):
    """Messages and parameters for one label call. Reasoning off: the label must be fast and cheap."""
    params = {**M.SAMPLING, **M.REASONING_OFF, "max_tokens": 512}
    system = SYSTEM
    if method == "tool":
        params.update(tools=[TOOL], tool_choice={"type": "function", "function": {"name": "label_reply"}})
    elif method == "json_object":
        params["response_format"] = {"type": "json_object"}
        system += ' Answer only with a JSON object: {"label": ..., "confidence": "high"|"low", "evidence": "..."}.'
    elif method == "json_schema":
        params["response_format"] = {"type": "json_schema", "json_schema": {"name": "label_reply", "schema": SCHEMA, "strict": True}}
    else:
        raise ValueError(method)
    return [{"role": "system", "content": system}, {"role": "user", "content": state_text}], params


def parse(reply, method):
    """(label dict, None) or (None, problem)."""
    if method == "tool":
        calls = [tc for tc in reply.tool_calls if tc["function"]["name"] == "label_reply"]
        if not calls:
            return None, "no label_reply tool call"
        raw = calls[0]["function"]["arguments"]
    else:
        raw = reply.text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"not JSON: {e}"
    if not isinstance(obj, dict):
        return None, "not an object"
    if obj.get("label") not in LABELS:
        return None, f"label {obj.get('label')!r} not in {LABELS}"
    if obj.get("confidence") not in ("high", "low"):
        return None, f"confidence {obj.get('confidence')!r}"
    if not isinstance(obj.get("evidence"), str):
        return None, "evidence missing"
    return obj, None


def classify(client, model, state_text, trace, method="tool", fallback=(M.SUPER, "tool")):
    """Label with `model`; one retry on an invalid answer; then the fallback model. Errors are logged, never swallowed."""
    attempts = []
    plan = [(model, method), (model, method)] + ([fallback] if fallback and fallback[0] != model else [])
    for i, (m, meth) in enumerate(plan):
        messages, params = request(m, state_text, meth)
        try:
            reply = client.chat(m, messages, **params)
        except TFError as e:
            attempts.append((m, f"HTTP {e.status}"))
            trace.add(Step("classify", m, ok=False, note=f"{meth}: {str(e)[:200]}"))
            continue
        obj, problem = parse(reply, meth)
        trace.add(Step.from_reply("classify", reply, ok=obj is not None, note=f"{meth}: {problem or obj['label']}"))
        attempts.append((reply.model, problem or "ok"))
        if obj:
            return Label(obj["label"], obj["confidence"], obj["evidence"][:200], reply.model, meth, True, attempts)
    return Label(None, None, "", None, method, False, attempts)
