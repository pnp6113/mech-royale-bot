"""Tactical scenario tests on synthetic (schema-shaped) observations. Run: python tests/test_scenarios.py

These are unit checks of decisions, not a game simulation.
"""
import copy
import gzip
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "kit", "check"))
import check  # noqa: E402
from brain import Bot, wrap  # noqa: E402

MSGS = [json.loads(l) for l in gzip.open(os.path.join(ROOT, "kit", "check", "stream.jsonl.gz"), "rt")]
HELLO = MSGS[0]
HELLO["map"]["dir"] = os.path.join(ROOT, "kit", "arena")
SCHEMA = check.Schema(os.path.join(ROOT, "kit", "mechfight.schema.json"))
TEMPLATE = next(m for m in MSGS if m.get("type") == "obs")
BOT0 = Bot(copy.deepcopy(HELLO))  # map built once, reused
MAP = BOT0.map


def open_spot():
    # find a free spot with a lot of open space around it
    for x in range(-200, 300, 8):
        for y in range(-200, 200, 8):
            if all(not MAP.is_blocked(x + dx, y + dy) for dx in range(-70, 71, 10) for dy in range(-70, 71, 10)):
                return float(x), float(y)
    raise RuntimeError("no open spot")


OX, OY = open_spot()
OZ = MAP.ground(OX, OY)


def fresh_bot():
    b = Bot.__new__(Bot)
    b.__dict__.update(copy.deepcopy({k: v for k, v in BOT0.__dict__.items() if k not in ("map", "planner")}))
    b.map, b.planner = MAP, BOT0.planner
    b.planner.path, b.planner.active, b.planner.path_goal = [], False, None
    return b


def obs(**over):
    o = copy.deepcopy(TEMPLATE)
    o.update(tick=100, t=10.0, alive=16, mechs=[], projectiles=[], crates=[], cubes=[], pickups=[], sounds=[],
             beams=[], events=[], drops=[], offer=None)
    s = o["self"]
    s.update(pos=[OX, OY, OZ], vel=[0, 0, 0], aim=[0.0, 0.0], hp=1400, max_hp=1400, energy=100, cubes=0,
             in_zone=True, grounded=True)
    s["weapons"] = [{"kind": "gun", "cooldown": 0, "ammo": 12, "reload": 0,
                     "stats": {"damage": 45, "cooldown": 0.24, "speed": 252, "range": 230, "spread_deg": 0.6,
                               "mag": 12, "reload": 2.4}}, None]
    o["zone"] = {"center": [OX, OY], "radius": 560, "next_center": [OX, OY], "next_radius": 380,
                 "shrink_start": 60, "shrink_end": 110, "dps": 20}
    for k, v in over.items():
        if k == "self":
            s.update(v)
        else:
            o[k] = v
    return o


def enemy(i, dx, dy, **kw):
    e = {"id": i, "kind": "mech", "pos": [OX + dx, OY + dy, MAP.ground(OX + dx, OY + dy)], "vel": [0, 0, 0],
         "aim": [0.0, 0.0], "hp": 1400, "max_hp": 1400, "cubes": 0, "weapons": ["gun"], "charging": False}
    e.update(kw)
    return e


def run(bot, o):
    a = dict(bot.act(o))
    a["tick"] = o["tick"]
    errs = SCHEMA.errors(a, "Action")
    assert not errs, errs
    json.dumps(a)
    return a


RESULTS = []


def case(fn):
    try:
        fn()
        RESULTS.append((fn.__name__, "ok", ""))
    except AssertionError as e:
        RESULTS.append((fn.__name__, "FAIL", str(e)))
    except Exception as e:  # crash
        import traceback
        RESULTS.append((fn.__name__, "CRASH", traceback.format_exc()))
    return fn


@case
def no_enemy_visible():
    a = run(fresh_bot(), obs())
    assert not any(a["fire"]), a


@case
def shoots_visible_enemy_when_aimed():
    b = fresh_bot()
    e = enemy(5, 60, 0)
    a = run(b, obs(mechs=[e]))
    assert abs(wrap(a["aim"][0] - 0.0)) < 0.15, a["aim"]
    # now our torso is on target
    o = obs(mechs=[e], self={"aim": a["aim"]})
    a = run(b, o)
    assert a["fire"][0], a


@case
def leads_moving_target():
    b = fresh_bot()
    e = enemy(5, 120, 0, vel=[0, 10, 0])
    a = run(b, obs(mechs=[e]))
    assert a["aim"][0] > 0.01, a["aim"]  # leads toward +y


@case
def outside_zone_runs_in_with_boost():
    b = fresh_bot()
    z = {"center": [OX - 300, OY], "radius": 200, "next_center": [OX - 300, OY], "next_radius": 120,
         "shrink_start": 5, "shrink_end": 40, "dps": 20}
    a = run(b, obs(zone=z, self={"in_zone": False}))
    assert a["move"][0] < -0.5 and a["boost"], a


