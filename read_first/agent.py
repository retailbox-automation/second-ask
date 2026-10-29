"""ReadFirstAgent: code decides the price, Nemotron reads the customer and writes the words.

For a customer reply:
  1. opt-out gate in code (`second_ask.compliance.opt_out_status`), before any model call;
  2. read: Nemotron 3 Super reads the reply into Facts with the frozen v4 reader (`second_read.readers.nemotron`);
     a read of "stop" stops the conversation;
  3. decide: `second_read.offer.offer()` turns the Facts into an offer; `read_first.decide.map_offer` finds the menu
     move that carries it (code default, flagged, when none does);
  4. write: Nemotron 3 Super writes the text around that move; amounts, percents and dates are checked by code and
     any mismatch gives the template (`read_first.writer`);
  5. compliance gate, disclosure, $ effect: `second_ask.agent.make_decision`;
  6. owner: one tap for a concession below list (`apply`, through `second_ask.agent.apply_decision`);
  7. book: the Super tool loop of `second_ask.agent.Agent.book`.
A silence (no customer reply since the shop's last message) is decided by SmartRules' silence branches and sent with
the template: no model is called.

Every stage is logged twice: as trace steps (one per model call, `second_ask.trace.Step`; model None for code) and as
one `Stage` per stage with its seconds and $ (the demo's model strip). The old path, `second_ask.agent.Agent`, is left
as it was.
"""
import json, time
from dataclasses import asdict, dataclass, field

from second_ask import models as M
from second_ask.agent import Agent, AgentConfig, Decision, _stop, apply_decision, make_decision
from second_ask.chooser import default_move
from second_ask.compliance import opt_out_status
from second_ask.menu import amounts_in, template_message
from second_ask.trace import Step
from second_read.offer import offer as decide_offer

from . import reader as R
from .decide import map_offer, menu_for, silence_move
from .writer import write

ACTORS = {M.SUPER: "Nemotron 3 Super", M.ULTRA: "Nemotron 3 Ultra", M.LIGHTNING: "Nemotron 3.5 Lightning",
          M.SUPER_VL: "Nemotron 3.5 Super-VL"}


def actor(model):
    return "code" if model is None else ACTORS.get(model, model)


@dataclass(frozen=True)
class ReadFirstConfig:
    reader_model: str = M.SUPER
    reader_method: str = "tool"
    writer_model: str = M.SUPER
    booker_model: str = M.SUPER
    shop_state: str = "FL"


@dataclass
class Stage:
    stage: str              # read, decide, write, owner, book
    actor: str              # "Nemotron 3 Super", "code", "owner tap"
    model: str | None       # the model id asked, None for code and the owner
    seconds: float = 0.0
    usd: float = 0.0
    ok: bool = True
    note: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class ReadFirstDecision:
    decision: Decision
    path: str                       # "reply", "silence" or "stopped"
    facts: object = None            # second_read.contract.Facts read from the reply
    offer: dict | None = None       # second_read.offer.offer() for those facts
    mapping: object = None          # read_first.decide.Mapping
    written: object = None          # read_first.writer.Written
    branch: str | None = None       # SmartRules silence branch
    read_meta: dict | None = None
    stages: list = field(default_factory=list)

    @property
    def move(self):
        return self.decision.move

    @property
    def message(self):
        return self.decision.message

    @property
    def needs_owner(self):
        return self.decision.needs_owner

    @property
    def flags(self):
        return self.decision.flags

    @property
    def stop_reason(self):
        return self.decision.stop_reason

    def to_dict(self):
        d = self.decision
        out = {"path": self.path, "stop_reason": d.stop_reason, "flags": list(d.flags), "needs_owner": d.needs_owner,
               "facts": self.facts.to_dict() if self.facts is not None else None, "offer": self.offer,
               "branch": self.branch, "stages": [s.to_dict() for s in self.stages]}
        if self.mapping is not None:
            out["mapping"] = {"exact": self.mapping.exact, "note": self.mapping.note}
        if self.written is not None:
            out["writer"] = {"template_used": self.written.template_used, "problems": self.written.problems,
                             "model": self.written.model}
        if d.move is not None:
            m = d.move
            out["move"] = {"id": m.id, "kind": m.kind, "description": m.description, "price": m.price,
                           "dropped": sorted(m.dropped), "slot": m.slot.start.isoformat() if m.slot else None,
                           "checkin_days": m.checkin_days, "needs_owner": m.needs_owner}
            out.update(effect=d.effect, message=d.message, send_at=d.send_at.isoformat() if d.send_at else None,
                       gate={"ok": d.message is not None, "send_at": d.send_at.isoformat() if d.send_at else None,
                             "blocked": d.stop_reason})
        return out


class _Recorder:
    """Passes calls through and keeps every reply, so each read call gets its own trace step (id, tokens, $)."""

    def __init__(self, client):
        self.client, self.replies = client, []

    def chat(self, model, messages, **params):
        reply = self.client.chat(model, messages, **params)
        self.replies.append(reply)
        return reply


