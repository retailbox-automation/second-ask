"""Nebius Token Factory client (OpenAI-compatible chat completions).

Handles the Nemotron quirks we hit in practice: text arriving in `reasoning` with `content: null`,
`finish_reason == "length"` when reasoning eats the budget, empty tool-call arguments, retired model IDs.
Every call reserves its worst-case cost in a shared append-only ledger before it is sent, so a hard
spend cap holds across processes and threads. Token Factory has no spend limit of its own.
"""
import fcntl, json, os, time, urllib.error, urllib.request
from dataclasses import dataclass
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

    def _write(self, tag, usd, note, check):
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                if check:
                    total, by_tag = self._sum(f)
                    if total + usd > self.caps["total"] or by_tag.get(tag, 0.0) + usd > self.caps.get(tag, 0.0):
                        raise BudgetExceeded(f"[{tag}] ${usd:.4f} would pass the cap (spent ${total:.4f} total, "
                                             f"${by_tag.get(tag, 0.0):.4f} on {tag}; caps {self.caps})")
                f.write(json.dumps({"ts": round(time.time(), 3), "tag": tag, "usd": usd, "note": note}) + "\n")
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    @staticmethod
    def _sum(f):
        f.seek(0)
        total, by_tag = 0.0, {}
        for line in f:
            if line.strip():
                r = json.loads(line)
                total += r["usd"]
                by_tag[r["tag"]] = by_tag.get(r["tag"], 0.0) + r["usd"]
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
        reserve = (est_in * p_in + params.get("max_tokens", 8192) * p_out) / 1e6
        for attempt in range(self.max_attempts):
            self.ledger.reserve(self.tag, reserve, f"reserve {model}")
            t0 = time.time()
            try:
                req = urllib.request.Request(f"{API}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                             headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = json.load(r)
            except urllib.error.HTTPError as e:
                self.ledger.settle(self.tag, -reserve, f"release {model} HTTP {e.code}")  # error responses are not billed
                if e.code in RETRY_STATUSES and attempt + 1 < self.max_attempts:
                    time.sleep(_retry_after(e.headers.get("Retry-After"), attempt))
                    continue
                raise TFError(e.code, e.read()[:500].decode(errors="replace"))
            except (TimeoutError, urllib.error.URLError) as e:  # reservation kept: the request may still be billed
                if attempt + 1 < self.max_attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise TFError(None, f"{type(e).__name__}: {e}")
            reply = _parse(model, raw, time.time() - t0, p_in, p_out)
            self.ledger.settle(self.tag, reply.cost - reserve, f"settle {model}")
            return reply
        raise AssertionError("unreachable")


def _retry_after(header, attempt):
    try:
        return max(0.0, float(header))
    except (TypeError, ValueError):
        return float(2 ** attempt)


def _parse(model, raw, seconds, p_in, p_out):
    choice = raw["choices"][0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    tool_calls = msg.get("tool_calls") or []
    for tc in tool_calls:
        tc.setdefault("function", {})["arguments"] = tc["function"].get("arguments") or "{}"
    from_reasoning = not content and not tool_calls and bool(reasoning)
    usage = raw.get("usage") or {}
    cost = (usage.get("prompt_tokens", 0) * p_in + usage.get("completion_tokens", 0) * p_out) / 1e6
    return Reply(model, content, reasoning, reasoning if from_reasoning else content, from_reasoning, tool_calls,
                 choice.get("finish_reason") or "", usage, cost, round(seconds, 3), raw)
