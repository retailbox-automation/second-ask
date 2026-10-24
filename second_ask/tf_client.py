"""Nebius Token Factory client (OpenAI-compatible chat completions).

Handles the Nemotron quirks we hit in practice: text arriving in `reasoning` with `content: null`,
`finish_reason == "length"` when reasoning eats the budget, empty tool-call arguments, retired model IDs.
Every call reserves its worst-case cost in a shared append-only ledger before it is sent, so a hard
spend cap holds across processes and threads. Token Factory has no spend limit of its own.

Failures: HTTP 429/5xx are retried and their reservation released (error replies are not billed). A dropped
connection, a TLS error, a cut-off or non-JSON body, or a reply without choices is retried with its reservation kept,
because the request may still have been billed. After the last attempt every one of them is a TFError, so callers
handle one exception type. BudgetExceeded is deliberately not a TFError: it must stop a run, not be absorbed.
"""
import fcntl, http.client, json, os, sys, time, urllib.error, urllib.request
from dataclasses import dataclass, field
from pathlib import Path

API = os.environ.get("TF_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
# USD per 1M tokens (input, output) from tokenfactory.nebius.com/api/public/models_info, 2026-10-23.
PRICES = {
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
    "nvidia/Nemotron-3_5-Lightning": (0.06, 0.24),
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
    "nvidia/Nemotron-3.5-Super-VL": (0.30, 0.90),
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 3.00),
    "Qwen/Qwen3-30B-A3B-Instruct-2507": (0.10, 0.30),
    "Qwen/Qwen3-235B-A22B-Instruct-2507": (0.20, 0.60),
    "deepseek-ai/DeepSeek-V4-Flash-0731": (0.14, 0.28),
    "openai/gpt-oss-120b": (0.15, 0.60),
}
DEFAULT_CAPS = {"total": 40.0, "b2": 20.0, "c1": 15.0, "demo": 5.0}


class BudgetExceeded(RuntimeError):
    pass


class TFError(RuntimeError):
    def __init__(self, status, detail):
        super().__init__(f"Token Factory error {status}: {detail}")
        self.status = status


