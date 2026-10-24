import email.message, http.client, io, json, ssl, threading, urllib.error
from pathlib import Path

import pytest

from second_ask import tf_client as tf

SUPER = "nvidia/nemotron-3-super-120b-a12b"
ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
RETIRED = "nvidia/Llama-3_1-Nemotron-Ultra-253B-v1"


def reply(content=None, reasoning=None, tool_calls=None, finish="stop", p=1000, c=100, rt=0):
    msg = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning"] = reasoning
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": p, "completion_tokens": c, "completion_tokens_details": {"reasoning_tokens": rt}}}


class FakeHTTP:
    """Scripted urlopen: each item is a response dict, an (HTTP status, headers) tuple, or an exception."""

    def __init__(self, script):
        self.script, self.requests = list(script), []

    def __call__(self, req, timeout=None):
        self.requests.append(json.loads(req.data))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, tuple):
            status, headers = item
            hdrs = email.message.Message()
            for k, v in headers.items():
                hdrs[k] = v
            raise urllib.error.HTTPError(req.full_url, status, "err", hdrs, io.BytesIO(b'{"detail":"x"}'))
        return io.BytesIO(json.dumps(item).encode())


@pytest.fixture
def ledger(tmp_path):
    return tf.Ledger(tmp_path / "ledger.jsonl", caps={"total": 1.0, "b2": 1.0})


def client(ledger, monkeypatch, script, **kw):
    fake = FakeHTTP(script)
    monkeypatch.setattr(tf.urllib.request, "urlopen", fake)
    monkeypatch.setattr(tf.time, "sleep", lambda s: None)
    return tf.Client("b2", ledger=ledger, key="test-key", log=kw.pop("log", lambda m: None), **kw), fake


def test_text_falls_back_to_reasoning_when_content_is_null(ledger, monkeypatch):
    c, _ = client(ledger, monkeypatch, [reply(content=None, reasoning="price", rt=40)])
    r = c.chat(SUPER, [{"role": "user", "content": "hi"}], max_tokens=512)
    assert (r.text, r.content, r.from_reasoning, r.reasoning_tokens) == ("price", "", True, 40)
    assert r.cost == pytest.approx((1000 * 0.30 + 100 * 0.90) / 1e6)
    assert ledger.spent()[0] == pytest.approx(r.cost)  # reservation fully settled to the real cost


def test_reasoning_is_not_text_when_the_model_called_a_tool(ledger, monkeypatch):
    tc = [{"id": "1", "type": "function", "function": {"name": "book_slot", "arguments": ""}}]
    c, _ = client(ledger, monkeypatch, [reply(content=None, reasoning="thinking", tool_calls=tc)])
    r = c.chat(SUPER, [{"role": "user", "content": "book"}], max_tokens=512)
    assert r.text == "" and not r.from_reasoning
    assert tf.tool_args(r.tool_calls[0]) == {}
    assert r.raw["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == ""  # evidence keeps the server's ""


def test_length_finish_is_flagged(ledger, monkeypatch):
    c, _ = client(ledger, monkeypatch, [reply(content=None, reasoning="...", finish="length")])
    assert c.chat(SUPER, [], max_tokens=64).truncated


def test_retired_model_falls_back_on_404(ledger, monkeypatch):
    tf.PRICES.setdefault(RETIRED, (1.0, 3.0))
    logs = []
    c, fake = client(ledger, monkeypatch, [(404, {}), reply(content="ok")], fallbacks={RETIRED: [SUPER]}, log=logs.append)
    r = c.chat(RETIRED, [], max_tokens=64)
    assert r.model == SUPER and [q["model"] for q in fake.requests] == [RETIRED, SUPER]
    assert "404" in logs[0]
    assert ledger.spent()[0] == pytest.approx(r.cost)  # the 404 reservation was released


def test_429_is_retried_after_the_server_says(ledger, monkeypatch):
    slept = []
    c, fake = client(ledger, monkeypatch, [(429, {"Retry-After": "7"}), reply(content="ok")])
    monkeypatch.setattr(tf.time, "sleep", slept.append)
    assert c.chat(SUPER, [], max_tokens=64).content == "ok"
    assert slept == [7.0] and len(fake.requests) == 2


def test_400_is_not_retried(ledger, monkeypatch):
    c, fake = client(ledger, monkeypatch, [(400, {})])
    with pytest.raises(tf.TFError) as e:
        c.chat(SUPER, [], max_tokens=64)
    assert e.value.status == 400 and len(fake.requests) == 1 and ledger.spent()[0] == pytest.approx(0)


def test_cap_blocks_the_call_before_anything_is_sent(tmp_path, monkeypatch):
    tiny = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 0.001, "b2": 0.001})
    c, fake = client(tiny, monkeypatch, [reply(content="never")])
    with pytest.raises(tf.BudgetExceeded):
        c.chat(ULTRA, [], max_tokens=16000)
    assert fake.requests == []


