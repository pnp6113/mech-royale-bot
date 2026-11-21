"""Tactical scenario tests on synthetic (schema-shaped) observations. Run: python tests/test_scenarios.py

These are unit checks of decisions, not a game simulation.
"""
import copy
import gzip
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "kit", "check"))
import check  # noqa: E402
import nav  # noqa: E402
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


def _oriented_cliff():
    """Adjacent free nav cells with a rise a mech cannot walk up."""
    nnx = MAP.nnx
    for k, blocked in enumerate(MAP.blocked):
        if blocked:
            continue
        j, i = divmod(k, nnx)
        if i + 1 >= MAP.nnx:
            continue
        k2 = k + 1
        if MAP.blocked[k2]:
            continue
        rise = MAP.nground[k2] - MAP.nground[k]
        if abs(rise) > 15.0:
            low, high = (k, k2) if rise > 0 else (k2, k)
            return MAP.ncenter(low), MAP.ncenter(high)
    raise RuntimeError("no cliff pair on this map")


def _wall_face():
    """A free nav cell whose east neighbour is solid."""
    nnx = MAP.nnx
    for j in range(2, MAP.nny - 2):
        for i in range(2, MAP.nnx - 2):
            k = j * nnx + i
            if MAP.blocked[k] or not MAP.blocked[k + 1]:
                continue
            return MAP.ncenter(k), MAP.ncenter(k + 1)
    raise RuntimeError("no wall face on this map")


LOW_CELL, HIGH_CELL = _oriented_cliff()
FREE_CELL, WALL_CELL = _wall_face()


@case
def multiple_attackers_prefers_the_one_shooting_us():
    b = fresh_bot()
    shooter = enemy(7, 70, 0)
    bystander = enemy(8, 45, 20)
    o = obs(mechs=[bystander, shooter], events=[
        {"type": "damaged", "amount": 45, "weapon": "gun", "bearing": 0.0, "by": 7}])
    a = run(b, o)
    shooter_yaw = 0.0
    other_yaw = math.atan2(20.0, 45.0)
    assert abs(wrap(a["aim"][0] - shooter_yaw)) < abs(wrap(a["aim"][0] - other_yaw)), a["aim"]


@case
def dodges_away_from_bullet_passing_north():
    # Bullet passes 1.5 m north of us. Stepping north walks into it.
    b = fresh_bot()
    p = {"id": 11, "kind": "gun", "pos": [OX + 40, OY + 1.5, OZ + 4.5], "vel": [-252, 0, 0]}
    a = run(b, obs(projectiles=[p]))
    assert a["move"][1] < -0.3, a["move"]


@case
def opposing_gunfire_does_not_cancel():
    b = fresh_bot()
    north = {"id": 12, "kind": "gun", "pos": [OX + 30, OY + 1.6, OZ + 4.5], "vel": [-252, 0, 0]}
    south = {"id": 13, "kind": "gun", "pos": [OX + 30, OY - 1.6, OZ + 4.5], "vel": [-252, 0, 0]}
    a = run(b, obs(projectiles=[north, south]))
    assert abs(a["move"][1]) > 0.4, a["move"]


@case
def charging_laser_behind_a_building_does_not_burn_boost():
    b = fresh_bot()
    e = enemy(6, 80, 0, aim=[math.pi, 0.0], charging=True, weapons=["gun", "laser"])
    real = MAP.los
    MAP.los = lambda *a, **k: False
    try:
        a = run(b, obs(mechs=[e], self={"energy": 40}))
    finally:
        MAP.los = real
    assert a["boost"] is False, a


@case
def low_energy_still_runs_into_the_zone():
    b = fresh_bot()
    z = {"center": [OX - 300, OY], "radius": 200, "next_center": [OX - 300, OY], "next_radius": 120,
         "shrink_start": 5, "shrink_end": 40, "dps": 60}
    a = run(b, obs(zone=z, self={"in_zone": False, "energy": 4, "hp": 250}))
    assert a["move"][0] < -0.4 and a["boost"] is False, a


@case
def straight_line_does_not_climb_a_cliff():
    b = fresh_bot()
    lx, ly = LOW_CELL
    dx, dy = b.steer_to(HIGH_CELL, lx, ly, time.perf_counter())
    heading = math.hypot(dx, dy)
    if heading < 1e-6:
        return
    ux, uy = dx / heading, dy / heading
    k0 = MAP.ncell(lx, ly)
    k1 = MAP.ncell(lx + ux * 3.0, ly + uy * 3.0)
    rise = MAP.nground[k1] - MAP.nground[k0]
    assert rise <= MAP.max_rise + 0.2, (round(dx, 3), round(dy, 3), round(rise, 2), LOW_CELL, HIGH_CELL)


