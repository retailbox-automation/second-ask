"""LIVE: the visitor plays the homeowner; the product as shipped (Super labels, Super chooses) answers through Token
Factory, on one of the replay scenarios' estimates (a seeded demo shop).

Off unless DEMO_LIVE=1 and NEBIUS_API_KEY are set. Limits: LIVE_PER_IP_PER_DAY conversations per visitor IP per UTC day,
MAX_MESSAGES visitor messages per conversation, MAX_CHARS characters per message, and the ledger tag "demo" (its cap in
second_ask.tf_client.DEFAULT_CAPS, $5, shared by every visitor and checked before each call). When the demo budget is
spent, LIVE answers with a plain message and REPLAY keeps working. State is per visitor session (one lock per session);
there is no global lock, so one visitor never waits for another.
"""
import os, secrets, threading, time
from dataclasses import dataclass, field
from datetime import timedelta

from second_ask.agent import Agent, AgentConfig, Event, OwnerTapStub, apply_decision
from second_ask.compliance import Recipient
from second_ask.agent import Conversation
from second_ask.tf_client import BudgetExceeded, Client, Ledger
from second_ask.trace import Trace
from second_ask.tournament import buyers as B

LIVE_PER_IP_PER_DAY = 5
MAX_MESSAGES = 6
MAX_CHARS = 400
SESSION_HOURS = 6
TAG = "demo"


class LiveError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Session:
    id: str
    ip: str
    scenario: str
    conv: object
    created: float
    clock: object                   # simulated time of the next customer message
    messages: int = 0
    pending: object = None          # a decision waiting for the owner's tap
    lock: threading.Lock = field(default_factory=threading.Lock)


def _step(s):
    return {"step": s.step, "model": s.model, "served_model": s.served_model, "seconds": s.seconds, "cost": s.cost,
            "ok": s.ok, "note": s.note, "response_id": s.response_id, "platform_error": s.platform_error}


def _decision(d):
    out = {"stop_reason": d.stop_reason, "flags": d.flags, "needs_owner": d.needs_owner,
           "label": None if d.label is None else {"label": d.label.label, "confidence": d.label.confidence,
                                                  "evidence": d.label.evidence, "model": d.label.model}}
    if d.move is not None:
        out.update(move={"id": d.move.id, "kind": d.move.kind, "description": d.move.description, "price": d.move.price},
                   effect=d.effect, message=d.message, headline=d.headline(),
                   reason=d.choice.reason if d.choice else None)
    return out