def test_total_cap_binds_even_when_the_tag_has_room(tmp_path):
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 0.03, "b2": 0.05, "c1": 0.05})
    led.reserve("b2", 0.02, "t")
    with pytest.raises(tf.BudgetExceeded):
        led.reserve("c1", 0.02, "t")  # c1 has $0.05 of room, but the two lanes together would pass $0.03
    led.reserve("c1", 0.01, "t")


def test_unknown_tag_has_no_budget(tmp_path, monkeypatch):
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 10.0, "c1": 5.0})
    c, fake = client(led, monkeypatch, [reply(content="never")])  # client tag is "b2"
    with pytest.raises(tf.BudgetExceeded):
        c.chat(SUPER, [], max_tokens=64)
    assert fake.requests == []


def test_unpriced_model_is_refused(ledger, monkeypatch):
    c, fake = client(ledger, monkeypatch, [reply(content="never")])
    with pytest.raises(tf.TFError):
        c.chat("vendor/unknown-model", [], max_tokens=64)
    assert fake.requests == []


def test_timeouts_keep_their_reservations(ledger, monkeypatch):
    c, fake = client(ledger, monkeypatch, [TimeoutError("t")] * 2, max_attempts=2)
    with pytest.raises(tf.TFError):
        c.chat(SUPER, [], max_tokens=1000)
    reserve = (len(json.dumps({"model": SUPER, "messages": [], "max_tokens": 1000})) / 3 * 0.30 + 1000 * 0.90) / 1e6
    assert ledger.spent()[0] == pytest.approx(2 * reserve)


def test_ledger_cap_holds_under_concurrency(tmp_path):
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 0.05, "b2": 0.05})
    ok, refused = [], []

    def worker():
        try:
            led.reserve("b2", 0.01, "t")
            ok.append(1)
        except tf.BudgetExceeded:
            refused.append(1)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert (len(ok), len(refused)) == (5, 15)
    assert led.spent()[0] == pytest.approx(0.05)


class _WithHeaders(io.BytesIO):
    def __init__(self, data, headers):
        super().__init__(data)
        self.headers = headers


def test_rate_limit_headers_are_kept_and_others_dropped(ledger, monkeypatch):
    c, fake = client(ledger, monkeypatch, [])
    body = json.dumps(reply(content="ok")).encode()
    hdrs = {"X-RateLimit-Remaining-Tokens": "199000", "Retry-After": "3", "Set-Cookie": "secret"}
    monkeypatch.setattr(tf.urllib.request, "urlopen", lambda req, timeout=None: _WithHeaders(body, hdrs))
    r = c.chat(SUPER, [], max_tokens=64)
    assert r.headers == {"x-ratelimit-remaining-tokens": "199000", "retry-after": "3"}


def test_n_choices_reserve_n_outputs(tmp_path, monkeypatch):
    one = (len(json.dumps({"model": SUPER, "messages": [], "max_tokens": 1000, "n": 3})) / 3 * 0.30 + 1000 * 0.90) / 1e6
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 2 * one, "b2": 2 * one})  # room for one output, not three
    c, fake = client(led, monkeypatch, [reply(content="never")])
    with pytest.raises(tf.BudgetExceeded):
        c.chat(SUPER, [], max_tokens=1000, n=3)
    assert fake.requests == []


class _CutOff(io.BytesIO):
    """A response whose body breaks while it is read (the server closed mid-body)."""
    def read(self, *a):
        raise http.client.IncompleteRead(b'{"choi', 200)


def _reserve(max_tokens=1000):
    return (len(json.dumps({"model": SUPER, "messages": [], "max_tokens": max_tokens})) / 3 * 0.30 + max_tokens * 0.90) / 1e6


@pytest.mark.parametrize("failure", [http.client.RemoteDisconnected("Remote end closed connection without response"),
                                     ConnectionResetError(54, "Connection reset by peer"),
                                     ssl.SSLError("record layer failure"), TimeoutError("timed out"),
                                     urllib.error.URLError("no route")])
def test_network_failures_are_retried_and_keep_their_reservation(ledger, monkeypatch, failure):
    """MF2a: on Python 3.14 RemoteDisconnected is not a URLError; every one of these must be retried, not raised."""
    c, fake = client(ledger, monkeypatch, [failure, reply(content="ok")])
    r = c.chat(SUPER, [], max_tokens=1000)
    assert r.content == "ok" and len(fake.requests) == 2
    assert ledger.spent()[0] == pytest.approx(_reserve() + r.cost)  # the failed attempt may have been billed


def test_a_body_cut_off_while_reading_is_retried(ledger, monkeypatch):
    seq = [_CutOff(b""), io.BytesIO(json.dumps(reply(content="ok")).encode())]
    monkeypatch.setattr(tf.urllib.request, "urlopen", lambda req, timeout=None: seq.pop(0))
    monkeypatch.setattr(tf.time, "sleep", lambda s: None)
    c = tf.Client("b2", ledger=ledger, key="k", log=lambda m: None)
    assert c.chat(SUPER, [], max_tokens=1000).content == "ok" and not seq


