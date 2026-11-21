"""Robustness fuzz over a real recorded observation.

Drops optional fields, fills lists, and pushes positions to the map edge.
Asserts act() does not raise, stays inside 20 ms, and returns a schema-valid action.

    python tests/test_fuzz.py
"""
import copy
import gzip
import json
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "kit", "check"))

import check  # noqa: E402
from brain import Bot  # noqa: E402

STREAM = os.path.join(ROOT, "kit", "check", "stream.jsonl.gz")
MSGS = [json.loads(line) for line in gzip.open(STREAM, "rt")]
HELLO = copy.deepcopy(MSGS[0])
HELLO["map"]["dir"] = os.path.join(ROOT, "kit", "arena")
SCHEMA = check.Schema(os.path.join(ROOT, "kit", "mechfight.schema.json"))
OBS = [m for m in MSGS if m.get("type") == "obs"]
BOT = Bot(HELLO)
LIMIT_MS = 20.0


def _check(bot, obs, label):
    t = time.perf_counter()
    try:
        action = bot.act(obs)
    except Exception as exc:  # noqa: BLE001 - the point of the fuzz is to catch these
        raise AssertionError(f"{label}: act raised {exc!r}") from exc
    elapsed = (time.perf_counter() - t) * 1000.0
    action = dict(action)
    action["tick"] = obs["tick"]
    errs = SCHEMA.errors(action, "Action")
    assert not errs, f"{label}: {errs} {action}"
    move = action.get("move") or [0, 0]
    assert move[0] * move[0] + move[1] * move[1] <= 1.01, f"{label}: move {move}"
    json.dumps(action)
    assert elapsed < LIMIT_MS, f"{label}: {elapsed:.2f} ms"
    return elapsed


def _mutate(rng, obs):
    o = copy.deepcopy(obs)
    o["qa_unknown"] = {"mode": "finals", "n": rng.randrange(10)}
    if rng.random() < 0.5:
        for key in ("hp", "max_hp", "cubes", "weapons", "charging"):
            for actor in o.get("mechs") or []:
                if rng.random() < 0.5:
                    actor.pop(key, None)
    if rng.random() < 0.4:
        weapons = o["self"].get("weapons") or [None, None]
        if weapons:
            weapons[rng.randrange(len(weapons))] = None
            o["self"]["weapons"] = weapons
    if rng.random() < 0.3:
        gun = o["self"]["weapons"][0]
        if isinstance(gun, dict):
            gun.pop("ammo", None)
            gun.pop("reload", None)
            gun.pop("charge", None)
    if rng.random() < 0.3:
        for key in ("crates", "cubes", "pickups", "sounds", "beams", "events", "drops", "projectiles"):
            if rng.random() < 0.5:
                o[key] = []
    if rng.random() < 0.4:
        o["events"] = [{"type": "damaged", "amount": 15, "weapon": "zone", "bearing": None, "by": None}]
    if rng.random() < 0.3:
        o["offer"] = {"id": 99, "expires": o["t"] + 5,
                      "choices": [{"id": "not-a-real-upgrade", "name": "x", "desc": "y"},
                                  {"id": "armor", "name": "a", "desc": "b"},
                                  {"id": "plating", "name": "p", "desc": "q"}]}
    else:
        o["offer"] = None
    if rng.random() < 0.2 and BOT.map is not None:
        m = BOT.map
        o["self"]["pos"] = [m.minx + 2.0, m.miny + 2.0, 0.0]
        o["self"]["vel"] = [0, 0, 0]
    return o


def main():
    rng = random.Random(6113)
    times = []
    base = OBS[len(OBS) // 2]
    for i in range(40):
        times.append(_check(BOT, _mutate(rng, base), f"mut-{i}"))
    # Many simultaneous shells. This is the expensive dodge path.
    heavy = copy.deepcopy(base)
    px, py, pz = heavy["self"]["pos"]
    heavy["projectiles"] = [
        {"id": 1000 + i, "kind": "mortar" if i % 2 == 0 else "gun",
         "pos": [px + (i % 17) - 8, py + (i % 5), pz + 30],
         "vel": [-20 - (i % 7), 3, -4]}
        for i in range(48)
    ]
    heavy["mechs"] = [
        {"id": i, "kind": "mech", "pos": [px + 20 + i, py, pz], "vel": [0, 0, 0],
         "aim": [0, 0], "hp": 900, "max_hp": 1400, "cubes": 0, "weapons": ["gun", "laser"],
         "charging": i % 2 == 0}
        for i in range(12)
    ]
    times.append(_check(BOT, heavy, "heavy"))
    times.sort()
    print(f"fuzz decisions {len(times)}  "
          f"p50 {times[len(times)//2]:.2f} ms  p95 {times[int(len(times)*0.95)]:.2f} ms  "
          f"max {times[-1]:.2f} ms  limit {LIMIT_MS:.0f} ms")
    print(f"{len(times)}/{len(times)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