@case
def close_goal_does_not_walk_into_a_wall():
    b = fresh_bot()
    fx, fy = FREE_CELL
    dx, dy = b.steer_to(WALL_CELL, fx, fy, time.perf_counter())
    nx, ny = fx + dx * 3.0, fy + dy * 3.0
    assert not MAP.is_blocked(nx, ny), (round(dx, 3), round(dy, 3), FREE_CELL, WALL_CELL)


@case
def stuck_recovery_does_not_push_into_the_wall():
    b = fresh_bot()
    fx, fy = FREE_CELL
    bx, by = WALL_CELL
    d = math.hypot(bx - fx, by - fy) or 1.0
    b.stuck_dir = ((bx - fx) / d, (by - fy) / d)
    b.stuck_until = 1e9
    z = MAP.ground(fx, fy)
    a = run(b, obs(self={"pos": [fx, fy, z]}))
    nx, ny = fx + a["move"][0] * 3.0, fy + a["move"][1] * 3.0
    assert not MAP.is_blocked(nx, ny), (a["move"], FREE_CELL, WALL_CELL)


@case
def swaps_the_weaker_weapon_and_keeps_the_gun():
    b = fresh_bot()
    o = obs(pickups=[{"id": 8099, "kind": "lightning", "pos": [OX + 1.2, OY, OZ]}])
    o["self"]["weapons"][1] = {"kind": "mortar", "cooldown": 0,
                               "stats": {"speed": 72, "gravity": 22, "damage": 200, "splash_radius": 7}}
    a = run(b, o)
    assert a.get("take") == 8099, a
    assert a.get("drop") == 1, a


@case
def unknown_upgrade_still_picks_a_legal_index():
    b = fresh_bot()
    offer = {"id": 9, "expires": 30, "choices": [
        {"id": "not_a_real_upgrade", "name": "", "desc": ""},
        {"id": "armor", "name": "", "desc": ""}]}
    a = run(b, obs(offer=offer))
    assert a.get("pick") == 1, a


@case
def long_match_does_not_grow_memory():
    b = fresh_bot()
    for i in range(400):
        e = enemy(2000 + (i % 80), 40, (i % 5) - 2)
        run(b, obs(tick=2000 + i, t=50.0 + i * 0.5, mechs=[e],
                   events=[{"type": "damaged", "amount": 5, "weapon": "gun", "bearing": 0.2, "by": e["id"]}]))
    assert len(b.enemy_mem) < 60, len(b.enemy_mem)
    assert len(b.hurt_log) < 30, len(b.hurt_log)
    assert len(b.pos_hist) < 40, len(b.pos_hist)


@case
def low_ammo_reloads_when_nothing_is_fighting_us():
    b = fresh_bot()
    o = obs()
    o["self"]["weapons"][0]["ammo"] = 1
    a = run(b, o)
    assert a.get("reload") is True, a


@case
def reload_already_running_is_not_repeated():
    b = fresh_bot()
    o = obs()
    o["self"]["weapons"][0]["ammo"] = 0
    o["self"]["weapons"][0]["reload"] = 1.4
    a = run(b, o)
    assert not a.get("reload"), a
    assert a["fire"][0] is False, a


@case
def missing_optional_fields_do_not_crash():
    b = fresh_bot()
    e = enemy(5, 50, 5)
    for key in ("hp", "max_hp", "cubes", "charging", "weapons"):
        e.pop(key, None)
    o = obs(mechs=[e], events=[{"type": "damaged", "amount": 20, "weapon": "zone", "bearing": None, "by": None}])
    o["self"]["weapons"][0].pop("ammo", None)
    o["self"]["stats"] = {}
    o["drops"] = [{"id": 20001, "pos": [OX - 30, OY, OZ], "radius": 8, "lands_in": 4,
                   "capture_s": 4, "progress": 0, "holder": None}]
    o["offer"] = None
    run(b, o)


@case
def final_circle_with_no_energy_still_moves_in():
    b = fresh_bot()
    z = {"center": [OX + 20, OY], "radius": 25, "next_center": [OX + 20, OY], "next_radius": 0,
         "shrink_start": 400, "shrink_end": 420, "dps": 260}
    a = run(b, obs(zone=z, t=410.0, alive=2, self={"energy": 0, "hp": 400}))
    assert a["move"][0] > 0.4 and a["boost"] is False, a


@case
def protocol_has_no_practice_or_finals_flag():
    # Same observation plus an unknown field must not change the decision.
    # hello has no match-mode field; classification would be a guess.
    b1, b2 = fresh_bot(), fresh_bot()
    e = enemy(5, 55, 0)
    o1 = obs(mechs=[e], tick=77, t=30.0)
    o2 = obs(mechs=[e], tick=77, t=30.0)
    o2["match_mode"] = "finals"
    a1, a2 = b1.act(o1), b2.act(o2)
    assert a1["move"] == a2["move"] and a1["fire"] == a2["fire"] and a1["aim"] == a2["aim"], (a1, a2)
    assert not any(hasattr(b1, name) for name in ("practice", "is_final", "match_mode"))