@pytest.mark.parametrize("body", [b"{not json", b"", json.dumps({"choices": []}).encode(),
                                  json.dumps({"error": {"message": "x"}}).encode(), json.dumps([1, 2]).encode(),
                                  json.dumps({"choices": [{"message": "text"}]}).encode()])
def test_malformed_replies_are_retried_then_raise_a_tferror(ledger, monkeypatch, body):
    seq = [io.BytesIO(body), io.BytesIO(body)]
    monkeypatch.setattr(tf.urllib.request, "urlopen", lambda req, timeout=None: seq.pop(0))
    monkeypatch.setattr(tf.time, "sleep", lambda s: None)
    c = tf.Client("b2", ledger=ledger, key="k", max_attempts=2, log=lambda m: None)
    with pytest.raises(tf.TFError):
        c.chat(SUPER, [], max_tokens=1000)
    assert not seq and ledger.spent()[0] == pytest.approx(2 * _reserve())


def test_every_attempt_failing_ends_in_a_tferror_not_the_raw_exception(ledger, monkeypatch):
    c, fake = client(ledger, monkeypatch, [http.client.RemoteDisconnected("x")] * 3, max_attempts=3)
    with pytest.raises(tf.TFError) as e:
        c.chat(SUPER, [], max_tokens=1000)
    assert "RemoteDisconnected" in str(e.value) and len(fake.requests) == 3


def test_budget_exceeded_is_not_a_tferror_so_no_handler_absorbs_it():
    assert not issubclass(tf.BudgetExceeded, tf.TFError)
    from fakes import FakeClient
    from second_ask import classifier as CL
    from second_ask.trace import Trace
    with pytest.raises(tf.BudgetExceeded):
        CL.classify(FakeClient([tf.BudgetExceeded("cap")]), SUPER, "state", Trace(sink=lambda _: None))


def test_a_reply_without_usage_keeps_its_reservation(ledger, monkeypatch):
    body = reply(content="ok")
    del body["usage"]
    c, _ = client(ledger, monkeypatch, [body])
    r = c.chat(SUPER, [], max_tokens=1000)
    assert r.cost == 0 and ledger.spent()[0] == pytest.approx(_reserve())  # not counted as free


def test_a_broken_ledger_line_is_skipped_and_not_glued_to_the_next(tmp_path, capsys):
    path = tmp_path / "l.jsonl"
    path.write_text('{"ts": 1, "tag": "b2", "usd": 0.5, "note": "x"}\n{"ts": 2, "tag": "b2", "us')  # killed mid-line
    led = tf.Ledger(path, caps={"total": 1.0, "b2": 1.0})
    led.reserve("b2", 0.25, "after the crash")
    assert led.spent() == (pytest.approx(0.75), {"b2": pytest.approx(0.75)})
    lines = path.read_text().splitlines()
    assert json.loads(lines[-1])["note"] == "after the crash" and len(lines) == 3
    assert "unreadable" in capsys.readouterr().err


SPIKES = Path(__file__).resolve().parents[1] / "evidence" / "spikes"


def test_parser_reads_every_recorded_spike_reply():
    """S5 of the playbook (reasoning / reasoning_content field): the parser re-reads every recorded raw reply of the
    platform spikes, all seven models, and gives back what the spike recorded."""
    seen, models = 0, set()
    for f in sorted(SPIKES.glob("s[1-7]*/raw.jsonl")):
        for line in f.read_text().splitlines():
            rec = json.loads(line)
            if not rec.get("ok") or not rec.get("raw"):
                continue
            r = tf._parse(rec["model"], rec["raw"], 0.0, *tf.PRICES[rec["model"]])
            assert (r.content, r.reasoning, r.finish_reason) == (rec["content"], rec["reasoning"], rec["finish_reason"]), rec["id"]
            assert r.tool_calls == rec["tool_calls"] and r.cost == pytest.approx(rec["cost"]), rec["id"]
            seen += 1
            models.add(rec["model"])
    assert seen >= 600 and len(models) >= 6


def test_token_factory_reports_the_model_it_was_asked_for():
    """The off-arm counter compares the reply's `model` with the requested id; on every recorded reply they match
    exactly, so a mismatch in the tournament means a real swap, not a spelling difference."""
    pairs = set()
    for f in sorted(SPIKES.glob("s[1-7]*/raw.jsonl")):
        for line in f.read_text().splitlines():
            rec = json.loads(line)
            if rec.get("ok") and rec.get("raw"):
                pairs.add((rec["model"], rec["raw"].get("model")))
    assert pairs and all(asked == served for asked, served in pairs), pairs
