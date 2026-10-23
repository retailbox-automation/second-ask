"""Shared runner for the platform spikes. Every call goes through second_ask.tf_client (ledger tag "b2").

Writes one JSON line per call to evidence/spikes/<name>/raw.jsonl: the request parameters (never the key), the raw
response, the rate-limit headers, latency and cost. Prints one log line per call.
"""
import json, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from second_ask import models as M, tf_client as tf  # noqa: E402

EVIDENCE = ROOT / "evidence" / "spikes"


def run(name, jobs, workers=6, tag="b2"):
    """jobs: dicts with id, model, messages, params and optional meta. Returns one record per job, in order."""
    out = EVIDENCE / name
    out.mkdir(parents=True, exist_ok=True)
    path, lock = out / "raw.jsonl", threading.Lock()
    path.write_text("")
    client = tf.Client(tag, max_attempts=4, log=lambda m: print(m, file=sys.stderr))

    def one(job):
        rec = {"id": job["id"], "model": job["model"], "params": job["params"], "meta": job.get("meta", {}),
               "messages": job["messages"], "ts": round(time.time(), 3)}
        try:
            r = client.chat(job["model"], job["messages"], **job["params"])
            rec.update(ok=True, status=200, seconds=r.seconds, cost=r.cost, usage=r.usage, finish_reason=r.finish_reason,
                       content=r.content, reasoning=r.reasoning, from_reasoning=r.from_reasoning,
                       tool_calls=r.tool_calls, reasoning_tokens=r.reasoning_tokens, headers=r.headers, raw=r.raw)
            print(f"[{name}] {job['id']} {job['model']} {r.seconds:.2f}s in={r.usage.get('prompt_tokens')} "
                  f"out={r.usage.get('completion_tokens')} reasoning={r.reasoning_tokens} ${r.cost:.5f} "
                  f"finish={r.finish_reason}")
        except tf.TFError as e:  # BudgetExceeded is not caught: it stops the spike
            rec.update(ok=False, status=e.status, error=str(e)[:800])
            print(f"[{name}] {job['id']} {job['model']} ERROR {str(e)[:200]}")
        with lock, open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, jobs))


def spent():
    total, by_tag = tf.Ledger().spent()
    return f"ledger: total ${total:.4f}, b2 ${by_tag.get('b2', 0.0):.4f}"