@case
def lightning_does_not_waste_the_shot_on_a_closer_crate():
    b = fresh_bot()
    e = enemy(5, 36, 0)
    crate = {"id": 1100, "pos": [OX + 12, OY, OZ], "hp": 300}
    lw = {"kind": "lightning", "cooldown": 0,
          "stats": {"damage": 60, "cooldown": 0.25, "range": 45, "cone_deg": 30, "chain": 2,
                    "chain_range": 14, "energy": 3}}
    o = obs(mechs=[e], crates=[crate])
    o["self"]["weapons"][1] = lw
    o["self"]["aim"] = [0.0, 0.0]
    a = run(b, o)
    assert a["fire"][1] is False, a


@case
def mortar_does_not_commit_to_an_unreachable_pitch():
    b = fresh_bot()
    e = enemy(5, 40, 0)
    o = obs(mechs=[e])
    o["self"]["weapons"][1] = {"kind": "mortar", "cooldown": 0,
                               "stats": {"speed": 72, "gravity": 22, "damage": 200, "splash_radius": 7}}
    sol = b.mortar_solution([OX, OY, OZ + 7], e, o["self"]["weapons"][1]["stats"], high=True)
    assert sol is not None and sol[1] > 1.3, sol
    real = MAP.los
    MAP.los = lambda *a, **k: False
    try:
        a = run(b, o)
    finally:
        MAP.los = real
    assert a["fire"][1] is False, a
    assert a["aim"][1] < 0.5, a["aim"]


@case
def intercept_leads_a_rising_target():
    lead = Bot.intercept([0.0, 0.0, 0.0], [200.0, 0.0, 5.0], (0.0, 0.0, 10.0), 252.0)
    assert lead[2] > 12.0, lead


@case
def planner_drops_a_stale_path_when_the_goal_jumps():
    pl = nav.Planner(MAP)
    pl.start(OX, OY, OX + 48, OY)
    assert pl.step(1.0)
    assert pl.path and pl.path_goal is not None
    pl.start(OX, OY, OX, OY + 80)
    assert pl.path == [], (len(pl.path), pl.path_goal, pl.req_goal)


@case
def hidden_pickup_stays_in_memory():
    b = fresh_bot()
    pk = {"id": 8123, "kind": "laser", "pos": [OX + 15, OY, OZ]}
    run(b, obs(pickups=[pk]))
    assert 8123 in b.pickup_mem
    real = MAP.los
    MAP.los = lambda *a, **k: False
    try:
        run(b, obs())
        assert 8123 in b.pickup_mem
    finally:
        MAP.los = real


@case
def short_ledge_does_not_start_a_jet_from_across_the_street():
    b = fresh_bot()
    b.t = 10.0
    b.plan_t = 10.0
    goal = (OX + 22.0, OY)
    b.planner.path = [(OX + 22.0, OY, OZ + 3.5)]
    b.planner.path_goal = goal
    b.planner.active = False
    real = MAP.walk_clear
    MAP.walk_clear = lambda *a, **k: False
    try:
        b.steer_to(goal, OX, OY, time.perf_counter())
        assert b.hop_req is None, b.hop_req
    finally:
        MAP.walk_clear = real
        b.planner.path, b.planner.active, b.planner.path_goal = [], False, None


@case
def short_projectile_vector_does_not_crash():
    b = fresh_bot()
    run(b, obs(projectiles=[{"id": 1, "kind": "gun", "pos": [OX + 20, OY, OZ + 4], "vel": [-20, 0]}]))


@case
def planned_paths_do_not_cross_blocked_cells():
    pl = nav.Planner(MAP)
    spots = []
    for j in range(8, MAP.nny - 8, 24):
        for i in range(8, MAP.nnx - 8, 28):
            k = j * MAP.nnx + i
            if not MAP.blocked[k]:
                spots.append(MAP.ncenter(k))
            if len(spots) >= 8:
                break
        if len(spots) >= 8:
            break
    checked = 0
    for a, c in zip(spots, spots[1:]):
        pl.start(a[0], a[1], c[0], c[1])
        pl.step(0.25)
        if not pl.path:
            continue
        checked += 1
        for x, y, _hop in pl.path:
            assert not MAP.is_blocked(x, y), (x, y)
    assert checked >= 3, checked


if __name__ == "__main__":
    bad = 0
    for name, status, msg in RESULTS:
        print(f"{status:5} {name}" + (f"\n      {msg[:600]}" if msg else ""))
        bad += status != "ok"
    print(f"{len(RESULTS) - bad}/{len(RESULTS)} passed")
    sys.exit(1 if bad else 0)