@dataclass
class Reply:
    model: str
    content: str          # message.content ("" when null)
    reasoning: str        # message.reasoning or message.reasoning_content
    text: str             # content; falls back to reasoning only when there are no tool calls
    from_reasoning: bool  # text was taken from a reasoning field
    tool_calls: list
    finish_reason: str
    usage: dict
    cost: float
    seconds: float
    raw: dict
    headers: dict = field(default_factory=dict)  # x-ratelimit-* and retry-after response headers, lower-cased

    @property
    def truncated(self):
        return self.finish_reason == "length"

    @property
    def reasoning_tokens(self):
        return (self.usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0


def tool_args(tool_call):
    """Parsed arguments of one tool call; Super sends "" for tools without arguments."""
    return json.loads(tool_call["function"].get("arguments") or "{}")


class Ledger:
    """Append-only JSONL spend log shared by every process on this machine (flock-guarded)."""

    def __init__(self, path=None, caps=None):
        self.path = Path(path or os.environ.get("TF_LEDGER", "~/.local/state/nebius-hackathon/tf-ledger.jsonl")).expanduser()
        self.caps = caps or json.loads(os.environ.get("TF_CAPS", "null")) or DEFAULT_CAPS
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.unreadable_lines = 0  # last count of broken lines seen (reported once per change, not per call)

    def _write(self, tag, usd, note, check):
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                _terminate_last_line(f)  # a line cut off by a killed writer must not swallow this one
                if check:
                    total, by_tag = self._sum(f)
                    if total + usd > self.caps["total"] or by_tag.get(tag, 0.0) + usd > self.caps.get(tag, 0.0):
                        raise BudgetExceeded(f"[{tag}] ${usd:.4f} would pass the cap (spent ${total:.4f} total, "
                                             f"${by_tag.get(tag, 0.0):.4f} on {tag}; caps {self.caps})")
                f.write(json.dumps({"ts": round(time.time(), 3), "tag": tag, "usd": usd, "note": note}) + "\n")
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _sum(self, f):
        """Totals over every readable line. A broken line (a writer killed mid-line) is skipped and reported on stderr:
        it holds at most one reservation, and refusing every later call over it would stop all lanes."""
        f.seek(0)
        total, by_tag, bad = 0.0, {}, 0
        for line in f:
            if line.strip():
                try:
                    r = json.loads(line)
                    usd, tag = float(r["usd"]), r["tag"]
                except (ValueError, KeyError, TypeError):
                    bad += 1
                    continue
                total += usd
                by_tag[tag] = by_tag.get(tag, 0.0) + usd
        if bad != self.unreadable_lines:
            print(f"[ledger] skipping {bad} unreadable line(s) in {f.name}", file=sys.stderr)
            self.unreadable_lines = bad
        return total, by_tag

    def reserve(self, tag, usd, note):
        self._write(tag, usd, note, check=True)

    def settle(self, tag, usd, note):
        self._write(tag, usd, note, check=False)

    def spent(self):
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return self._sum(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


class Client:
    def __init__(self, tag, ledger=None, key=None, fallbacks=None, timeout=300, max_attempts=5, log=print):
        self.tag, self.ledger = tag, ledger or Ledger()
        self.key = key or os.environ["NEBIUS_API_KEY"]
        self.fallbacks = fallbacks or {}  # {"model-id": ["fallback-id", ...]}, tried in order on HTTP 404
        self.timeout, self.max_attempts, self.log = timeout, max_attempts, log

    def chat(self, model, messages, **params):
        chain = [model] + self.fallbacks.get(model, [])
        for m, nxt in zip(chain, chain[1:] + [None]):
            try:
                return self._call(m, messages, params)
            except TFError as e:
                if e.status != 404 or nxt is None:
                    raise
                self.log(f"[tf] {m} returned 404, falling back to {nxt}")
        raise AssertionError("unreachable")

    def _call(self, model, messages, params):
        if model not in PRICES:
            raise TFError(None, f"no price for {model}; add it to PRICES before calling")
        p_in, p_out = PRICES[model]
        body = {"model": model, "messages": messages, **params}
        est_in = len(json.dumps(body)) / 3  # generous: ~3 characters per token
        reserve = (est_in * p_in + params.get("max_tokens", 8192) * params.get("n", 1) * p_out) / 1e6  # n choices bill n outputs
        for attempt in range(self.max_attempts):
            self.ledger.reserve(self.tag, reserve, f"reserve {model}")
            t0 = time.time()
            try:
                req = urllib.request.Request(f"{API}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                             headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = json.load(r)
                    headers = {k.lower(): v for k, v in (getattr(r, "headers", None) or {}).items()
                               if k.lower().startswith("x-ratelimit") or k.lower() == "retry-after"}
            except urllib.error.HTTPError as e:
                self.ledger.settle(self.tag, -reserve, f"release {model} HTTP {e.code}")  # error responses are not billed
                if e.code in RETRY_STATUSES and attempt + 1 < self.max_attempts:
                    time.sleep(_retry_after((e.headers or {}).get("Retry-After"), attempt))
                    continue
                raise TFError(e.code, _error_body(e))
            except (OSError, http.client.HTTPException, ValueError) as e:
                # Timeout, refused or dropped connection (RemoteDisconnected), TLS error, cut-off body (IncompleteRead),
                # body that is not JSON. Reservation kept: the request may still be billed.
                if attempt + 1 < self.max_attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise TFError(None, f"{type(e).__name__}: {e}")
            try:
                reply = _parse(model, raw, time.time() - t0, p_in, p_out)
            except MalformedReply as e:  # reservation kept, as above
                if attempt + 1 < self.max_attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise TFError(None, f"MalformedReply: {e}")
            reply.headers = headers
            if reply.usage:
                self.ledger.settle(self.tag, reply.cost - reserve, f"settle {model}")
            else:  # no usage means no cost we can trust: keep the reservation rather than call it free
                self.ledger.settle(self.tag, 0.0, f"no usage from {model}: reservation kept")
            return reply
        raise AssertionError("unreachable")


class MalformedReply(ValueError):
    pass


def _terminate_last_line(f):
    f.seek(0, os.SEEK_END)
    end = f.tell()
    if end:
        f.seek(end - 1)
        if f.read(1) != "\n":
            f.write("\n")


def _error_body(e):
    try:
        return e.read()[:500].decode(errors="replace")
    except (OSError, http.client.HTTPException, AttributeError) as x:
        return f"(error body unreadable: {type(x).__name__})"


def _retry_after(header, attempt):
    try:
        return max(0.0, float(header))
    except (TypeError, ValueError):
        return float(2 ** attempt)


def _parse(model, raw, seconds, p_in, p_out):
    """Reply from a raw response; MalformedReply when its shape is not a chat completion (retried by the client)."""
    try:
        return _parse_reply(model, raw, seconds, p_in, p_out)
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise MalformedReply(f"{type(e).__name__}: {e}") from e


def _parse_reply(model, raw, seconds, p_in, p_out):
    choices = raw.get("choices") if isinstance(raw, dict) else None
    if not choices or not isinstance(choices, list) or not isinstance(choices[0], dict):
        raise MalformedReply(f"reply without choices: {str(raw)[:200]}")
    choice = choices[0]
    msg = choice.get("message") or {}
    if not isinstance(msg, dict):
        raise MalformedReply(f"message is not an object: {str(msg)[:200]}")
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    tool_calls = [dict(tc, function=dict(tc.get("function") or {})) for tc in msg.get("tool_calls") or []]
    for tc in tool_calls:  # normalized copies: `raw` keeps exactly what the server sent
        tc["function"]["arguments"] = tc["function"].get("arguments") or "{}"
    from_reasoning = not content and not tool_calls and bool(reasoning)
    usage = raw.get("usage") or {}
    cost = ((usage.get("prompt_tokens") or 0) * p_in + (usage.get("completion_tokens") or 0) * p_out) / 1e6
    return Reply(model, content, reasoning, reasoning if from_reasoning else content, from_reasoning, tool_calls,
                 choice.get("finish_reason") or "", usage, cost, round(seconds, 3), raw)
