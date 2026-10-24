"""One log line per agent step: model, latency, tokens (reasoning included), $ and the Token Factory response id.

A Trace belongs to one conversation (one visitor in the demo); nothing here is global.
"""
import json, sys
from dataclasses import asdict, dataclass


@dataclass
class Step:
    step: str
    model: str | None
    seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0
    ok: bool = True
    note: str = ""
    response_id: str | None = None
    served_model: str | None = None  # the model id the server says answered (`model` is the one we asked for)
    platform_error: bool = False     # no answer: Token Factory failed after the client's retries (TFError)

    @classmethod
    def from_reply(cls, step, reply, ok=True, note=""):
        u, raw = reply.usage or {}, reply.raw or {}
        return cls(step, reply.model, reply.seconds, u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                   reply.reasoning_tokens, round(reply.cost, 6), ok, note, raw.get("id"), raw.get("model"))

    @classmethod
    def platform_failure(cls, step, model, error):
        return cls(step, model, ok=False, note=str(error)[:200], platform_error=True)


class Trace:
    def __init__(self, sink=None):
        self.steps = []
        self.sink = sink if sink is not None else (lambda line: print(line, file=sys.stderr))

    def add(self, step):
        self.steps.append(step)
        if self.sink:
            self.sink(json.dumps(asdict(step)))
        return step

    @property
    def cost(self):
        return sum(s.cost for s in self.steps)
