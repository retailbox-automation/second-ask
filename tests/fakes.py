"""Scripted stand-ins for Token Factory replies, built through the real response parser."""
import json

from second_ask import tf_client as tf


def raw(content=None, tool_calls=None, p=800, c=60, rt=0, rid="resp-1"):
    msg = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {"id": rid, "choices": [{"message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"prompt_tokens": p, "completion_tokens": c, "completion_tokens_details": {"reasoning_tokens": rt}}}


def call(name, args, cid="call-1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}


def label(lbl, conf="high", ev="customer said so"):
    return raw(tool_calls=[call("label_reply", {"label": lbl, "confidence": conf, "evidence": ev})])


def choice(move_id, message="Sounds good, we can help.", reason="fits what they said"):
    return raw(tool_calls=[call("choose_move", {"move_id": move_id, "message": message, "reason": reason})])


class FakeClient:
    """chat() pops the next scripted raw response (or raises a scripted exception) and parses it like the real client."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def chat(self, model, messages, **params):
        self.calls.append({"model": model, "messages": list(messages), "params": params})  # snapshot: callers keep appending
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        p_in, p_out = tf.PRICES[model]
        return tf._parse(model, item, 0.5, p_in, p_out)
