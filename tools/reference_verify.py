"""Run the official checker plus strict wire, latency and process-memory checks."""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import math
from pathlib import Path
import platform
import queue
import subprocess
import sys
import time


class WireError(RuntimeError):
    """The candidate failed the supplementary wire check."""


def load_checker(kit):
    spec = importlib.util.spec_from_file_location("mechfight_kit_check", kit / "check" / "check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finite_number(token):
    number = float(token)
    if not math.isfinite(number):
        raise ValueError(f"non-finite JSON number {token}")
    return number


def checked_frame(raw, schema, shape, max_bytes):
    if not raw.endswith(b"\n") or len(raw) - 1 > max_bytes:
        raise WireError("missing newline or over-limit JSON frame")
    try:
        value = json.loads(raw.decode("utf-8"), parse_float=finite_number, parse_constant=finite_number)
    except ValueError as e:
        raise WireError(f"invalid {shape} JSON: {e}") from e
    if errors := schema.errors(value, shape):
        raise WireError(f"invalid {shape}: {', '.join(errors)}")
    return value


def next_line(bot, deadline):
    received = bot.next(deadline)
    if received is None:
        raise WireError("timed out waiting for bot output")
    at, raw = received
    if raw is None:
        status = bot.proc.wait(timeout=1)
        raise WireError(f"bot exited before expected output (status {status})")
    return at, raw


def read_response(bot, *, deadline, wanted_tick, sent_ticks, schema, max_bytes):
    stale = 0
    while True:
        at, raw = next_line(bot, deadline)
        action = checked_frame(raw, schema, "Action", max_bytes)
        tick = action["tick"]
        if tick == wanted_tick:
            return at, stale
        if tick not in sent_ticks or tick > wanted_tick:
            raise WireError(f"unexpected action tick {tick}; wanted {wanted_tick}")
        stale += 1


def process_memory_kib(pid):
    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        return {}
    fields = {}
    for line in status.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(":")
        if key in ("VmRSS", "VmHWM"):
            fields[key] = int(value.split()[0])
    return fields


def cgroup_peak_bytes():
    for path in ("/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"):
        entry = Path(path)
        if entry.exists():
            return int(entry.read_text(encoding="utf-8"))
    return None


def load_stream(kit, arena):
    with gzip.open(kit / "check" / "stream.jsonl.gz", "rt", encoding="utf-8") as stream:
        messages = [json.loads(line) for line in stream if line.strip()]
    hello, end = messages[0], messages[-1]
    hello["map"]["dir"] = str(arena.resolve())
    return hello, [obs for obs in messages[1:-1] if obs["type"] == "obs"], end


def close_bot(bot):
    if bot.proc.poll() is None:
        bot.proc.kill()
    bot.proc.wait(timeout=2)
    bot.proc.stdin.close()
    bot.proc.stdout.close()


def strict_replay(checker, kit, arena, command):
    hello, observations, end = load_stream(kit, arena)
    schema = checker.Schema(kit / "mechfight.schema.json")
    bot_config = hello["config"]["bot"]
    max_bytes = bot_config["max_line_bytes"]
    budget_ms = bot_config["tick_budget_ms"]
    boot_budget = bot_config["hello_budget_ms"] / 1000
    times_ms = []
    observed_hwm = []
    sent_ticks = set()
    stale = batches = 0
    bot = checker.Bot(command)
    try:
        boot = time.perf_counter()
        bot.send(hello)
        at, raw = next_line(bot, boot + boot_budget)
        checked_frame(raw, schema, "Ready", max_bytes)
        ready_ms = (at - boot) * 1000
        ready_memory = process_memory_kib(bot.proc.pid)
        for i, obs in enumerate(observations):
            if i % 25 == 24:
                batch = observations[i - 2:i + 1]
                batches += 1
            else:
                batch = [obs]
            sent_ticks.update(message["tick"] for message in batch)
            start = time.perf_counter()
            bot.send_many(batch)
            at, old_frames = read_response(
                bot, deadline=start + budget_ms / 1000 + 0.25, wanted_tick=obs["tick"],
                sent_ticks=sent_ticks, schema=schema, max_bytes=max_bytes,
            )
            times_ms.append((at - start) * 1000)
            stale += old_frames
            memory = process_memory_kib(bot.proc.pid)
            if "VmHWM" in memory:
                observed_hwm.append(memory["VmHWM"])
        bot.send(end)
        bot.proc.stdin.close()
        try:
            status = bot.proc.wait(timeout=2)
        except subprocess.TimeoutExpired as e:
            raise WireError("bot did not exit after end") from e
        if status:
            raise WireError(f"bot exited with status {status} after end")
        try:
            _, trailing = bot.lines.get(timeout=0.5)
        except queue.Empty:
            trailing = None
        if trailing is not None:
            raise WireError("unexpected stdout after the final action")
    finally:
        close_bot(bot)

    times_ms.sort()

    def percentile(fraction):
        return round(times_ms[math.ceil(fraction * len(times_ms)) - 1], 3)

    return {
        "ready_ms": round(ready_ms, 3),
        "decisions": len(times_ms),
        "queue_batches": batches,
        "stale_queued_frames": stale,
        "raw_wall_over_budget": sum(t > budget_ms for t in times_ms),
        "tick_budget_ms": budget_ms,
        "wall_ms": {"p50": percentile(0.5), "p90": percentile(0.9), "p95": percentile(0.95),
                    "p99": percentile(0.99), "max": percentile(1.0)},
        "bot_rss_at_ready_kib": ready_memory.get("VmRSS"),
        "bot_high_water_rss_kib": max(observed_hwm, default=ready_memory.get("VmHWM")),
        "clean_exit": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, default=Path(__file__).resolve().parents[1] / "kit")
    parser.add_argument("--arena", type=Path, default=Path("/arena"))
    parser.add_argument("--label", required=True)
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not command:
        parser.error("supply the candidate command after --")
    print(f"=== {args.label} | {platform.system()} {platform.machine()} ===", flush=True)
    official = subprocess.run(
        [sys.executable, "-u", str(args.kit / "check" / "check.py"), "--map-dir", str(args.arena), "--", *command],
        timeout=150, check=False,
    )
    report = {"candidate": args.label, "official_returncode": official.returncode}
    try:
        report["strict"] = strict_replay(load_checker(args.kit), args.kit, args.arena, command)
    except (WireError, subprocess.TimeoutExpired, OSError) as e:
        report["strict_error"] = str(e)
    report["whole_container_peak_memory_bytes"] = cgroup_peak_bytes()
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False), flush=True)
    return int(official.returncode != 0 or "strict_error" in report)


if __name__ == "__main__":
    raise SystemExit(main())
