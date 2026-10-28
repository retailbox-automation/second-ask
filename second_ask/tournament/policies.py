"""The seven pre-registered policies. Every one goes through the same floor check and compliance gate.

1 shop_rule             discount to the owner's floor on the first objection or silence, then weekly reminders
2 concession_heuristic  concede half of the remaining gap to the floor at every objection or silence, every 4 days: a
                        naive gap-halving rule (not the concession heuristic of any paper)
3 super_only            Nemotron Super labels (named tool) and chooses
4 ultra_only            Nemotron Ultra labels (JSON object) and chooses
5 routed                Lightning labels (JSON object, Super tool as the fallback label model); Super chooses. Against
                        super_only it differs in the label model, the label output format and the label fallback: each
                        model in its best S3 format. S7: Ultra was not better than Super, so it is not routed in.
6 super_vl              Nemotron Super-VL labels and chooses
7 smart_rules           the strongest rule baseline we could write without a model (SmartRules); the main baseline
"""
import json, random, re

from .. import models as M
from ..agent import Agent, AgentConfig, make_decision
from ..chooser import CONFIGS, DEFAULTS
from ..classifier import LABELS
from ..menu import amounts_in, build_menu, concession, template_message
from ..tf_client import PRICES, _parse
from ..trace import Step

LLM_POLICIES = {
    "super_only": AgentConfig(classifier_model=M.SUPER, classifier_method="tool", chooser="super_think"),
    "ultra_only": AgentConfig(classifier_model=M.ULTRA, classifier_method="json_object", chooser="ultra_think"),
    "routed": AgentConfig(classifier_model=M.LIGHTNING, classifier_method="json_object", chooser="super_think"),
    "super_vl": AgentConfig(classifier_model=M.SUPER_VL, classifier_method="tool", chooser="super_vl_nothink"),
}
RULE_POLICIES = ("shop_rule", "concession_heuristic")  # never read the buyer's words: one run each (K = 1)
SMART = "smart_rules"                                   # reads the words, so it is repeated like the LLM policies
REPEATED = (SMART,) + tuple(LLM_POLICIES)
ALL = RULE_POLICIES + REPEATED


def _stop_decision(conv, now, move):
    return make_decision(conv, move, template_message(move, conv.estimate, conv.offer), now, "FL")


def _arm_rule(self):
    return {}  # no shop-side model calls: any would be off-arm


class ShopRule:
    name, llm, next_check_days = "shop_rule", False, 7
    arm = _arm_rule

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
    arm = _arm_rule

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


def _menu(conv, now):
    """The agent's menu, with the agent's 90-day SMS limit on check-ins (agent.Agent.decide)."""
    est = conv.estimate
    max_checkin = None
    if conv.offer.channel == "sms" and not conv.recipient.written_consent:
        max_checkin = 90 - (now - est.sent_at).days
    return build_menu(est, conv.offer, conv.slots_after(now), max_checkin_days=max_checkin)


_SOONER = re.compile(r"\b(sooner|too late|asap|urgent|urgently|right away|done by|need it by|need this by|by (monday|"
                     r"tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|the weekend)|this week|tomorrow|"
                     r"can'?t wait|cannot wait|earlier)\b")
_WEEKS = re.compile(r"\b(\d+|a|one|two|three|four|five|six|a couple of|a few|few) (?:more )?weeks?\b")
_LATER = {r"\bnext month\b|\ba month\b": 30, r"\blater this month\b|\bafter the holidays\b|\bwhen the (insurance|"
          r"money)\b|\bnot ready\b|\bnot yet\b": 14}
_FAMILY = re.compile(r"\b(wife|husband|partner|landlord|dad|mom|mother|father|family|spouse|hoa|talk(ed)? it over|"
                     r"go over it|look at it with)\b")
_READY = re.compile(r"\b(just saw|missed|looks good|looks fine|sounds good|when can you|come out|book|go ahead|"
                    r"let'?s do it|schedule)\b")
