import json, threading, time

import pytest

from second_ask import tf_client as tf
from second_ask.tf_client import BudgetExceeded


def _call(name, args):
    return {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeTF:
    """A thread-safe stand-in for Token Factory: labels every message `label`, chooses `move` when the menu offers it
    (else the first offer to book), and quotes the customer's latest words back so each visitor can recognise its own
    conversation. Records how many calls ran at the same time."""

    def __init__(self, label="price", move="concede_pkg_5", delay=0.0, budget_left=None):
        self.label, self.move, self.delay = label, move, delay
        self.budget_left = budget_left  # None: unlimited; otherwise calls allowed before BudgetExceeded
        self.lock, self.active, self.peak, self.calls = threading.Lock(), 0, 0, []
        self.ledger = None

    def chat(self, model, messages, **params):
        with self.lock:
            if self.budget_left is not None:
                if self.budget_left <= 0:
                    raise BudgetExceeded("[demo] cap")
                self.budget_left -= 1
            self.calls.append(model)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delay)
            names = [t["function"]["name"] for t in params.get("tools", [])]
            user = messages[-1]["content"]
            if "label_reply" in names:
                body = {"tool_calls": [_call("label_reply", {"label": self.label, "confidence": "high", "evidence": "said so"})]}
            elif "choose_move" in names:
                enum = params["tools"][0]["function"]["parameters"]["properties"]["move_id"]["enum"]
                move = self.move if self.move in enum else next((m for m in enum if m.startswith(("offer", "drop"))), enum[0])
                said = user.split("Customer: ")[-1].split("\n")[0][:60].replace("$", "")
                body = {"tool_calls": [_call("choose_move", {"move_id": move, "message": f"About '{said}': we can help.",
                                                             "reason": "fake"})]}
            else:
                body = {"content": "No booking in this fake."}
            raw = {"id": f"fake-{len(self.calls)}", "model": model,
                   "choices": [{"message": {"role": "assistant", "content": body.get("content"), **body},
                                "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "completion_tokens_details": {"reasoning_tokens": 0}}}
            return tf._parse(model, raw, 0.01, *tf.PRICES[model])
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture
def fake_tf():
    return FakeTF()
