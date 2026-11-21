"""Compare v8 and pinned standalone Go bots in the approximate Mech Royale simulator.

Run all three on identical starting seeds with `python sim/reference_compare.py`.
Use `--scenario ffa` for the existing model's independent house-bot opponents.
The simulator does not apply the official wall-clock decision deadline.
"""

import argparse
from collections import defaultdict
from contextlib import redirect_stderr
import gc
import io
import json
import math
import multiprocessing
import os
from pathlib import Path
from queue import Empty, SimpleQueue
from statistics import fmean, median, stdev
import subprocess
import sys
from tempfile import TemporaryFile
from threading import Thread
from time import perf_counter, perf_counter_ns

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brain
import house
import sim


class ProtocolController:
    """Adapt a standalone mechfight/1 controller to the simulator's Python callback."""

    def __init__(self, hello, binary):
        self.output = SimpleQueue()
        self.limit = hello["config"]["bot"]["max_line_bytes"]
        env = dict(os.environ, GOMAXPROCS="1")
        self.stderr = TemporaryFile()
        try:
            self.process = subprocess.Popen(
                [binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
                cwd=str(Path(binary).parent), env=env,
            )
        except Exception:
            self.stderr.close()
            raise
        self.reader = Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self._send(hello)
            response = self._receive(hello["config"]["bot"]["hello_budget_ms"] / 1_000)
            if response.get("type") != "ready" or not isinstance(response.get("name"), str):
                raise ValueError(f"expected ready, received {response!r}")
            self.name = response["name"]
        except Exception as error:
            detail = self.close()
            raise RuntimeError(f"startup failed for {binary}: {error}\n{detail}") from error

    def _read(self):
        try:
            while line := self.process.stdout.readline(self.limit + 1):
                self.output.put(line)
        finally:
            self.output.put(None)

    def _send(self, message):
        payload = (json.dumps(message, separators=(",", ":"), allow_nan=False) + "\n").encode()
        self.process.stdin.write(payload)
        self.process.stdin.flush()

    def _receive(self, timeout):
        try:
            line = self.output.get(timeout=timeout)
        except Empty as error:
            raise TimeoutError(f"controller did not respond in {timeout:g} s") from error
        if line is None:
            raise RuntimeError("controller closed stdout")
        if len(line) > self.limit or not line.endswith(b"\n"):
            raise ValueError("invalid protocol line length or termination")
        response = json.loads(line)
        if not isinstance(response, dict):
            raise ValueError("expected a JSON object")
        return response

    def act(self, obs):
        self._send(obs)
        response = self._receive(3.0)  # Stop a deadlock without simulating the 50 ms deadline.
        if response.get("tick") != obs["tick"]:
            raise ValueError(f"expected tick {obs['tick']}, received {response.get('tick')!r}")
        return response

    def close(self, result=None):
        try:
            if self.process.poll() is None:
                if result is not None:
                    self._send({"type": "end", "place": result["place"], "kills": result["kills"]})
                    self.process.stdin.close()
                    self.process.wait(timeout=2.0)
                else:
                    self.process.kill()
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.process.kill()
        finally:
            self.process.wait()
            self.reader.join(timeout=2.0)
            self.process.stdin.close()
            self.process.stdout.close()
            end = self.stderr.seek(0, os.SEEK_END)
            self.stderr.seek(max(0, end - 1_200))
            detail = self.stderr.read().decode(errors="replace")
            self.stderr.close()
        return detail


class TimedController:
    def __init__(self, hello, binary):
        self.bot = ProtocolController(hello, binary) if binary else brain.Bot(hello)
        self.times = []
        self.failure = None

    def act(self, obs):
        start = perf_counter_ns()
        try:
            return self.bot.act(obs)
        except Exception as error:
            self.failure = error
            return {}
        finally:
            self.times.append(perf_counter_ns() - start)


class ReferenceMatch(sim.Match):
    def __init__(self, seed, factory, scenario):
        self.ffa = scenario == "ffa"
        self.acquired_at = {}
        self.laser_releases = []
        self.dealt = defaultdict(float)
        super().__init__(seed, factory, house.make_factory(seed))

    def decide(self):
        super().decide()
        controller = self.mechs[self.our_slot].controller
        if controller.failure is not None:
            raise RuntimeError(f"controller failed at simulation tick {self.tick}") from controller.failure

    def take(self, mech, pickup_id, drop):
        super().take(mech, pickup_id, drop)
        if mech.id == self.our_slot:
            for weapon in mech.weapons:
                if weapon and weapon.kind != "gun":
                    self.acquired_at.setdefault(weapon.kind, self.t)

    def _laser_shot(self, mech, weapon, stats):
        if mech.id == self.our_slot:
            self.laser_releases.append((self.t, weapon.charge, stats["charge"]))
        super()._laser_shot(mech, weapon, stats)

    def damage(self, victim, amount, weapon, attacker=None, from_pos=None):
        before = victim.hp
        super().damage(victim, amount, weapon, attacker, from_pos)
        if attacker is not None and attacker.id == self.our_slot and victim is not attacker:
            self.dealt[weapon] += before - victim.hp


def run_match(job):
    seed, arm, binary, scenario = job
    controller = result = None

    def factory(hello):
        nonlocal controller
        controller = TimedController(hello, binary)
        return controller

    try:
        errors = io.StringIO()
        with redirect_stderr(errors):
            match = ReferenceMatch(seed, factory, scenario)
            result = match.run()
        if "map unavailable" in errors.getvalue() or "Traceback" in errors.getvalue():
            raise RuntimeError(errors.getvalue()[-1_500:])
        mech = match.mechs[match.our_slot]
        last_weapon = mech.dmg_log[-1][2] if mech.dmg_log else None
        return {
            "seed": seed, "arm": arm, "scenario": scenario,
            "slot": match.our_slot, "place": result["place"], "score": result["score"],
            "kills": mech.kills, "survival_s": mech.death_t if mech.death_t is not None else match.t,
            "weapon_damage": mech.dmg_taken["weapon"], "zone_damage": mech.dmg_taken["zone"],
            "zone_death": not mech.alive and last_weapon == "zone",
            "acquired_s": match.acquired_at, "dealt_hp": dict(match.dealt),
            "laser_shots": len(match.laser_releases), "laser_hits": mech.hits.get("laser", 0),
            "laser_full_shots": sum(amount >= full - 1e-8 for _, amount, full in match.laser_releases),
            "first_laser_shot_s": match.laser_releases[0][0] if match.laser_releases else None,
            "latency_ns": controller.times,
        }
    except Exception as error:
        raise RuntimeError(f"{scenario} {arm} seed {seed}: {error}") from error
    finally:
        if controller is not None and binary:
            detail = controller.bot.close(result)
            if detail and result is None:
                print(f"{arm} seed {seed} stderr: {detail}", file=sys.stderr)
        gc.unfreeze()  # The v8 map setup freezes startup data in a one-match process.


def percentile(values, fraction):
    return values[math.ceil(len(values) * fraction) - 1] / 1_000_000


def summarize(rows):
    by_arm = {}
    for arm in ("v8", "giddy", "kanyaraasi"):
        group = [row for row in rows if row["arm"] == arm]
        if not group:
            continue
        by_arm[arm] = {row["seed"]: row for row in group}

        def mean(key):
            return fmean(row[key] for row in group)

        print(f"{arm} n={len(group)} place={mean('place'):.3f} score={mean('score'):.3f} "
              f"kills={mean('kills'):.3f} survival_s={mean('survival_s'):.2f} "
              f"weapon_damage={mean('weapon_damage'):.1f} zone_damage={mean('zone_damage'):.1f} "
              f"zone_deaths={sum(row['zone_death'] for row in group)}")
        acquired = [min(row["acquired_s"].values()) for row in group if row["acquired_s"]]
        if acquired:
            print(f"  first non-gun acquisition {len(acquired)}/{len(group)} median_s={median(acquired):.2f}")
        for kind in ("laser", "lightning", "mortar"):
            times = [row["acquired_s"][kind] for row in group if kind in row["acquired_s"]]
            if times:
                print(f"  {kind} acquisition {len(times)}/{len(group)} median_s={median(times):.2f}")
        laser_shots = sum(row["laser_shots"] for row in group)
        laser_hits = sum(row["laser_hits"] for row in group)
        laser_hp = sum(row["dealt_hp"].get("laser", 0.0) for row in group)
        first_laser = [row["first_laser_shot_s"] for row in group if row["first_laser_shot_s"] is not None]
        print(f"  laser shots={laser_shots} full={sum(row['laser_full_shots'] for row in group)} "
              f"mech_hits={laser_hits} mech_hp={laser_hp:.1f} "
              f"first_release_median_s={median(first_laser) if first_laser else 'none'}")
        latency = sorted(ns for row in group for ns in row["latency_ns"])
        if latency:
            print(f"  {'in-process act' if arm == 'v8' else 'protocol round-trip'} wall ms "
                  f"p50={percentile(latency, .50):.3f} p95={percentile(latency, .95):.3f} "
                  f"p99={percentile(latency, .99):.3f} max={latency[-1] / 1_000_000:.3f} "
                  f">50ms={sum(ns > 50_000_000 for ns in latency)}")

    baseline = by_arm.get("v8", {})
    for arm in ("giddy", "kanyaraasi"):
        reference = by_arm.get(arm, {})
        common = sorted(baseline.keys() & reference.keys())
        if not common:
            continue
        deltas = [reference[seed]["score"] - baseline[seed]["score"] for seed in common]
        mean = fmean(deltas)
        print(f"{arm} vs v8 paired n={len(common)} score_delta={mean:+.3f} "
              f"wins/ties/losses={sum(d > 0 for d in deltas)}/{deltas.count(0)}/{sum(d < 0 for d in deltas)}")
        if len(common) > 1:
            margin = 1.96 * stdev(deltas) / math.sqrt(len(common))
            print(f"  approximate 95% paired interval [{mean - margin:+.3f}, {mean + margin:+.3f}]")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=("practice", "ffa"), default="practice")
    parser.add_argument("--arm", choices=("all", "v8", "giddy", "kanyaraasi"), default="all")
    parser.add_argument("--seed0", type=int, default=1)
    parser.add_argument("-n", type=int, default=48)
    parser.add_argument("-j", type=int, default=6)
    parser.add_argument("--giddy", type=Path)
    parser.add_argument("--kanyaraasi", type=Path)
    parser.add_argument("--output", type=Path, help="per-seed JSON rows")
    args = parser.parse_args()
    if args.n < 1 or args.j < 1:
        parser.error("-n and -j must be positive")

    arms = ("v8", "giddy", "kanyaraasi") if args.arm == "all" else (args.arm,)
    binaries = {"v8": None}
    for arm in arms:
        if arm == "v8":
            continue
        filename = "bot.exe" if os.name == "nt" else "bot"
        path = getattr(args, arm) or ROOT / ".reference-build" / arm / filename
        if not path.is_file():
            parser.error(f"missing {arm} executable at {path}")
        binaries[arm] = str(path.resolve())

    seeds = range(args.seed0, args.seed0 + args.n)
    jobs = [(seed, arm, binaries[arm], args.scenario) for seed in seeds for arm in arms]
    print(f"APPROXIMATE {args.scenario.upper()} SIM  seeds {seeds.start}-{seeds.stop - 1}  "
          f"workers={args.j}  jobs={len(jobs)}", flush=True)
    start = perf_counter()
    rows = []
    with multiprocessing.Pool(args.j) as pool:
        for count, row in enumerate(pool.imap_unordered(run_match, jobs), start=1):
            rows.append(row)
            if count % 8 == 0 or count == len(jobs):
                print(f"completed {count}/{len(jobs)} after {perf_counter() - start:.1f}s", flush=True)
    rows.sort(key=lambda row: (row["seed"], row["arm"]))
    if args.output:
        with args.output.open("w", encoding="utf-8") as stream:
            json.dump(rows, stream)
    summarize(rows)


if __name__ == "__main__":
    main()