class ReadFirstAgent:
    def __init__(self, client, config=ReadFirstConfig()):
        self.client, self.cfg = client, config

    # --- decide -------------------------------------------------------------------------------------------------
    def decide(self, conv, now):
        if conv.recipient.opted_out:
            return self._stopped("recipient opted out; no further messages")
        if conv.status != "open":
            return self._stopped(f"conversation is {conv.status}")
        last = conv.last_customer_text()
        if last is None:
            return self._silence(conv, now)
        status = opt_out_status(last)
        if status == "opt_out":
            conv.recipient.opted_out, conv.status = True, "opted_out"
            return self._stopped("customer asked to stop (opt-out keyword); no further messages")
        flags = ["an opt-out word appears inside a sentence: owner should glance at it"] if status == "ambiguous" else []
        rd = ReadFirstDecision(None, "reply")
        facts, meta = self._read(conv, last, now, rd)
        rd.facts, rd.read_meta = facts, meta
        if facts is None:
            t0 = time.perf_counter()
            menu = menu_for(conv, now)
            move = default_move(None, menu)
            flags.append(f"read failed ({meta['error_kind']}: {meta['error']}); code default {move.id} used")
            rd.decision = make_decision(conv, move, template_message(move, conv.estimate, conv.offer), now,
                                        self.cfg.shop_state, menu=menu, flags=flags)
            self._decided(conv, rd, t0, {"read": "failed", "move": move.id})
            return rd
        if facts.stop:
            conv.recipient.opted_out, conv.status = True, "opted_out"
            rd.decision = _stop("the reader read a request to stop; no further messages", flags=flags)
            rd.path = "stopped"
            self._decided(conv, rd, time.perf_counter(), {"stop": True})
            return rd
        t0 = time.perf_counter()
        rd.offer = decide_offer(facts, R.ConversationCard(conv), R.today_of(now))
        menu = menu_for(conv, now)
        rd.mapping = map_offer(rd.offer, conv, menu, now, facts)
        if not rd.mapping.exact:
            flags.append(rd.mapping.note)
        self._decided(conv, rd, t0, {"offer": rd.offer, "move": rd.mapping.move.id, "exact": rd.mapping.exact})
        said = [a for e in conv.events if e.who == "customer" for a in amounts_in(e.text)]
        rd.written = write(self.client, conv, rd.mapping.move, now, conv.trace, said, facts, rd.offer["timing"],
                           self.cfg.writer_model)
        w = rd.written
        rd.stages.append(Stage("write", actor(self.cfg.writer_model), self.cfg.writer_model, w.seconds, round(w.usd, 6),
                               not w.template_used, "; ".join(w.problems)[:200] or "model text passed the checks"))
        if w.template_used:
            flags.append(f"model's message replaced by the template: {w.problems}")
        rd.decision = make_decision(conv, rd.mapping.move, w.text, now, self.cfg.shop_state, menu=menu, flags=flags)
        return rd

    def _read(self, conv, text, now, rd):
        rec = _Recorder(self.client)
        facts, meta = R.read(rec, conv, text, now, self.cfg.reader_model, self.cfg.reader_method)
        for reply, attempt in zip(rec.replies, meta["attempts"]):
            conv.trace.add(Step.from_reply("read", reply, ok=attempt.get("problem") is None,
                                           note=attempt.get("problem") or "facts passed the contract"))
        if meta["error_kind"] == "platform":
            conv.trace.add(Step.platform_failure("read", self.cfg.reader_model, meta["error"]))
        note = json.dumps(facts.to_dict(), sort_keys=True) if facts is not None else f"{meta['error_kind']}: {meta['error']}"
        rd.stages.append(Stage("read", actor(self.cfg.reader_model), self.cfg.reader_model, meta["seconds"],
                               round(meta["usd"], 6), facts is not None, note[:300]))
        return facts, meta

    def _decided(self, conv, rd, t0, what):
        secs = round(time.perf_counter() - t0, 6)
        note = json.dumps(what, sort_keys=True, default=str)
        conv.trace.add(Step("decide", None, secs, note=note[:300]))
        rd.stages.append(Stage("decide", "code", None, secs, 0.0, True, note[:300]))

    def _silence(self, conv, now):
        t0 = time.perf_counter()
        menu = menu_for(conv, now)
        move, branch = silence_move(conv, menu)
        rd = ReadFirstDecision(None, "silence", branch=branch)
        rd.decision = make_decision(conv, move, template_message(move, conv.estimate, conv.offer), now,
                                    self.cfg.shop_state, menu=menu, flags=[f"silence: rule branch {branch}"])
        self._decided(conv, rd, t0, {"silence": branch, "move": move.id})
        return rd

    @staticmethod
    def _stopped(reason):
        return ReadFirstDecision(_stop(reason), "stopped")

    # --- owner and booking ----------------------------------------------------------------------------------------
    def apply(self, conv, rd, owner):
        """The owner's tap for a concession, then the message is recorded as sent (second_ask.agent.apply_decision)."""
        d = rd.decision
        if d.move is None or d.message is None:
            return False
        if not d.needs_owner:
            rd.stages.append(Stage("owner", "owner tap", None, 0.0, 0.0, True, "not needed: no concession below list"))
            return apply_decision(conv, d, owner)

        def tap(c, move):
            t0 = time.perf_counter()
            ok = owner(c, move)
            secs = round(time.perf_counter() - t0, 6)
            note = f"{'approved' if ok else 'declined'} {move.id} at ${move.price:,.0f}"
            c.trace.add(Step("owner", None, secs, ok=bool(ok), note=note))
            rd.stages.append(Stage("owner", "owner tap", None, secs, 0.0, bool(ok), note))
            return ok
        return apply_decision(conv, d, tap)

    def book(self, conv, now, rd=None, max_rounds=8):
        """The Super tool loop of second_ask.agent.Agent.book; adds one "book" stage to `rd` when given."""
        before = len(conv.trace.steps)
        job = Agent(self.client, AgentConfig(booker_model=self.cfg.booker_model)).book(conv, now, max_rounds)
        steps = conv.trace.steps[before:]
        if rd is not None:
            rd.stages.append(Stage("book", actor(self.cfg.booker_model), self.cfg.booker_model,
                                   round(sum(s.seconds for s in steps), 3), round(sum(s.cost for s in steps), 6),
                                   job is not None, f"{len(steps)} call(s); " + (f"job {job['job_id']}" if job else "not booked")))
        return job
