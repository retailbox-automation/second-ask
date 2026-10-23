"""The six pre-registered policies. Every one goes through the same floor check and compliance gate.

1 shop_rule             discount to the owner's floor on the first objection or silence, then weekly reminders
2 concession_heuristic  concede half of the remaining gap to the floor at every objection or silence (PrefBench-style)
3 super_only            Nemotron Super labels and chooses
4 ultra_only            Nemotron Ultra labels and chooses
5 routed                Lightning labels; Super chooses (S7: Ultra was not better than Super, so it is not routed in)
6 super_vl              Nemotron Super-VL labels and chooses
"""
import json, random

from .. import models as M
from ..agent import Agent, AgentConfig, make_decision
from ..chooser import CONFIGS, DEFAULTS
from ..classifier import LABELS
from ..menu import build_menu, concession, template_message
from ..tf_client import PRICES, _parse

LLM_POLICIES = {
    "super_only": AgentConfig(classifier_model=M.SUPER, classifier_method="tool", chooser="super_think"),
    "ultra_only": AgentConfig(classifier_model=M.ULTRA, classifier_method="json_object", chooser="ultra_think"),
    "routed": AgentConfig(classifier_model=M.LIGHTNING, classifier_method="json_object", chooser="super_think"),
    "super_vl": AgentConfig(classifier_model=M.SUPER_VL, classifier_method="tool", chooser="super_vl_nothink"),
}
RULE_POLICIES = ("shop_rule", "concession_heuristic")
ALL = RULE_POLICIES + tuple(LLM_POLICIES)


def _stop_decision(conv, now, move):
    return make_decision(conv, move, template_message(move, conv.estimate, conv.offer), now, "FL")


class ShopRule:
    name, llm, next_check_days = "shop_rule", False, 7

    def act(self, conv, now, hint):
        est, offer = conv.estimate, conv.offer
        by = {m.id: m for m in build_menu(est, offer, conv.slots_after(now))}
        if hint == "ready":
            move = by["offer_slot"]
        elif offer.price > est.floor(offer.dropped) and "concede_pkg_floor" in by:
            move = by["concede_pkg_floor"]  # "best price if you book this week"
        else:
            move = by["reminder"]
        return _stop_decision(conv, now, move)


class ConcessionHeuristic:
    name, llm, next_check_days = "concession_heuristic", False, 4

    def act(self, conv, now, hint):
        est, offer = conv.estimate, conv.offer
        by = {m.id: m for m in build_menu(est, offer, conv.slots_after(now))}
        if hint == "ready":
            return _stop_decision(conv, now, by["offer_slot"])
        floor = est.floor(offer.dropped)
        gap = offer.price - floor
        if gap < 10:
            return _stop_decision(conv, now, by["reminder"])
        price = floor if gap < 20 else max(floor, round((offer.price - gap / 2) / 5) * 5)
        return _stop_decision(conv, now, concession(est, offer, price))


class LLMPolicy:
    llm, next_check_days = True, 3

    def __init__(self, name, client):
        self.name, self.agent = name, Agent(client, LLM_POLICIES[name])

    def act(self, conv, now, hint):  # the coarse act is ignored: the agent reads the messages itself
        return self.agent.decide(conv, now)


def make(name, client=None):
    if name == "shop_rule":
        return ShopRule()
    if name == "concession_heuristic":
        return ConcessionHeuristic()
    return LLMPolicy(name, client)


class FakeSimClient:
    """Dry-run stand-in for Token Factory: answers label and choose_move calls without a model.

    The label is right with probability `accuracy` (it peeks at the buyer card the runner sets), the choice follows the
    code playbook; usage is a rough character count so the dry run also exercises the cost path. Not evidence of
    model quality: it only proves the machinery end to end and counts calls.
    """

    def __init__(self, accuracy=0.85, seed=1):
        self.accuracy, self.rng, self.card, self.calls = accuracy, random.Random(seed), None, []

    def chat(self, model, messages, **params):
        from .buyers import LABEL_OF
        self.calls.append(model)
        text = json.dumps(messages)
        names = [t["function"]["name"] for t in params.get("tools", [])]
        if "choose_move" in names:
            enum = params["tools"][0]["function"]["parameters"]["properties"]["move_id"]["enum"]
            label = messages[1]["content"].split("Label: ", 1)[1].split(" ", 1)[0]
            want = {"price": [m for m in enum if m.startswith("drop_")] + ["concede_pkg_5", "concede_pkg_floor"],
                    "timing": (["earlier_slot_1"] if "sooner" in text else []) + ["checkin_14"],
                    "channel": [m for m in enum if m.startswith("resend_")]}.get(label, [DEFAULTS.get(label, "ask_why")])
            move = next((m for m in want if m in enum), "ask_why")
            body = {"tool_calls": [{"id": "f", "type": "function", "function": {"name": "choose_move", "arguments": json.dumps(
                {"move_id": move, "message": "Thanks for the reply. Here is what we can do.", "reason": "dry run"})}}]}
            comp = 150
        else:
            truth = LABEL_OF.get(getattr(self.card, "type", ""), "other")
            lbl = truth if self.rng.random() < self.accuracy else self.rng.choice([x for x in LABELS if x != "opt_out"])
            obj = {"label": lbl, "confidence": "high", "evidence": "dry run"}
            if "label_reply" in names:
                body = {"tool_calls": [{"id": "f", "type": "function", "function": {"name": "label_reply", "arguments": json.dumps(obj)}}]}
            else:
                body = {"content": json.dumps(obj)}
            comp = 40
        raw = {"id": "dry-run", "choices": [{"message": {"role": "assistant", "content": body.get("content"), **body},
                                             "finish_reason": "stop"}],
               "usage": {"prompt_tokens": len(text) // 4, "completion_tokens": comp,
                         "completion_tokens_details": {"reasoning_tokens": 0}}}
        p_in, p_out = PRICES[model]
        return _parse(model, raw, 0.0, p_in, p_out)


assert set(LLM_POLICIES[n].chooser for n in LLM_POLICIES) <= set(CONFIGS)
