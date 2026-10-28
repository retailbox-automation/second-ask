"""Nemotron reader: one prompt per reply, Facts back through a named tool (or a JSON object), validated by the contract.

Methods (one per arm):
  tool         named tool `record_facts` with contract.SCHEMA, reasoning off (Super, Super-VL; as the shipped label step)
  json_object  response_format json_object, reasoning off (Lightning)
  think_tools  tools with reasoning on (Ultra: NVIDIA requires reasoning on for Ultra tool calls), tool_choice auto
An output that fails the contract is retried once on the same model; a second failure returns None (a non-match,
never repaired). A platform error (TFError after the client's own retries) returns None with error_kind "platform" so
the harness can re-run the line. BudgetExceeded is not caught: it must stop the run.
"""
import json

from second_ask import models as M
from second_ask.tf_client import TFError, tool_args

from ..contract import SCHEMA, FactsError, parse
from . import optional_items, reader_input, render

SYSTEM_PROMPT = """You read one customer's reply to a home-service estimate and record only the facts the customer \
actually states. You do not decide anything and you do not write a reply: the shop's own code chooses the next offer \
from your facts.

Fields:
- stop: true only if the customer asks the shop to stop contacting them ("stop", "unsubscribe", "remove my number", \
"don't text me again", "no me escriban más"), in any words or language. Turning the job down or saying they hired \
someone else is not stop unless they also ask not to be contacted.
- ceiling_usd: the most they say they will pay for the job, in dollars: a budget ("I only have 1,800"), a price they \
ask for ("can you do it for two grand?" -> 2000), or another company's all-in price they would book at ("Acme quoted \
1,650 all in" -> 1650). Amounts in words or without "$" count ("fifteen hundred" -> 1500, "dos mil" -> 2000, \
"2.5k" -> 2500). If they ask for an amount off ("knock off 200"), the ceiling is the estimate total minus that amount; \
a percentage off the total works the same way. null if they name no amount they would pay.
- drop_items: optional line items of THIS estimate that they say they do not want or need, written exactly as the \
estimate names them, also when the customer calls them something else ("skip the surge thing" -> "Surge protector"). \
Only items marked (optional) can be dropped; never list the main job. Empty list if none.
- need_by: the latest day the job must be done, as YYYY-MM-DD, worked out from today's date and weekday ("by Friday" \
-> that Friday; "before the 14th" -> the 13th; "end of next week" -> Friday of next week; "next week" is the Monday-to-\
Sunday week after this one). null if they give no deadline.
- wait_weeks: they want the job, but later: about how many weeks from today, as a whole number of at least 1 ("in a \
couple of weeks" -> 2, "next month" -> 4, "after the 20th" -> the weeks from today to then, rounded). null if they \
are not asking to wait.
- decider: someone else must agree first (my wife, my husband, my partner, my landlord, the HOA, my dad): that \
person, as the customer names them. null if nobody else has to agree.
- ready: true if they want to book now or ask when the crew can come.

One reply can state several facts; record every one of them. The reply may be in English, Spanish or a mix of both. \
Do not guess: a field the customer does not state is false, null or an empty list."""
TOOL_NAME = "record_facts"
TOOL = {"type": "function", "function": {"name": TOOL_NAME, "description": "Record the facts the customer's reply states.",
                                         "parameters": SCHEMA}}
JSON_SUFFIX = ("\n\nAnswer only with one JSON object with exactly these keys: stop, ceiling_usd, drop_items, need_by, "
               "wait_weeks, decider, ready.")
TOOL_SUFFIX = f"\n\nCall {TOOL_NAME} exactly once with the facts."
METHODS = ("tool", "json_object", "think_tools")


def request(method, user_text):
    """(messages, params) for one read call."""
    if method == "tool":
        params = {**M.SAMPLING, **M.REASONING_OFF, "max_tokens": 1024, "tools": [TOOL],
                  "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}}}
        system = SYSTEM_PROMPT + TOOL_SUFFIX
    elif method == "json_object":
        params = {**M.SAMPLING, **M.REASONING_OFF, "max_tokens": 1024, "response_format": {"type": "json_object"}}
        system = SYSTEM_PROMPT + JSON_SUFFIX
    elif method == "think_tools":
        params = {**M.SAMPLING, **M.THINK_TOOLS, "max_tokens": 8192, "tools": [TOOL], "tool_choice": "auto"}
        system = SYSTEM_PROMPT + TOOL_SUFFIX
    else:
        raise ValueError(f"unknown method {method!r}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user_text}], params


def raw_facts(reply, method):
    """(dict, None) or (None, problem) from one reply."""
    if method in ("tool", "think_tools"):
        calls = [tc for tc in reply.tool_calls if tc["function"].get("name") == TOOL_NAME]
        if len(calls) != 1:
            return None, f"{len(calls)} {TOOL_NAME} tool calls"
        try:
            obj = tool_args(calls[0])
        except ValueError as e:
            return None, f"arguments not JSON: {e}"
    else:
        text = (reply.text or "").strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            return None, f"not JSON: {e}"
    return obj, None


class NemotronReader:
    def __init__(self, model, method, client=None, name=None):
        if method not in METHODS:
            raise ValueError(f"unknown method {method!r}")
        self.model, self.method, self.client = model, method, client
        self.name = name or f"nemotron:{model}:{method}"

    def read(self, case, card=None):
        inp = reader_input(case, card)
        messages, params = request(self.method, render(inp))
        meta = {"reader": self.name, "model": self.model, "method": self.method, "seconds": 0.0, "usd": 0.0,
                "attempts": [], "error": None, "error_kind": None, "served_model": None, "usage": {}}
        for _ in range(2):  # one retry on output that fails the contract
            try:
                reply = self.client.chat(self.model, messages, **params)
            except TFError as e:
                meta["attempts"].append({"problem": f"TFError {e.status}"})
                meta.update(error=str(e)[:300], error_kind="platform")
                return None, meta
            meta["seconds"] = round(meta["seconds"] + reply.seconds, 3)
            meta["usd"] = meta["usd"] + reply.cost
            meta["served_model"] = reply.model
            for k in ("prompt_tokens", "completion_tokens"):
                meta["usage"][k] = meta["usage"].get(k, 0) + (reply.usage.get(k) or 0)
            meta["usage"]["reasoning_tokens"] = meta["usage"].get("reasoning_tokens", 0) + reply.reasoning_tokens
            obj, problem = raw_facts(reply, self.method)
            if obj is not None:
                try:
                    facts = parse(obj, optional_items(inp))
                except FactsError as e:
                    problem = f"contract: {e}"
                else:
                    meta["attempts"].append({"seconds": reply.seconds, "usd": reply.cost, "problem": None,
                                             "finish_reason": reply.finish_reason})
                    meta["error"] = None
                    return facts, meta
            meta["attempts"].append({"seconds": reply.seconds, "usd": reply.cost, "problem": problem,
                                     "finish_reason": reply.finish_reason})
            meta.update(error=problem[:300], error_kind="invalid")
        return None, meta