class LiveService:
    def __init__(self, scenarios, cards=None, client=None, enabled=None, per_ip=LIVE_PER_IP_PER_DAY, clock=time.time):
        self.enabled = (os.environ.get("DEMO_LIVE") == "1" and bool(os.environ.get("NEBIUS_API_KEY"))) if enabled is None else enabled
        self.scenarios = {s["id"]: s for s in scenarios}
        self.cards = {c.id: c for c in (cards or B.load(B.SEEDS.parent / "tournament" / "buyers.json"))}
        self._client = client
        self.per_ip, self.clock = per_ip, clock
        self.sessions, self.starts = {}, {}
        self._index = threading.Lock()  # guards the two dicts only, never held during a model call

    @property
    def client(self):
        if self._client is None:
            self._client = Client(TAG, ledger=Ledger(), max_attempts=4, fallbacks={})
        return self._client

    def budget(self):
        led = getattr(self.client, "ledger", None) if self.enabled else None
        if led is None:
            return None
        spent = led.spent()[1].get(TAG, 0.0)
        return {"cap_usd": led.caps.get(TAG, 0.0), "spent_usd": round(spent, 4), "left_usd": round(led.caps.get(TAG, 0.0) - spent, 4)}

    def status(self):
        return {"enabled": self.enabled, "per_ip_per_day": self.per_ip, "max_messages": MAX_MESSAGES, "max_chars": MAX_CHARS,
                "models": "Super labels and chooses (the product's default after the tournament)", "budget": self.budget()}

    def _require(self):
        if not self.enabled:
            raise LiveError(403, "Live mode is off here. The recorded replays work without it.")

    def start(self, ip, scenario_id):
        self._require()
        sc = self.scenarios.get(scenario_id)
        if sc is None:
            raise LiveError(404, f"No scenario {scenario_id!r}.")
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        with self._index:
            self._expire()
            n = self.starts.get((ip, day), 0)
            if n >= self.per_ip:
                raise LiveError(429, f"This address has used its {self.per_ip} live conversations for today (UTC). "
                                     "The recorded replays are always available.")
            self.starts[(ip, day)] = n + 1
            card = self.cards[sc["buyer"]]
            est = card.estimate()
            conv = Conversation(est, Recipient(card.tz, card.state), card.open_slots(), trace=Trace(sink=lambda _: None))
            s = Session(secrets.token_urlsafe(16), ip, scenario_id, conv, self.clock(),
                        est.sent_at + timedelta(days=1, hours=1))  # the next morning, inside messaging hours
            self.sessions[s.id] = s
        return {"session": s.id, "scenario": scenario_id, "estimate": sc["estimate"], "messages_left": MAX_MESSAGES}

    def _expire(self):
        cutoff = self.clock() - SESSION_HOURS * 3600
        for k in [k for k, s in self.sessions.items() if s.created < cutoff]:
            del self.sessions[k]

    def _session(self, sid):
        self._require()
        with self._index:
            s = self.sessions.get(sid)
        if s is None:
            raise LiveError(404, "No live conversation for this visitor; start one first.")
        return s

    def _run(self, s, fn):
        before = len(s.conv.trace.steps)
        try:
            out = fn()
        except BudgetExceeded:
            raise LiveError(503, "The live demo has used its budget. The recorded replays still work.") from None
        new = s.conv.trace.steps[before:]
        return out | {"steps": [_step(x) for x in new], "cost_usd": round(sum(x.cost for x in new), 6),
                      "conversation_cost_usd": round(s.conv.trace.cost, 6)}

    def message(self, sid, text):
        s = self._session(sid)
        text = (text or "").strip()
        if not text:
            raise LiveError(400, "Write a message as the homeowner.")
        if len(text) > MAX_CHARS:
            raise LiveError(400, f"Keep it under {MAX_CHARS} characters.")
        with s.lock:
            if s.messages >= MAX_MESSAGES:
                raise LiveError(429, f"This conversation has reached {MAX_MESSAGES} messages. Start another one.")
            if s.pending is not None:
                raise LiveError(409, "The owner has not answered the last proposal yet.")
            if s.conv.status != "open":
                raise LiveError(409, f"This conversation is {s.conv.status}.")
            s.messages += 1
            s.conv.events.append(Event(s.clock, "customer", s.conv.estimate.channel, text))
            now = s.clock + timedelta(minutes=10)
            s.clock += timedelta(hours=1)
            agent = Agent(self.client, AgentConfig())

            def go():
                d = agent.decide(s.conv, now)
                sent = False
                if d.move is not None and d.message is not None:
                    if d.needs_owner:
                        s.pending = d
                    else:
                        sent = apply_decision(s.conv, d, OwnerTapStub())
                return {"decision": _decision(d), "sent": sent, "messages_left": MAX_MESSAGES - s.messages}
            return self._run(s, go)

    def owner(self, sid, approve):
        s = self._session(sid)
        with s.lock:
            if s.pending is None:
                raise LiveError(409, "Nothing is waiting for the owner.")
            d, s.pending = s.pending, None
            sent = apply_decision(s.conv, d, OwnerTapStub(approve=bool(approve)))
            return {"sent": sent, "message": d.message if sent else None, "flags": d.flags}

    def book(self, sid):
        s = self._session(sid)
        with s.lock:
            last = next((e for e in reversed(s.conv.events) if e.who == "shop"), None)
            if last is None or s.conv.status not in ("open", "booked"):
                raise LiveError(409, "There is no offer to book yet.")
            agent = Agent(self.client, AgentConfig())
            return self._run(s, lambda: {"job": agent.book(s.conv, s.clock)})
