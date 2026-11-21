"""Compare baseline and cover-peek on matched seeds in the approximate simulator.

    python sim/cover_ab.py -n 48 -j 6 --output cover-results.json

Use --arm baseline to record a baseline before implementation. Pass that file
with --baseline-input to run only the treatment on the matching seeds.
"""

import argparse
from collections import Counter
from contextlib import redirect_stderr
import gc
import io
import json
import math
import multiprocessing
from pathlib import Path
from statistics import fmean, stdev
import sys
from time import perf_counter, perf_counter_ns

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brain
import house
import sim


class TimedBot:
    def __init__(self, hello):
        self.bot = brain.Bot(hello)
        self.times_ns = []
        self.cover_times_ns = []
        self.phases = Counter()

    def act(self, obs):
        start = perf_counter_ns()
        action = self.bot.act(obs)
        elapsed = perf_counter_ns() - start
        self.times_ns.append(elapsed)
        phase = getattr(self.bot, "cover_phase", None)
        if phase is not None:
            self.phases[phase] += 1
            self.cover_times_ns.append(elapsed)
        return action


def run_match(job):
    seed, arm = job
    brain.TUNE["cover_peek"] = arm == "cover"
    stderr = io.StringIO()
    try:
        with redirect_stderr(stderr):
            match = sim.Match(seed, TimedBot, house.make_factory(seed))
            result = match.run()
        agent = match.mechs[match.our_slot]
        controller = agent.controller
        last_damage = agent.dmg_log[-1][2] if agent.dmg_log else None
        errors = [line for line in stderr.getvalue().splitlines()
                  if "Traceback" in line or "map load failed" in line or "map unavailable" in line]
        if errors:
            raise RuntimeError(f"{arm} match {seed} failed:\n{stderr.getvalue()[-2000:]}")
        return {
            "seed": seed,
            "arm": arm,
            "place": result["place"],
            "score": result["score"],
            "kills": agent.kills,
            "survival_s": agent.death_t if agent.death_t is not None else match.t,
            "weapon_damage": agent.dmg_taken["weapon"],
            "zone_damage": agent.dmg_taken["zone"],
            "zone_death": not agent.alive and last_damage == "zone",
            "gun_shots": agent.shots.get("gun", 0),
            "mech_gun_hits": agent.hits.get("gun", 0),
            "cover_phases": dict(controller.phases),
            "decision_ns": controller.times_ns,
            "cover_decision_ns": controller.cover_times_ns,
        }
    finally:
        gc.unfreeze()  # Bot freezes startup data in a process that normally runs one match.


def percentile(values, fraction):
    return values[math.ceil(len(values) * fraction) - 1] / 1_000_000


def summarize(rows):
    arms = {}
    for arm in ("baseline", "cover"):
        group = [row for row in rows if row["arm"] == arm]
        if not group:
            continue
        arms[arm] = {row["seed"]: row for row in group}

        def mean(key):
            return fmean(row[key] for row in group)

        times = sorted(time for row in group for time in row["decision_ns"])
        phases = Counter()
        for row in group:
            phases.update(row["cover_phases"])
        print(f"{arm} n={len(group)} score={mean('score'):.3f} place={mean('place'):.3f} "
              f"kills={mean('kills'):.3f} survival_s={mean('survival_s'):.2f} "
              f"damage={mean('weapon_damage') + mean('zone_damage'):.1f} "
              f"weapon_damage={mean('weapon_damage'):.1f} zone_damage={mean('zone_damage'):.1f} "
              f"zone_deaths={sum(row['zone_death'] for row in group)} "
              f"gun_shots={mean('gun_shots'):.1f} mech_gun_hits={mean('mech_gun_hits'):.1f}")
        if times:
            p50, p95, p99 = (percentile(times, q) for q in (0.50, 0.95, 0.99))
            print(f"  act wall ms n={len(times)} p50={p50:.3f} p95={p95:.3f} p99={p99:.3f} "
                  f"max={times[-1] / 1_000_000:.3f} >50ms={sum(t > 50_000_000 for t in times)}")
        print(f"  cover phases {dict(phases)} across "
              f"{sum(bool(row['cover_phases']) for row in group)} matches")
        cover_times = sorted(time for row in group for time in row.get("cover_decision_ns", ()))
        if cover_times:
            print(f"  active cover act wall ms n={len(cover_times)} "
                  f"p95={percentile(cover_times, 0.95):.3f} p99={percentile(cover_times, 0.99):.3f} "
                  f"max={cover_times[-1] / 1_000_000:.3f}")

    if len(arms) == 2:
        seeds = sorted(arms["baseline"].keys() & arms["cover"].keys())
        pairs = [(arms["baseline"][seed], arms["cover"][seed]) for seed in seeds]
        score_diff = [cover["score"] - baseline["score"] for baseline, cover in pairs]
        print(f"matched n={len(seeds)} score delta={fmean(score_diff):+.3f} "
              f"wins/ties/losses={sum(d > 0 for d in score_diff)}/{score_diff.count(0)}/"
              f"{sum(d < 0 for d in score_diff)}")
        if len(seeds) > 1:
            margin = 1.96 * stdev(score_diff) / math.sqrt(len(seeds))
            print(f"  approximate paired 95% score interval "
                  f"[{fmean(score_diff) - margin:+.3f}, {fmean(score_diff) + margin:+.3f}]")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", type=int, default=48, help="number of seeds per arm")
    parser.add_argument("-j", type=int, default=8, help="worker processes")
    parser.add_argument("--seed0", type=int, default=1)
    parser.add_argument("--arm", choices=("both", "baseline", "cover"), default="both")
    parser.add_argument("--baseline-input", type=Path, help="reuse baseline rows saved with --output")
    parser.add_argument("--output", type=Path, help="write per-seed results and decision timings")
    args = parser.parse_args()
    if args.n < 1 or args.j < 1:
        parser.error("-n and -j must be positive")
    if args.arm != "baseline" and "cover_peek" not in brain.TUNE:
        parser.error("cover arm requires the cover_peek switch in brain.TUNE")

    seeds = range(args.seed0, args.seed0 + args.n)
    arms = ("baseline", "cover") if args.arm == "both" else (args.arm,)
    rows = []
    if args.baseline_input:
        if args.arm == "baseline":
            parser.error("--baseline-input requires a cover arm")
        with args.baseline_input.open(encoding="utf-8") as source:
            rows = [row for row in json.load(source) if row["arm"] == "baseline" and row["seed"] in seeds]
        if len(rows) != len(seeds) or {row["seed"] for row in rows} != set(seeds):
            parser.error("baseline input must contain one baseline row per requested seed")
        arms = ("cover",)

    jobs = [(seed, arm) for seed in seeds for arm in arms]
    print(f"APPROXIMATE LOCAL SIM  seeds {seeds.start}-{seeds.stop - 1}  "
          f"workers={args.j}  jobs={len(jobs)}", flush=True)
    start = perf_counter()
    with multiprocessing.Pool(args.j) as pool:
        for count, row in enumerate(pool.imap_unordered(run_match, jobs), start=1):
            rows.append(row)
            if count % 8 == 0 or count == len(jobs):
                print(f"completed {count}/{len(jobs)} in {perf_counter() - start:.1f}s", flush=True)
    rows.sort(key=lambda row: (row["seed"], row["arm"]))
    if args.output:
        with args.output.open("w", encoding="utf-8") as target:
            json.dump(rows, target)
    summarize(rows)


if __name__ == "__main__":
    main()