@case
def dodges_head_on_bullet():
    b = fresh_bot()
    p = {"id": 1, "kind": "gun", "pos": [OX + 40, OY, OZ + 4.5], "vel": [-252, 0, 0]}
    a = run(b, obs(projectiles=[p]))
    assert abs(a["move"][1]) > 0.5, a["move"]


@case
def dodges_mortar_landing():
    b = fresh_bot()
    # shell falling right on us in ~1 s
    p = {"id": 2, "kind": "mortar", "pos": [OX + 2, OY + 1, OZ + 25], "vel": [0, 0, -2]}
    a = run(b, obs(projectiles=[p]))
    assert math.hypot(*a["move"]) > 0.5 and a["move"][0] < 0.3, a["move"]


@case
def sidesteps_charging_laser():
    b = fresh_bot()
    e = enemy(6, 150, 0, aim=[math.pi, 0.0], charging=True, weapons=["gun", "laser"])
    a = run(b, obs(mechs=[e]))
    assert abs(a["move"][1]) > 0.4 and a["boost"], a


@case
def takes_pickup_in_range():
    b = fresh_bot()
    pk = {"id": 8001, "kind": "lightning", "pos": [OX + 1.5, OY, OZ]}
    a = run(b, obs(pickups=[pk]))
    assert a.get("take") == 8001, a


@case
def picks_good_upgrade():
    b = fresh_bot()
    offer = {"id": 1, "expires": 15, "choices": [{"id": "jets", "name": "", "desc": ""},
                                                  {"id": "laser_pierce", "name": "", "desc": ""},
                                                  {"id": "plating", "name": "", "desc": ""}]}
    a = run(b, obs(offer=offer))
    assert a.get("pick") == 2, a


@case
def laser_charges_then_releases():
    b = fresh_bot()
    e = enemy(5, 100, 0)
    lw = {"kind": "laser", "cooldown": 0, "charge": 0,
          "stats": {"damage": 315, "charge": 0.75, "min_charge": 0.15, "min_frac": 0.35, "hold_s": 2,
                    "cooldown": 1.65, "range": 280}}
    o = obs(mechs=[e])
    o["self"]["weapons"][1] = lw
    a = run(b, o)
    assert a["fire"][1] is True, a
    o2 = copy.deepcopy(o)
    o2["self"]["weapons"][1]["charge"] = 0.75
    o2["self"]["aim"] = [0.0, b.aim_at([OX, OY, OZ + 7], e["pos"][:2] + [e["pos"][2] + 4.5])[1]]
    a2 = run(b, o2)
    assert a2["fire"][1] is False, a2


@case
def lightning_fires_in_cone():
    b = fresh_bot()
    e = enemy(5, 25, 0)
    lw = {"kind": "lightning", "cooldown": 0,
          "stats": {"damage": 60, "cooldown": 0.25, "range": 45, "cone_deg": 30, "chain": 2, "chain_range": 14,
                    "energy": 3}}
    o = obs(mechs=[e])
    o["self"]["weapons"][1] = lw
    a = run(b, o)
    assert a["fire"][1], a


@case
def low_hp_retreats_away():
    from brain import TUNE
    if TUNE["retreat_hp"] <= 0.25:
        return  # retreat disabled in this variant
    b = fresh_bot()
    e = enemy(5, 40, 0)
    a = run(b, obs(mechs=[e], self={"hp": 300}))
    assert a["move"][0] < 0, a


@case
def supply_drop_without_enemies_goes():
    b = fresh_bot()
    d = {"id": 20000, "pos": [OX - 50, OY, OZ], "radius": 8, "lands_in": 5, "capture_s": 4, "progress": 0,
         "holder": None}
    a = run(b, obs(drops=[d]))
    assert a["move"][0] < -0.5, a


@case
def minimal_observation_does_not_crash():
    b = fresh_bot()
    o = {"type": "obs", "tick": 4, "t": 0.2, "alive": 16,
         "self": {"id": 3, "pos": [OX, OY, OZ], "vel": [0, 0, 0], "aim": [0, 0], "hp": 1400, "max_hp": 1400,
                  "energy": 100, "max_energy": 100, "grounded": True, "cubes": 0, "upgrades": [], "in_zone": True,
                  "weapons": [None, None], "stats": {}},
         "zone": {"center": [0, 0], "radius": 560, "next_center": [0, 0], "next_radius": 380}}
    run(b, o)


@case
def late_game_final_circle():
    b = fresh_bot()
    z = {"center": [OX + 30, OY], "radius": 25, "next_center": [OX + 30, OY], "next_radius": 0,
         "shrink_start": 400, "shrink_end": 420, "dps": 260}
    a = run(b, obs(zone=z, t=405.0, alive=2))
    assert a["move"][0] > 0.5, a


if __name__ == "__main__":
    bad = 0
    for name, status, msg in RESULTS:
        print(f"{status:5} {name}" + (f"\n      {msg[:600]}" if msg else ""))
        bad += status != "ok"
    print(f"{len(RESULTS) - bad}/{len(RESULTS)} passed")
    sys.exit(1 if bad else 0)
