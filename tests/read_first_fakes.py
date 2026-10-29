"""Scripted Token Factory replies for the read-first path (built on tests/fakes.py)."""
from fakes import call, raw

from second_read.readers import nemotron as N
from read_first import writer as W

EMPTY = {"stop": False, "ceiling_usd": None, "drop_items": [], "need_by": None, "wait_weeks": None, "decider": None,
         "ready": False}


def facts(**kw):
    """A record_facts tool call with the contract's keys (unset ones empty)."""
    return raw(tool_calls=[call(N.TOOL_NAME, {**EMPTY, **kw})], rid="read-1")


def text(message):
    return raw(tool_calls=[call(W.TOOL_NAME, {"message": message})], rid="write-1", p=900, c=50)
