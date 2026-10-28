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

SYSTEM_PROMPT = """You read one customer's reply to a home-service estimate and record only the facts the customer actually states. You do not decide anything and you do not write a reply: the shop's own code chooses the next offer from your facts.

Fields:
- stop: true only if the customer asks the shop to stop contacting them ("stop", "unsubscribe", "remove me from your list", "don't text me again", "don't reach out again", "no me contacten más"), in any words or language. Turning the job down or saying they hired someone else is not stop unless they also ask not to be contacted. When stop is true, leave every other field empty (false, null, []).
- ceiling_usd: the most they say they will pay for the job, in dollars: a budget or maximum ("the most I can pay is 1,800", "I can't go over 560"), a price they ask for ("can you do it for two grand?" -> 2000, "if we can keep it at 2500 total" -> 2500), or another company's all-in price they would book at ("another place quoted 1,650 all in" -> 1650). Amounts in words or without "$" count ("fifteen hundred" -> 1500, "dos mil" -> 2000, "2.5k" -> 2500). If they ask for an amount off ("knock off 200"), the ceiling is the estimate total minus that amount; a percentage off the total works the same way. A price they only repeat from the estimate (agreeing to its total or to an item's listed price) is not a ceiling. null if they name no amount of their own.
- drop_items: optional line items of THIS estimate that they say they do not want or need, written exactly as the estimate names them, also when the customer calls them something else ("skip the surge thing" -> "Surge protector"). Only items marked (optional) can be dropped; never list the main job. Empty list if none.
- need_by: the latest day the job must be done, as YYYY-MM-DD, worked out from today's date and weekday. "By X", "no later than X", "para el X" -> X itself ("by the 18th" -> the 18th, "by Sunday, Nov 15" -> Nov 15). Only "before X" / "antes del X" moves one day earlier ("before the 14th" -> the 13th). "By the end of next week" -> Friday of next week ("next week" is the Monday-to-Sunday week after this one). A bare day of the month is the next such date on or after today (normally this month). A bare weekday ("by Thursday") is the next such weekday after today. null if they name no day or date for it: asking to book now or when the crew can come ("ya", "asap") is ready, not a deadline. Wanting it some weeks from now is wait_weeks, not need_by.
- wait_weeks: they want the job, but later: about how many weeks from today, as a whole number of at least 1 ("about a week from now" -> 1, "a couple of weeks" -> 2, "about three weeks" -> 3, "about a month" or "next month" -> 4, "about two months" -> 8). null if they are not asking to wait.
- decider: someone else must agree first (my wife, my husband, my partner, my landlord, the HOA, my dad, my mom): that person, as the customer names them. null if nobody else has to agree.
- ready: false by default. true ONLY when they say they want to book now ("book it", "please book us", "schedule it now", "quiero agendar") or ask when the crew can come ("when can you come out?", "¿cuándo pueden venir?"). Everything else is false: naming a price limit or another company's price, asking "can you do it for that?", "can you work with that?" or "¿me pueden igualar?", asking them to make sure of a deadline or whether it is possible, asking to remove an item ("just the basic job please"), saying they need or want the job done, saying they would go with whoever matches a price, or wanting it some weeks from now (that is wait_weeks, not ready).

One reply can state several facts; record every one of them. The reply may be in English, Spanish or a mix of both. Do not guess: a field the customer does not state is false, null or an empty list. Write an empty value as JSON null, never as the text "None", "null" or ""."""
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
