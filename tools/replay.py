"""Replay the official recorded stream straight into Bot.act (no wrapper), surfacing exceptions,
validating every action against the schema, and reporting per-decision latency.

    python tools/replay.py            # uses kit/check/stream.jsonl.gz and kit/arena
"""
import gzip
import json
import os
import statistics
import sys
import time
import tracemalloc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "kit", "check"))

import check  # noqa: E402  (official validator, used only for its Schema class)
from brain import Bot  # noqa: E402


def main():
    stream = os.path.join(ROOT, "kit", "check", "stream.jsonl.gz")
    msgs = [json.loads(l) for l in gzip.open(stream, "rt")]
    hello = msgs[0]
    hello["map"]["dir"] = os.path.join(ROOT, "kit", "arena")
    schema = check.Schema(os.path.join(ROOT, "kit", "mechfight.schema.json"))
    if not os.environ.get("NOTRACE"):
        tracemalloc.start()
    t = time.perf_counter()
    bot = Bot(hello)
    init_ms = (time.perf_counter() - t) * 1000
    times, bad, modes = [], 0, {}
    for m in msgs[1:]:
        if m.get("type") != "obs":
            continue
        t = time.perf_counter()
        a = bot.act(m)
        times.append((time.perf_counter() - t) * 1000)
        a = dict(a)
        a["tick"] = m["tick"]
        errs = schema.errors(a, "Action")
        if errs:
            bad += 1
            if bad <= 3:
                print("schema error", m["tick"], errs, a)
        json.dumps(a)
        modes[bot.mode] = modes.get(bot.mode, 0) + 1
    cur, peak = tracemalloc.get_traced_memory()
    times.sort()
    q = lambda p: times[min(len(times) - 1, int(p * len(times)))]
    print(f"init {init_ms:.0f} ms  decisions {len(times)}  schema errors {bad}")
    print(f"act ms p50 {q(.5):.2f} p95 {q(.95):.2f} p99 {q(.99):.2f} max {times[-1]:.2f}  mean {statistics.mean(times):.2f}")
    print(f"python heap peak {peak/1e6:.1f} MB  modes {modes}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
