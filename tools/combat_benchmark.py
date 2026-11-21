"""Measure combat decision latency on the kit replay and fixed dense observations.

    python tools/combat_benchmark.py [--repeats 5]
"""

import argparse
import gzip
import json
import math
from pathlib import Path
import platform
import sys
from time import perf_counter_ns

from combat_check_bot import PLANNED_MOVE

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "kit"
sys.path.insert(0, str(KIT / "check"))

from check import Schema
from combat import CombatController
from geom import MapModel


def load_replay():
    with gzip.open(KIT / "check" / "stream.jsonl.gz", "rt", encoding="utf-8") as stream:
        hello = json.loads(next(stream))
        observations = [message for message in map(json.loads, stream) if message["type"] == "obs"]
    return hello, observations


def point_from(origin, bearing, distance, z):
    return [origin[0] + math.cos(bearing) * distance, origin[1] + math.sin(bearing) * distance, z]


def stress_actors(origin, config, arena, slot, phase):
    actors = []
    enemy_ids = [actor_id for actor_id in range(config["match"]["mechs"]) if actor_id != slot]
    npc_ids = list(range(100, 100 + config["npc"]["count"]))
    for index, actor_id in enumerate(enemy_ids + npc_ids):
        mech = index < len(enemy_ids)
        bearing = phase + index * 2.399963229728653  # Golden angle spreads the actors around the mech.
        distance = 9 + index * 5 if mech else 14 + (index - len(enemy_ids)) * 3
        pos = point_from(origin, bearing, distance, 0)
        if arena.is_blocked(pos[0], pos[1]):
            cell = arena.nearest_free(arena.ncell(pos[0], pos[1]))
            pos[:2] = arena.ncenter(cell)
        pos[2] = arena.ground(pos[0], pos[1])
        speed = 6 if mech else 3
        vel = [speed * math.sin(bearing), -speed * math.cos(bearing), 0]
        yaw = math.atan2(origin[1] - pos[1], origin[0] - pos[0])
        hp = config["mech" if mech else "npc"]["hp"]
        actor = {"id": actor_id, "kind": "mech" if mech else "npc", "pos": pos, "vel": vel,
                 "aim": [yaw, 0], "hp": hp * (0.55 + 0.1 * (index % 5)), "max_hp": hp}
        if mech:
            secondary = ("laser", "mortar", "lightning")[index % 3]
            actor.update(cubes=index % 4, weapons=["gun", secondary], charging=secondary == "laser")
        actors.append(actor)
    return actors


def stress_projectiles(origin, config, phase):
    projectiles = []
    for index in range(48):
        kind = ("gun", "npc", "mortar")[index % 3]
        bearing = phase + index * 0.7
        distance = 10 + 4 * (index % 12)
        z = origin[2] + (14 + index % 7 if kind == "mortar" else 1 + index % 8)
        pos = point_from(origin, bearing, distance, z)
        if kind == "mortar":
            speed = config["weapons"]["mortar"]["speed"] * 0.65
            vertical_speed = -16 - index % 11
        elif kind == "gun":
            speed = config["weapons"]["gun"]["speed"]
            vertical_speed = 0
        else:
            speed = config["npc"]["projectile_speed"]
            vertical_speed = 0
        heading = bearing + math.pi + 0.03 * ((index % 11) - 5)
        vel = [speed * math.cos(heading), speed * math.sin(heading), vertical_speed]
        projectiles.append({"id": 50_000 + index, "kind": kind, "pos": pos, "vel": vel})
    return projectiles


def stress_stream(template, config, arena, loadout):
    observations = []
    match = config["match"]
    for index in range(96):
        stress = index % 16 >= 4
        phase = index * 0.045
        own = dict(template["self"])
        own["pos"] = point_from(own["pos"], phase, 3, own["pos"][2])
        own["pos"][2] = arena.ground(*own["pos"][:2])
        own["vel"] = [-1.35 * math.sin(phase), 1.35 * math.cos(phase), 0]
        own["aim"] = [math.remainder(phase, math.tau), 0.1]
        own["hp"] = own["max_hp"] * (0.55 if stress else 0.95)
        own["energy"] = own["max_energy"] * (0.25 if stress else 0.8)
        own["weapons"] = []
        for kind in loadout:
            stats = dict(config["weapons"][kind])
            weapon = {"kind": kind, "stats": stats, "cooldown": 0}
            if kind == "gun":
                weapon.update(ammo=stats["mag"] - index % 4, reload=0)
            elif kind == "laser":
                weapon["charge"] = min(stats["charge"], (index % 10) * 0.1)
            own["weapons"].append(weapon)
        tick = index * match["decide_every"]
        actors = stress_actors(own["pos"], config, arena, own["id"], phase)
        projectiles = stress_projectiles(own["pos"], config, phase) if stress else []
        observations.append(dict(template, tick=tick, t=tick / match["tick_hz"], self=own,
                                 mechs=actors, projectiles=projectiles))
    return observations


def time_decisions(observations, config, arena, repeats, schema):
    timings = []
    reference_actions = None
    for repeat in range(repeats + 1):
        controller = CombatController(config, arena=arena)
        actions = []
        for obs in observations:
            start = perf_counter_ns()
            result = controller.decide(obs, move=PLANNED_MOVE, engage=True)
            elapsed_ms = (perf_counter_ns() - start) / 1_000_000
            if repeat:
                timings.append(elapsed_ms)
            action = dict(result.action_fields(), tick=obs["tick"])
            actions.append(json.dumps(action, allow_nan=False))
            if not repeat:
                errors = schema.errors(action, "Action")
                if errors:
                    raise ValueError(f"Invalid action at tick {obs['tick']}: {errors}")
        if reference_actions is None:
            reference_actions = actions
        elif reference_actions != actions:
            raise ValueError(f"Action output changed on repeat {repeat}")
    return sorted(timings)


def percentile(sorted_values, fraction):
    return sorted_values[math.ceil(len(sorted_values) * fraction) - 1]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeats", default=5, type=int, help="timed passes per input stream")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    hello, recorded = load_replay()
    config = hello["config"]
    arena = MapModel(str(KIT / "arena"), hello["map"]["files"], config["mech"])
    schema = Schema(KIT / "mechfight.schema.json")
    scenarios = {"kit replay": recorded}
    for loadout in (("gun", "lightning"), ("mortar", "laser")):
        scenarios["dense " + "/".join(loadout)] = stress_stream(recorded[0], config, arena, loadout)

    budget_ms = config["bot"]["tick_budget_ms"]
    print(f"Python {platform.python_version()} on {platform.system()} {platform.machine()}")
    print(f"Controller only, elapsed milliseconds, 1 warmup and {args.repeats} timed passes per input")
    print("Synthetic dense turns contain 15 opponent mechs, 12 NPCs, and up to 48 projectiles.")
    for label, observations in scenarios.items():
        times = time_decisions(observations, config, arena, args.repeats, schema)
        p50, p95, p99 = (percentile(times, q) for q in (0.50, 0.95, 0.99))
        late = sum(elapsed > budget_ms for elapsed in times)
        print(f"{label:24} n={len(times):4}  p50={p50:7.3f}  p95={p95:7.3f}  "
              f"p99={p99:7.3f}  max={times[-1]:7.3f}  >{budget_ms} ms={late}")
    print("Actions were schema-valid and deterministic across passes. Inputs were fixed observations.")


if __name__ == "__main__":
    main()