_PRICE = re.compile(r"\b(expensive|too much|more than|cheaper|budget|price|cost|afford|discount|deal|lower)\b")
_THINK = re.compile(r"\b(think|get back|not sure|maybe|consider)\b")
_NOT_NEEDED = re.compile(r"not needed|don'?t need|do without|skip|no need|without the")
_NUMBER_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "a couple of": 2, "a few": 3,
                 "few": 3}


class SmartRules:
    """The strongest rule baseline we could write without a model. Frozen in the manifest (policies.smart_rules).

    Inputs are only what the LLM policies see: the messages and their times, and how often the estimate was opened (the
    coarse hint the other rule policies get is ignored). Same menu, same 3-day rhythm, prices only from the menu.
    On the customer's latest unanswered message (lower-cased, ’ read as '), the first matching rule wins:
      1 an optional item named with "not needed / don't need / do without / skip / no need / without the" -> drop it
      2 a named $ amount below the current price (line-item prices excluded: those are references) -> of the drop and
        concede moves priced at or below the largest such amount, the one that keeps the most margin; none -> once,
        the lowest-priced drop or concede move; then answer
      3 sooner / too late / done by / by <day> / this week / tomorrow / urgent -> the earliest earlier slot, else
        offer_slot
      4 "N weeks" -> the check-in nearest to 7N days; "next month" -> 30; "later this month", "after the holidays",
        "not ready", "not yet" -> 14
      5 family words (wife, husband, partner, landlord, family, talk it over ...) -> ask_why once, then answer
      6 ready words (looks good, when can you, book, just saw, missed ...) -> offer_slot
      7 price words with no amount (expensive, more than, cheaper, budget ...) -> ask_why once, then the smallest
        concession (the highest-priced drop or concede move below the current price)
      8 think / get back / not sure / maybe -> ask_why once, then checkin_7
      9 anything else -> ask_why once, then answer
    Silence (no reply since the shop's last message):
      no reply ever, estimate never opened, not resent yet -> resend on the other channel;
      the shop's last move was a check-in -> offer_slot; else ask_why once, then checkin_7.
    Opt-out is the shared code layer (compliance.opt_out_status), as for every policy.
    """
    name, llm, next_check_days = SMART, False, 3
    arm = _arm_rule

    def act(self, conv, now, hint):  # hint ignored on purpose: this policy reads the words
        menu = _menu(conv, now)
        return _stop_decision(conv, now, self.choose(conv, menu))

    @staticmethod
    def choose(conv, menu):
        return SmartRules.choose_with_branch(conv, menu)[0]

    @staticmethod
    def choose_with_branch(conv, menu):
        """(move, branch): the move of the docstring's rules and which rule chose it (silence_resend, silence_checkin,
        silence, drop, amount, amount_lowest, amount_answer, sooner, later, family, ready, price, think, none)."""
        est, offer = conv.estimate, conv.offer
        by = {m.id: m for m in menu}
        shop_moves = [e.move_id for e in conv.events if e.who == "shop"]
        asked = "ask_why" in shop_moves

        def ask_then(then):  # ask_why the first time, `then` afterwards (ask_why again if `then` is not on the menu)
            return by[then] if asked and then in by else by["ask_why"]

        priced = [m for m in menu if m.kind in ("drop", "concede")]
        last = conv.last_customer_text()
        if last is None:
            if not any(e.who == "customer" for e in conv.events) and conv.views == 0 and offer.channel == est.channel:
                return next((m for m in menu if m.kind == "resend"), by["ask_why"]), "silence_resend"
            if shop_moves and shop_moves[-1].startswith("checkin_"):
                return by["offer_slot"], "silence_checkin"
            return ask_then("checkin_7"), "silence"
        text = last.translate(str.maketrans({"\u2019": "'", "\u2018": "'"})).lower()
        if _NOT_NEEDED.search(text):
            for m in menu:
                extra = m.dropped - offer.dropped
                if m.kind == "drop" and len(extra) == 1 and next(iter(extra)).lower() in text:
                    return m, "drop"
        visible = {i.price for i in est.items}
        asks = [a for a in amounts_in(text) if a < offer.price and a not in visible]
        if asks:
            fits = [m for m in priced if m.price <= max(asks)]
            if fits:
                return max(fits, key=lambda m: (m.price - est.cost(m.dropped), m.price)), "amount"
            lowest = min(priced, key=lambda m: (m.price, m.id), default=None)
            if lowest and lowest.id not in shop_moves:
                return lowest, "amount_lowest"
            return by["answer"], "amount_answer"
        if _SOONER.search(text):
            return next((m for m in menu if m.kind == "earlier_slot"), by["offer_slot"]), "sooner"
        days = None
        w = _WEEKS.search(text)
        if w:
            n = w.group(1)
            days = 7 * (int(n) if n.isdigit() else _NUMBER_WORDS[n])
        else:
            days = next((d for pat, d in _LATER.items() if re.search(pat, text)), None)
        if days is not None:
            checkins = [m for m in menu if m.kind == "checkin"]
            if checkins:
                return min(checkins, key=lambda m: (abs(m.checkin_days - days), m.checkin_days)), "later"
        if _FAMILY.search(text):
            return ask_then("answer"), "family"
        if _READY.search(text):
            return by["offer_slot"], "ready"
        if _PRICE.search(text):
            if not asked:
                return by["ask_why"], "price"
            below = [m for m in priced if m.price < offer.price]
            return (max(below, key=lambda m: (m.price, m.price - est.cost(m.dropped))) if below else by["answer"]), "price"
        if _THINK.search(text):
            return ask_then("checkin_7"), "think"
        return ask_then("answer"), "none"


