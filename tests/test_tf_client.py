import email.message, io, json, threading, urllib.error

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