# v3 (tournament/v3/PREREG.md): the rulebook decides; Nemotron is called only where the rulebook does not know the answer.
UNKNOWN_BRANCHES = ("think", "none")  # SmartRules rules 8 and 9: the customer's words name no reason the rules key on
HYBRID = "rules_then_super"


class RulesThenSuper:
    """smart_rules, except on rules 8 and 9 of a customer's message (think / get back / not sure / maybe, or nothing the
    rules recognise): there the super_only agent (Nemotron Super labels, Super chooses from the same menu) decides.
    Silence, rules 1-7 and a customer the shared opt-out layer has already stopped stay with the rules. Every hand-off
    is logged as a model-less "handoff" step: the rule's branch, the move the rules would have made, and what the agent
    did."""
    name, llm, next_check_days = HYBRID, True, 3

    def __init__(self, client):
        self.agent = Agent(client, LLM_POLICIES["super_only"])

    def act(self, conv, now, hint):  # hint ignored, as in smart_rules
        menu = _menu(conv, now)
        move, branch = SmartRules.choose_with_branch(conv, menu)
        if branch not in UNKNOWN_BRANCHES or conv.recipient.opted_out:
            return _stop_decision(conv, now, move)
        decision = self.agent.decide(conv, now)
        conv.trace.add(Step("handoff", None, note=json.dumps({
            "branch": branch, "rule_move": move.id,
            "model_move": decision.move.id if decision.move is not None else None,
            "stop": decision.stop_reason if decision.move is None else None}, sort_keys=True)))
        return decision

    def arm(self):
        cfg = LLM_POLICIES["super_only"]
        return {"classify": {cfg.classifier_model}, "choose": {CONFIGS[cfg.chooser][0]},
                "label_fallback": {M.SUPER} - {cfg.classifier_model}, "handoff": {None}}


class LLMPolicy:
    llm, next_check_days = True, 3

    def __init__(self, name, client):
        self.name, self.agent = name, Agent(client, LLM_POLICIES[name])

    def act(self, conv, now, hint):  # the coarse act is ignored: the agent reads the messages itself
        return self.agent.decide(conv, now)

    def arm(self):
        """Models this policy may call, per step (integrity counters: any other model is an off-arm call)."""
        cfg = LLM_POLICIES[self.name]
        return {"classify": {cfg.classifier_model}, "choose": {CONFIGS[cfg.chooser][0]},
                "label_fallback": {M.SUPER} - {cfg.classifier_model}}


def make(name, client=None):
    if name == "shop_rule":
        return ShopRule()
    if name == "concession_heuristic":
        return ConcessionHeuristic()
    if name == SMART:
        return SmartRules()
    if name == HYBRID:
        return RulesThenSuper(client)
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
