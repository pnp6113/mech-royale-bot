"""APPROXIMATE local simulator for mechfight/1. NOT the official game.

Built from kit/protocol.md + hello.config. Server-side details we can't see are simplified:
  - collisions use the 4 m walkability grid (no roofs, no jet-climbing onto buildings)
  - house bots are our own model of "a squad that shares vision and hunts you weakest first"
  - NPC drones are stationary turrets; supply drops are omitted
Use it to catch bugs and compare tactics, never as a substitute for practice scores.
"""
from __future__ import annotations

import copy
import gzip
import json
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import geom  # noqa: E402

TAU = 2 * math.pi


def wrap(a):
    return (a + math.pi) % TAU - math.pi


def load_hello():
    with gzip.open(os.path.join(ROOT, "kit", "check", "stream.jsonl.gz"), "rt") as f:
        h = json.loads(f.readline())
    h["map"]["dir"] = os.path.join(ROOT, "kit", "arena")
    return h


HELLO = load_hello()
CFG = HELLO["config"]
_MAP = None


def get_map():
    global _MAP
    if _MAP is None:
        _MAP = geom.MapModel(HELLO["map"]["dir"], HELLO["map"]["files"], CFG["mech"])
    return _MAP


class Weapon:
    def __init__(self, kind):
        self.kind = kind
        self.cooldown = 0.0
        self.ammo = CFG["weapons"]["gun"]["mag"] if kind == "gun" else None
        self.reload = 0.0
        self.charge = 0.0
        self.equip = 0.0
        self.held_prev = False


class Mech:
    def __init__(self, mid, pos, controller, skill=None):
        self.id = mid
        self.pos = list(pos)
        self.vel = [0.0, 0.0, 0.0]
        self.aim = [0.0, 0.0]
        self.cmd = {"move": [0, 0], "aim": [0, 0], "fire": [False, False], "boost": False, "jump": False}
        self.stats = dict(CFG["mech"])
        self.wstats = copy.deepcopy(CFG["weapons"])
        self.hp = float(self.stats["hp"])
        self.max_hp = self.hp
        self.energy = float(self.stats["energy"])
        self.last_spend = -99.0
        self.last_combat = -99.0
        self.weapons = [Weapon("gun"), None]
        self.cubes = 0
        self.upgrades = []
        self.offers = []
        self.offer = None
        self.offer_id = 0
        self.alive = True
        self.death_t = None
        self.place = None
        self.kills = 0
        self.controller = controller
        self.skill = skill
        self.events = []
        self.grounded = True
        self.seen_t = {}  # id -> last t seen (for memory)
        self.dmg_taken = {"zone": 0.0, "weapon": 0.0}
        self.dmg_log = []  # (t, amount, weapon, attacker_id)
        self.shots = {}
        self.hits = {}

    def scale(self):
        return 1.0 + CFG["cubes"]["size_per_cube"] * self.cubes

    def radius(self):
        return self.stats["radius"] * self.scale()

    def center(self):
        return [self.pos[0], self.pos[1], self.pos[2] + 4.5 * self.scale()]

    def muzzle(self):
        return [self.pos[0], self.pos[1], self.pos[2] + self.stats["muzzle_height"] * self.scale()]

    def eye(self):
        return [self.pos[0], self.pos[1], self.pos[2] + self.stats["eye_height"] * self.scale()]

    def dmg_mult(self):
        return 1.0 + CFG["cubes"]["damage_per_cube"] * min(self.cubes, CFG["cubes"]["stat_cap"])


class Match:
    def __init__(self, seed, our_factory, house_factory, n=16):
        self.rng = random.Random(seed)
        self.m = get_map()
        self.t = 0.0
        self.tick = 0
        self.dt = 1.0 / CFG["match"]["tick_hz"]
        self.mechs = []
        self.npcs = []
        self.crates = []
        self.cubes = []
        self.pickups = []
        self.projectiles = []
        self.next_id = {"npc": 100, "crate": 1000, "cube": 5000, "pick": 8000, "proj": 1}
        self.zone_phase = 0
        z = CFG["zone"]
        self.zc = list(z["start_center"])
        self.zr = float(z["start_radius"])
        self._new_phase(0)
        spawns = self._spawns(n)
        self.our_slot = self.rng.randrange(n)
        skills = ["easy"] * 4 + ["medium"] * 4 + ["hard"] * 4 + ["expert"] * 3
        self.rng.shuffle(skills)
        si = 0
        for i in range(n):
            if i == self.our_slot:
                hello = copy.deepcopy(HELLO)
                hello["slot"] = i
                hello["seed"] = self.rng.randrange(1 << 31)
                mech = Mech(i, spawns[i], None)
                mech.controller = our_factory(hello)
                mech.is_us = True
            else:
                mech = Mech(i, spawns[i], None, skill=skills[si])
                si += 1
                mech.controller = house_factory(self, mech)
                mech.is_us = False
            self.mechs.append(mech)
        self._spawn_loot()
        self.sounds = []  # (t, kind, pos, src_id)

    # ------------------------------------------------------------------ setup
    def free_point(self, cx, cy, r, tries=200):
        for _ in range(tries):
            a = self.rng.uniform(0, TAU)
            d = r * math.sqrt(self.rng.random())
            x, y = cx + math.cos(a) * d, cy + math.sin(a) * d
            if self.m.minx + 30 < x < self.m.maxx - 30 and self.m.miny + 30 < y < self.m.maxy - 30 \
                    and not self.m.is_blocked(x, y):
                return x, y
        return cx, cy

    def _spawns(self, n):
        out = []
        frac = CFG["match"]["spawn_zone_fraction"]
        sep = CFG["match"]["spawn_min_separation"]
        for i in range(n):
            for attempt in range(400):
                x, y = self.free_point(self.zc[0], self.zc[1], self.zr * frac)
                if all(math.hypot(x - a, y - b) > sep * (1 - attempt / 500) for a, b, _ in out):
                    break
            out.append((x, y, self.m.ground(x, y)))
        return out

    def _spawn_loot(self):
        L = CFG["loot"]
        kinds = [k for k, w in L["weights"].items() for _ in range(int(w))]
        for _ in range(L["floor_count"]):
            x, y = self.free_point(self.zc[0], self.zc[1], self.zr * 0.95)
            self.pickups.append({"id": self._nid("pick"), "kind": self.rng.choice(kinds), "pos": [x, y, self.m.ground(x, y)]})
        for _ in range(CFG["crates"]["count"]):
            x, y = self.free_point(self.zc[0], self.zc[1], self.zr * 0.95)
            self.crates.append({"id": self._nid("crate"), "pos": [x, y, self.m.ground(x, y)], "hp": CFG["crates"]["hp"]})
        for _ in range(CFG["npc"]["count"]):
            x, y = self.free_point(self.zc[0], self.zc[1], self.zr * 0.9)
            self.npcs.append({"id": self._nid("npc"), "pos": [x, y, self.m.ground(x, y)], "hp": CFG["npc"]["hp"],
                              "cd": 0.0, "target": None, "vel": [0, 0, 0]})

    def _nid(self, k):
        self.next_id[k] += 1
        return self.next_id[k]

    def _new_phase(self, i):
        z = CFG["zone"]
        ph = z["phases"]
        self.zone_phase = i
        if i >= len(ph):
            self.nc, self.nr, self.ss, self.se, self.dps = self.zc, 0.0, 1e9, 1e9, ph[-1]["dps"]
            return
        p = ph[i]
        nr = float(p["radius"])
        # next circle fully inside the current one
        a = self.rng.uniform(0, TAU)
        d = self.rng.uniform(0, max(0.0, self.zr - nr)) * (CFG["drops"].get("spread", 0.7))
        self.nc = [self.zc[0] + math.cos(a) * d, self.zc[1] + math.sin(a) * d]
        self.nr = nr
        self.ss = self.t + p["hold_s"]
        self.se = self.ss + p["shrink_s"]
        self.dps = p["dps"]
        self.cur_c0, self.cur_r0 = list(self.zc), self.zr

    def blocked_at(self, x, y, z, r, grounded):
        """A wall at this spot stops a mech whose feet are at z (walls taller than step_up above the feet)."""
        m = self.m
        su = CFG["mech"]["step_up"]
        h = CFG["mech"]["height"]
        for dx, dy in ((0, 0), (r, 0), (-r, 0), (0, r), (0, -r), (r * .7, r * .7), (-r * .7, r * .7), (r * .7, -r * .7), (-r * .7, -r * .7)):
            k = m.cell2(x + dx, y + dy)
            if k < 0:
                return True
            tp = m.top[k]
            if tp > geom.NO_SOLID and tp > z + su and m.base[k] < z + h:
                return True
            gr = m.ground2[k]
            if gr > z + (su if grounded else 0.2):
                return True
        return False

    def surface(self, x, y, z):
        m = self.m
        g = m.ground(x, y)
        k = m.cell2(x, y)
        if k >= 0:
            tp = m.top[k]
            if tp > geom.NO_SOLID and g < tp <= z + CFG["mech"]["step_up"] + 0.01:
                return tp
        return g

    def zone_now(self):
        if self.t < self.ss:
            return self.cur_c0, self.cur_r0
        if self.t >= self.se:
            return self.nc, self.nr
        f = (self.t - self.ss) / (self.se - self.ss)
        c = [self.cur_c0[0] + (self.nc[0] - self.cur_c0[0]) * f, self.cur_c0[1] + (self.nc[1] - self.cur_c0[1]) * f]
        return c, self.cur_r0 + (self.nr - self.cur_r0) * f

    # ------------------------------------------------------------------ observation
    def visible(self, a: Mech, pos_center, top=None):
        V = CFG["vision"]
        dx, dy = pos_center[0] - a.pos[0], pos_center[1] - a.pos[1]
        d = math.hypot(dx, dy)
        near = V["near_range"] + a.stats.get("near_range_bonus", 0)
        rng = V["range"] + a.stats.get("vision_bonus", 0)
        if d > rng:
            return False
        if d > near:
            b = math.atan2(dy, dx)
            if abs(wrap(b - a.aim[0])) > math.radians(V["fov_deg"]) / 2:
                return False
        return self.m.los(a.eye(), pos_center) or (top is not None and self.m.los(a.eye(), top))

    def build_obs(self, me: Mech):
        z_c, z_r = self.zone_now()
        mechs = []
        for o in self.mechs:
            if o is me or not o.alive:
                continue
            top = [o.pos[0], o.pos[1], o.pos[2] + 9 * o.scale()]
            if self.visible(me, o.center(), top):
                me.seen_t[o.id] = self.t
            if self.t - me.seen_t.get(o.id, -99) <= CFG["vision"]["memory_s"]:
                mechs.append({"id": o.id, "kind": "mech", "pos": [round(v, 2) for v in o.pos],
                              "vel": [round(v, 2) for v in o.vel], "aim": [round(o.aim[0], 3), round(o.aim[1], 3)],
                              "hp": round(o.hp, 1), "max_hp": round(o.max_hp, 1), "cubes": o.cubes,
                              "weapons": [w.kind for w in o.weapons if w],
                              "charging": any(w and w.kind == "laser" and w.charge > 0 for w in o.weapons)})
        for n in self.npcs:
            c = [n["pos"][0], n["pos"][1], n["pos"][2] + 1.75]
            if self.visible(me, c):
                mechs.append({"id": n["id"], "kind": "npc", "pos": n["pos"], "vel": [0, 0, 0], "aim": [0, 0],
                              "hp": n["hp"], "max_hp": CFG["npc"]["hp"]})
        vis_items = lambda lst, h: [x for x in lst if self.visible(me, [x["pos"][0], x["pos"][1], x["pos"][2] + h])]
        projs = []
        for p in self.projectiles:
            if p["owner"] == me.id:
                continue
            if math.hypot(p["pos"][0] - me.pos[0], p["pos"][1] - me.pos[1]) < 250 and self.visible(me, p["pos"]):
                projs.append({"id": p["id"], "kind": p["kind"], "pos": [round(v, 2) for v in p["pos"]],
                              "vel": [round(v, 2) for v in p["vel"]]})
        sounds = []
        for st, kind, pos, src in self.sounds:
            if src == me.id or self.t - st > 0.11:
                continue
            d = math.hypot(pos[0] - me.pos[0], pos[1] - me.pos[1])
            if d < CFG["sound"]["range"].get(kind, 0):
                b = math.atan2(pos[1] - me.pos[1], pos[0] - me.pos[0]) + self.rng.gauss(0, math.radians(7))
                sounds.append({"kind": kind, "bearing": round(wrap(b), 3),
                               "dist": "near" if d < 60 else ("mid" if d < 160 else "far"), "age": round(self.t - st, 2)})
        weapons = []
        for w in me.weapons:
            if w is None:
                weapons.append(None)
                continue
            d = {"kind": w.kind, "cooldown": round(max(w.cooldown, w.equip), 3), "stats": dict(me.wstats[w.kind])}
            if w.kind == "gun":
                d.update(ammo=w.ammo, reload=round(w.reload, 2))
            if w.kind == "laser":
                d["charge"] = round(w.charge, 3)
            weapons.append(d)
        stats = dict(me.stats)
        stats["eye_height"] = me.stats["eye_height"] * me.scale()
        stats["muzzle_height"] = me.stats["muzzle_height"] * me.scale()
        alive = sum(1 for o in self.mechs if o.alive)
        in_zone = math.hypot(me.pos[0] - z_c[0], me.pos[1] - z_c[1]) <= z_r
        obs = {"type": "obs", "tick": self.tick, "t": round(self.t, 2), "alive": alive,
               "self": {"id": me.id, "pos": [round(v, 2) for v in me.pos], "vel": [round(v, 2) for v in me.vel],
                        "aim": [round(me.aim[0], 4), round(me.aim[1], 4)], "hp": round(me.hp, 1),
                        "max_hp": round(me.max_hp, 1), "energy": round(me.energy, 1),
                        "max_energy": me.stats["energy"], "grounded": me.grounded, "cubes": me.cubes,
                        "upgrades": list(me.upgrades), "in_zone": in_zone, "weapons": weapons, "stats": stats},
               "mechs": mechs, "projectiles": projs,
               "crates": [{"id": c["id"], "pos": c["pos"], "hp": c["hp"]} for c in vis_items(self.crates, 1.2)],
               "cubes": [{"id": c["id"], "pos": c["pos"]} for c in vis_items(self.cubes, 0.5)],
               "pickups": [dict(p) for p in vis_items(self.pickups, 0.5)],
               "sounds": sounds, "beams": [], "events": me.events, "drops": [],
               "zone": {"center": [round(v, 2) for v in self.cur_c0], "radius": round(self.cur_r0, 2),
                        "next_center": [round(v, 2) for v in self.nc], "next_radius": self.nr,
                        "shrink_start": round(self.ss, 2), "shrink_end": round(self.se, 2), "dps": self.dps},
               "offer": me.offer}
        me.events = []
        return obs

    # ------------------------------------------------------------------ damage
    def damage(self, victim, amount, weapon, attacker=None, from_pos=None):
        if not victim.alive:
            return
        rear = False
        if weapon != "zone":
            if from_pos is not None:
                b = math.atan2(from_pos[1] - victim.pos[1], from_pos[0] - victim.pos[0])
                if abs(wrap(b - victim.aim[0])) > math.radians(victim.stats["front_arc_deg"]) / 2:
                    rear = True
                    amount *= victim.stats["rear_mult"]
            amount *= max(0.0, 1.0 - victim.stats.get("armor", 0))
            victim.last_combat = self.t
            victim.dmg_taken["weapon"] += amount
        else:
            victim.dmg_taken["zone"] += amount
        victim.hp -= amount
        victim.dmg_log.append((self.t, amount, weapon, attacker.id if attacker else None))
        if attacker is not None:
            attacker.hits[weapon] = attacker.hits.get(weapon, 0) + 1
        bearing = None
        if from_pos is not None:
            bearing = round(math.atan2(from_pos[1] - victim.pos[1], from_pos[0] - victim.pos[0]), 3)
        ev = {"type": "damaged", "amount": round(amount, 1), "weapon": weapon, "bearing": bearing,
              "by": attacker.id if attacker else None}
        if rear:
            ev["rear"] = True
        victim.events.append(ev)
        if attacker is not None:
            attacker.last_combat = self.t
            attacker.events.append({"type": "hit", "target": victim.id, "amount": round(amount, 1), "weapon": weapon})
        if victim.hp <= 0:
            victim.alive = False
            victim.death_t = self.t
            if attacker is not None and attacker is not victim:
                attacker.kills += 1
                attacker.events.append({"type": "kill", "target": victim.id})
            for _ in range(victim.cubes + CFG["cubes"]["death_drop_extra"]):
                self.cubes.append({"id": self._nid("cube"), "pos": [victim.pos[0] + self.rng.uniform(-3, 3),
                                                                     victim.pos[1] + self.rng.uniform(-3, 3), victim.pos[2]]})
            for w in victim.weapons[1:]:
                if w:
                    self.pickups.append({"id": self._nid("pick"), "kind": w.kind, "pos": list(victim.pos)})
            self.sounds.append((self.t, "death", list(victim.pos), victim.id))

    def damage_npc(self, n, amount, attacker):
        n["hp"] -= amount
        n["target"] = attacker.id
        attacker.events.append({"type": "hit", "target": n["id"], "amount": round(amount, 1), "weapon": "x"})
        if n["hp"] <= 0:
            self.npcs.remove(n)
            for _ in range(CFG["npc"]["cubes"]):
                self.cubes.append({"id": self._nid("cube"), "pos": [n["pos"][0] + self.rng.uniform(-2, 2),
                                                                     n["pos"][1] + self.rng.uniform(-2, 2), n["pos"][2]]})

    def damage_crate(self, c, amount):
        c["hp"] -= amount
        if c["hp"] <= 0 and c in self.crates:
            self.crates.remove(c)
            self.cubes.append({"id": self._nid("cube"), "pos": list(c["pos"])})
            if self.rng.random() < CFG["loot"]["crate_weapon_chance"]:
                kinds = [k for k, w in CFG["loot"]["weights"].items() for _ in range(int(w))]
                self.pickups.append({"id": self._nid("pick"), "kind": self.rng.choice(kinds), "pos": list(c["pos"])})

    # ------------------------------------------------------------------ step
    def decide(self):
        for me in self.mechs:
            if not me.alive:
                continue
            obs = self.build_obs(me)
            try:
                a = me.controller.act(obs) or {}
            except Exception as e:  # a crash = idle
                import traceback
                if getattr(me, "is_us", False):
                    traceback.print_exc()
                a = {}
            self.apply_action(me, a)

    def apply_action(self, me, a):
        c = me.cmd
        mv = a.get("move", [0, 0])
        L = math.hypot(mv[0], mv[1])
        if L > 1:
            mv = [mv[0] / L, mv[1] / L]
        c["move"] = mv
        c["boost"] = bool(a.get("boost", False))
        c["jump"] = bool(a.get("jump", False))
        if "aim" in a:
            c["aim"] = [float(a["aim"][0]), max(me.stats["min_pitch"], min(me.stats["max_pitch"], float(a["aim"][1])))]
        c["fire"] = list(a.get("fire", [False, False]))[:2] + [False] * max(0, 2 - len(a.get("fire", [])))
        if a.get("reload"):
            g = me.weapons[0]
            for w in me.weapons:
                if w and w.kind == "gun" and w.reload == 0 and w.ammo < me.wstats["gun"]["mag"]:
                    w.reload = me.wstats["gun"]["reload"]
        if a.get("pick") is not None and me.offer:
            i = int(a["pick"])
            if 0 <= i < len(me.offer["choices"]):
                self.apply_upgrade(me, me.offer["choices"][i]["id"])
                me.offer = None
        if a.get("take") is not None:
            self.take(me, a["take"], a.get("drop"))
        elif a.get("drop") is not None:
            i = a["drop"]
            if 0 <= i < 2 and me.weapons[i]:
                self.pickups.append({"id": self._nid("pick"), "kind": me.weapons[i].kind, "pos": list(me.pos)})
                me.weapons[i] = None

    def take(self, me, pid, drop):
        p = next((p for p in self.pickups if p["id"] == pid), None)
        if p is None:
            me.events.append({"type": "take_failed", "id": pid, "reason": "gone"})
            return
        if math.hypot(p["pos"][0] - me.pos[0], p["pos"][1] - me.pos[1]) > CFG["loot"]["pickup_radius"]:
            me.events.append({"type": "take_failed", "id": pid, "reason": "range"})
            return
        if any(w and w.kind == p["kind"] for w in me.weapons):
            me.events.append({"type": "take_failed", "id": pid, "reason": "have"})
            return
        slot = None
        if drop is not None and 0 <= drop < 2:
            slot = drop
        elif me.weapons[1] is None:
            slot = 1
        elif me.weapons[0] is None:
            slot = 0
        if slot is None:
            me.events.append({"type": "take_failed", "id": pid, "reason": "full"})
            return
        if me.weapons[slot]:
            self.pickups.append({"id": self._nid("pick"), "kind": me.weapons[slot].kind, "pos": list(me.pos)})
        w = Weapon(p["kind"])
        w.equip = max(CFG["loot"]["equip_s"], me.wstats[w.kind]["cooldown"])
        me.weapons[slot] = w
        self.pickups.remove(p)
        me.events.append({"type": "took", "id": pid, "kind": p["kind"], "slot": slot})

    def apply_upgrade(self, me, uid):
        u = next((u for u in CFG["upgrades"] if u["id"] == uid), None)
        if not u:
            return
        me.upgrades.append(uid)
        for eff in u["effects"]:
            path = eff["path"].split(".")
            if path[0] == "mech":
                tgt, key = me.stats, path[1]
            else:
                tgt, key = me.wstats[path[1]], path[2]
            if "mul" in eff:
                tgt[key] = tgt.get(key, 0) * eff["mul"]
            if "add" in eff:
                tgt[key] = tgt.get(key, 0) + eff["add"]
            if key == "hp":
                old = me.max_hp
                me.max_hp = me.stats["hp"] * (1 + CFG["cubes"]["hp_per_cube"] * min(me.cubes, CFG["cubes"]["stat_cap"]))
                me.hp += me.max_hp - old
        me.events.append({"type": "upgrade", "id": uid})

    def grant_offer(self, me):
        taken = {u: me.upgrades.count(u) for u in me.upgrades}
        kinds = {w.kind for w in me.weapons if w}
        avail = [u for u in CFG["upgrades"] if taken.get(u["id"], 0) < u["max"]
                 and (not u.get("requires") or u["requires"] in kinds)]
        self.rng.shuffle(avail)
        ch = avail[:3]
        if ch:
            me.offer_id += 1
            me.offer = {"id": me.offer_id, "expires": round(self.t + 5, 2),
                        "choices": [{"id": u["id"], "name": u["name"], "desc": u["desc"]} for u in ch]}

    def physics(self):
        dt = self.dt
        m = self.m
        for me in self.mechs:
            if not me.alive:
                continue
            st = me.stats
            c = me.cmd
            charging = any(w and w.kind == "laser" and w.charge > 0 for w in me.weapons)
            spd = st["boost_speed"] if (c["boost"] and me.energy > 0 and me.grounded) else st["walk_speed"]
            if charging:
                spd *= st["charge_move_mult"]
            if c["boost"] and me.energy > 0:
                me.energy -= (st["boost_drain"] if me.grounded else st["jet_drain"]) * dt
                me.last_spend = self.t
            tvx, tvy = c["move"][0] * spd, c["move"][1] * spd
            acc = st["ground_accel"] if me.grounded else st["air_accel"]
            for i, tv in ((0, tvx), (1, tvy)):
                dv = tv - me.vel[i]
                step = acc * dt
                me.vel[i] += max(-step, min(step, dv))
            # vertical
            if c["jump"] and me.grounded:
                me.vel[2] = st["jump_speed"]
                me.grounded = False
            if not me.grounded:
                me.vel[2] -= st["gravity"] * dt
                if c["boost"] and me.energy > 0:
                    me.vel[2] = min(me.vel[2] + st["jet_accel"] * dt, st["max_rise_speed"])
            z = me.pos[2]
            nx, ny = me.pos[0] + me.vel[0] * dt, me.pos[1] + me.vel[1] * dt
            nx = max(m.minx + 5, min(m.maxx - 5, nx))
            ny = max(m.miny + 5, min(m.maxy - 5, ny))
            r = me.radius()
            if self.blocked_at(nx, ny, z, r, me.grounded) and not self.blocked_at(me.pos[0], me.pos[1], z, r, me.grounded):
                if not self.blocked_at(nx, me.pos[1], z, r, me.grounded):
                    ny = me.pos[1]
                    me.vel[1] = 0.0
                elif not self.blocked_at(me.pos[0], ny, z, r, me.grounded):
                    nx = me.pos[0]
                    me.vel[0] = 0.0
                else:
                    nx, ny = me.pos[0], me.pos[1]
                    me.vel[0] = me.vel[1] = 0.0
            g = self.surface(nx, ny, z)
            nz = z + me.vel[2] * dt
            if nz <= g:
                nz = g
                me.vel[2] = 0.0
                me.grounded = True
            elif me.grounded and nz - g < st["step_up"] and me.vel[2] <= 0:
                nz = g  # walking down a gentle slope / small step
            else:
                me.grounded = False
            me.pos = [nx, ny, nz]
            # energy regen
            if self.t - me.last_spend > st["energy_regen_delay"]:
                me.energy = min(st["energy"], me.energy + st["energy_regen"] * dt)
            # aim slew
            dy = wrap(c["aim"][0] - me.aim[0])
            mx = st["turn_rate"] * dt
            me.aim[0] = wrap(me.aim[0] + max(-mx, min(mx, dy)))
            dp = c["aim"][1] - me.aim[1]
            mp = st["pitch_rate"] * dt
            me.aim[1] += max(-mp, min(mp, dp))
            # hp regen
            zc, zr = self.zone_now()
            inside = math.hypot(me.pos[0] - zc[0], me.pos[1] - zc[1]) <= zr
            if inside and self.t - me.last_combat > st["regen_delay"]:
                me.hp = min(me.max_hp, me.hp + st["regen_per_s"] * dt)
            if not inside:
                self.damage(me, self.dps * dt, "zone")
                me.last_combat = self.t
            # pickups of cubes
            for cb in list(self.cubes):
                if math.hypot(cb["pos"][0] - me.pos[0], cb["pos"][1] - me.pos[1]) < CFG["cubes"]["pickup_radius"]:
                    self.cubes.remove(cb)
                    me.cubes += 1
                    old = me.max_hp
                    me.max_hp = me.stats["hp"] * (1 + CFG["cubes"]["hp_per_cube"] * min(me.cubes, CFG["cubes"]["stat_cap"]))
                    me.hp += me.max_hp - old
                    me.events.append({"type": "pickup", "cubes": me.cubes})
                    if me.cubes in CFG["cubes"]["upgrade_thresholds"]:
                        if me.offer is None:
                            self.grant_offer(me)
            if me.offer and self.t > me.offer["expires"]:
                self.apply_upgrade(me, me.offer["choices"][0]["id"])
                me.offer = None
            # weapons
            for i, w in enumerate(me.weapons):
                if w is None:
                    continue
                w.cooldown = max(0.0, w.cooldown - dt)
                w.equip = max(0.0, w.equip - dt)
                if w.kind == "gun" and w.reload > 0:
                    w.reload -= dt
                    if w.reload <= 0:
                        w.reload = 0.0
                        w.ammo = me.wstats["gun"]["mag"]
                held = bool(c["fire"][i]) if i < len(c["fire"]) else False
                self.fire_weapon(me, w, held)
                w.held_prev = held
            if me.cmd.get("move") and math.hypot(me.vel[0], me.vel[1]) > 5 and self.tick % 12 == 0:
                self.sounds.append((self.t, "step", list(me.pos), me.id))

    def fire_weapon(self, me, w, held):
        ws = me.wstats[w.kind]
        if w.equip > 0:
            if w.kind == "laser":
                w.charge = 0.0
            return
        if w.kind == "gun":
            if held and w.cooldown <= 0 and w.reload <= 0 and w.ammo > 0:
                w.cooldown = ws["cooldown"]
                me.shots["gun"] = me.shots.get("gun", 0) + 1
                w.ammo -= 1
                if w.ammo <= 0:
                    w.reload = ws["reload"]
                sp = math.radians(ws["spread_deg"])
                yaw = me.aim[0] + self.rng.gauss(0, sp)
                pit = me.aim[1] + self.rng.gauss(0, sp)
                d = [math.cos(pit) * math.cos(yaw), math.cos(pit) * math.sin(yaw), math.sin(pit)]
                mz = me.muzzle()
                start = [mz[0] + d[0] * 3, mz[1] + d[1] * 3, mz[2] + d[2] * 3]
                self.projectiles.append({"id": self._nid("proj"), "kind": "gun", "owner": me.id, "pos": start,
                                         "vel": [d[0] * ws["speed"], d[1] * ws["speed"], d[2] * ws["speed"]],
                                         "ttl": ws["range"] / ws["speed"], "dmg": ws["damage"] * me.dmg_mult()})
                self.sounds.append((self.t, "gun", list(me.pos), me.id))
        elif w.kind == "mortar":
            if held and w.cooldown <= 0:
                w.cooldown = ws["cooldown"]
                yaw, pit = me.aim
                d = [math.cos(pit) * math.cos(yaw), math.cos(pit) * math.sin(yaw), math.sin(pit)]
                mz = me.muzzle()
                mz[2] += 2.5
                self.projectiles.append({"id": self._nid("proj"), "kind": "mortar", "owner": me.id,
                                         "pos": [mz[0] + d[0] * 3, mz[1] + d[1] * 3, mz[2] + d[2] * 3],
                                         "vel": [d[0] * ws["speed"], d[1] * ws["speed"], d[2] * ws["speed"]],
                                         "ttl": ws["ttl"], "dmg": ws["damage"] * me.dmg_mult(),
                                         "splash": ws["splash"] * me.dmg_mult(), "srad": ws["splash_radius"]})
                self.sounds.append((self.t, "mortar", list(me.pos), me.id))
        elif w.kind == "lightning":
            if held and w.cooldown <= 0 and me.energy >= ws["energy"]:
                w.cooldown = ws["cooldown"]
                me.energy -= ws["energy"]
                me.last_spend = self.t
                self.sounds.append((self.t, "lightning", list(me.pos), me.id))
                mz = me.muzzle()
                cone = math.radians(ws["cone_deg"]) / 2
                cands = []
                for o in self.mechs:
                    if o is me or not o.alive:
                        continue
                    cands.append(("mech", o, o.center()))
                for n in self.npcs:
                    cands.append(("npc", n, [n["pos"][0], n["pos"][1], n["pos"][2] + 1.75]))
                for cr in self.crates:
                    cands.append(("crate", cr, [cr["pos"][0], cr["pos"][1], cr["pos"][2] + 1.2]))
                best = None
                for kind, obj, cpos in cands:
                    d = math.hypot(cpos[0] - me.pos[0], cpos[1] - me.pos[1])
                    if d > ws["range"]:
                        continue
                    if abs(wrap(math.atan2(cpos[1] - me.pos[1], cpos[0] - me.pos[0]) - me.aim[0])) > cone:
                        continue
                    if not self.m.los(mz, cpos):
                        continue
                    if best is None or d < best[0]:
                        best = (d, kind, obj, cpos)
                if best:
                    dmg = ws["damage"] * me.dmg_mult()
                    chain = [best]
                    hit_ids = {id(best[2])}
                    last = best[3]
                    for _ in range(int(ws["chain"])):
                        nb = None
                        for kind, obj, cpos in cands:
                            if id(obj) in hit_ids:
                                continue
                            d = math.dist(last, cpos)
                            if d <= ws["chain_range"] and (nb is None or d < nb[0]):
                                nb = (d, kind, obj, cpos)
                        if nb is None:
                            break
                        chain.append(nb)
                        hit_ids.add(id(nb[2]))
                        last = nb[3]
                    for k, (d, kind, obj, cpos) in enumerate(chain):
                        amt = dmg * (ws["chain_falloff"] ** k)
                        if kind == "mech":
                            if any(x and x.kind == "laser" for x in obj.weapons):
                                for x in obj.weapons:
                                    if x and x.kind == "laser":
                                        x.charge = 0.0
                            self.damage(obj, amt, "lightning", me, me.pos)
                        elif kind == "npc":
                            self.damage_npc(obj, amt, me)
                        else:
                            self.damage_crate(obj, amt)
        elif w.kind == "laser":
            if held and w.cooldown <= 0:
                w.charge += self.dt
                if w.charge >= ws["charge"] + ws["hold_s"]:
                    self._laser_shot(me, w, ws)
            elif w.charge > 0:
                if w.charge >= ws["min_charge"] or not held:
                    self._laser_shot(me, w, ws)

    def _laser_shot(self, me, w, ws):
        frac = min(1.0, w.charge / ws["charge"])
        dmg = ws["damage"] * (ws["min_frac"] + (1 - ws["min_frac"]) * frac) * me.dmg_mult()
        w.charge = 0.0
        w.cooldown = ws["cooldown"]
        self.sounds.append((self.t, "laser", list(me.pos), me.id))
        yaw, pit = me.aim
        d = [math.cos(pit) * math.cos(yaw), math.cos(pit) * math.sin(yaw), math.sin(pit)]
        mz = me.muzzle()
        best = None
        for o in self.mechs:
            if o is me or not o.alive:
                continue
            t = self._ray_cyl(mz, d, o.pos, o.radius(), 9 * o.scale(), ws["range"])
            if t is not None and (best is None or t < best[0]):
                best = (t, o)
        if best:
            hit = [mz[0] + d[0] * best[0], mz[1] + d[1] * best[0], mz[2] + d[2] * best[0]]
            if self.m.los(mz, hit):
                o = best[1]
                if any(x and x.kind == "mortar" for x in o.weapons):
                    dmg *= 1 + ws["cookoff"]
                self.damage(o, dmg, "laser", me, me.pos)

    @staticmethod
    def _ray_cyl(o, d, base, r, h, maxd):
        # horizontal circle intersection then height check
        fx, fy = o[0] - base[0], o[1] - base[1]
        a = d[0] * d[0] + d[1] * d[1]
        if a < 1e-9:
            return None
        b = 2 * (fx * d[0] + fy * d[1])
        c = fx * fx + fy * fy - r * r
        disc = b * b - 4 * a * c
        if disc < 0:
            return None
        t = (-b - math.sqrt(disc)) / (2 * a)
        if t < 0 or t > maxd:
            return None
        z = o[2] + d[2] * t
        if base[2] <= z <= base[2] + h:
            return t
        return None

    def projectiles_step(self):
        dt = self.dt
        g = CFG["weapons"]["mortar"]["gravity"]
        keep = []
        for p in self.projectiles:
            p["ttl"] -= dt
            x0 = list(p["pos"])
            if p["kind"] == "mortar":
                p["vel"][2] -= g * dt
            p["pos"] = [x0[0] + p["vel"][0] * dt, x0[1] + p["vel"][1] * dt, x0[2] + p["vel"][2] * dt]
            hit = False
            # mech hit along segment
            seg = [p["pos"][i] - x0[i] for i in range(3)]
            L = math.sqrt(sum(s * s for s in seg))
            d = [s / L for s in seg] if L > 0 else [1, 0, 0]
            best = None
            for o in self.mechs:
                if not o.alive or o.id == p["owner"]:
                    continue
                t = self._ray_cyl(x0, d, o.pos, o.radius() + CFG["projectile"]["gun_radius"], 9 * o.scale(), L)
                if t is not None and (best is None or t < best[0]):
                    best = (t, o)
            if p["kind"] in ("gun", "npc"):
                for n in self.npcs:
                    if p["owner"] == n["id"]:
                        continue
                    t = self._ray_cyl(x0, d, n["pos"], 1.8, 3.5, L)
                    if t is not None and (best is None or t < best[0]):
                        best = (t, n)
                for cr in self.crates:
                    t = self._ray_cyl(x0, d, cr["pos"], 1.4, 2.4, L)
                    if t is not None and (best is None or t < best[0]):
                        best = (t, cr)
            wall = not self.m.los(x0, p["pos"], step=1.5)
            owner = self.mechs[p["owner"]] if isinstance(p["owner"], int) and p["owner"] < 16 else None
            if best is not None:
                obj = best[1]
                if isinstance(obj, Mech):
                    if p["kind"] == "mortar":
                        self.explode(p, p["pos"], owner, direct=obj)
                    else:
                        self.damage(obj, p["dmg"], "npc" if p["kind"] == "npc" else "gun", owner, x0)
                elif owner is not None:
                    if "hp" in obj and obj in self.npcs:
                        self.damage_npc(obj, p["dmg"], owner)
                    elif obj in self.crates:
                        self.damage_crate(obj, p["dmg"])
                hit = True
            elif wall or p["pos"][2] < self.m.ground(p["pos"][0], p["pos"][1]):
                if p["kind"] == "mortar":
                    self.explode(p, x0, owner)
                hit = True
            if not hit and p["ttl"] > 0:
                keep.append(p)
        self.projectiles = keep

    def explode(self, p, at, owner, direct=None):
        self.sounds.append((self.t, "explosion", list(at), -1))
        if direct is not None:
            self.damage(direct, p["dmg"], "mortar", owner, at)
        for o in self.mechs:
            if not o.alive or o is direct:
                continue
            d = math.dist(at, o.center())
            if d < p["srad"] + o.radius() and self.m.los(at, o.center()):
                f = 1 - (1 - CFG["weapons"]["mortar"]["splash_edge"]) * min(1.0, d / p["srad"])
                amt = p["splash"] * f
                if owner is o:
                    amt *= o.stats["self_damage_mult"]
                self.damage(o, amt, "mortar", owner, at)
                ux, uy = o.pos[0] - at[0], o.pos[1] - at[1]
                L = math.hypot(ux, uy) or 1
                o.vel[0] += ux / L * CFG["weapons"]["mortar"]["knock"] * f
                o.vel[1] += uy / L * CFG["weapons"]["mortar"]["knock"] * f
                o.energy = max(0.0, o.energy - CFG["weapons"]["mortar"]["drain"] * f)

    def npcs_step(self):
        for n in self.npcs:
            n["cd"] = max(0.0, n["cd"] - self.dt)
            if n["cd"] > 0:
                continue
            tgt = None
            if n["target"] is not None:
                o = self.mechs[n["target"]]
                if o.alive and math.hypot(o.pos[0] - n["pos"][0], o.pos[1] - n["pos"][1]) < CFG["npc"]["deaggro_range"]:
                    tgt = o
                else:
                    n["target"] = None
            if tgt is None:
                for o in self.mechs:
                    if o.alive and math.hypot(o.pos[0] - n["pos"][0], o.pos[1] - n["pos"][1]) < CFG["npc"]["aggro_range"]:
                        tgt = o
                        break
            if tgt is None:
                continue
            src = [n["pos"][0], n["pos"][1], n["pos"][2] + 3.0]
            c = tgt.center()
            if not self.m.los(src, c):
                continue
            n["cd"] = CFG["npc"]["cooldown"]
            d = [c[i] - src[i] for i in range(3)]
            L = math.sqrt(sum(x * x for x in d))
            sp = CFG["npc"]["projectile_speed"]
            yaw = math.atan2(d[1], d[0]) + self.rng.gauss(0, math.radians(CFG["npc"]["spread_deg"]))
            pit = math.asin(d[2] / L)
            v = [math.cos(pit) * math.cos(yaw) * sp, math.cos(pit) * math.sin(yaw) * sp, math.sin(pit) * sp]
            self.projectiles.append({"id": self._nid("proj"), "kind": "npc", "owner": n["id"], "pos": src, "vel": v,
                                     "ttl": 2.0, "dmg": CFG["npc"]["damage"]})

    def zone_step(self):
        if self.t >= self.se and self.zone_phase < len(CFG["zone"]["phases"]):
            self.zc, self.zr = list(self.nc), self.nr
            self._new_phase(self.zone_phase + 1)

    def run(self, max_t=None):
        max_t = max_t or CFG["match"]["duration_s"]
        dec_every = CFG["match"]["decide_every"]
        while True:
            alive = [o for o in self.mechs if o.alive]
            us = self.mechs[self.our_slot]
            if len(alive) <= 1 or self.t >= max_t or (not us.alive):
                break
            if self.tick % dec_every == 0:
                self.decide()
            before = {o.id for o in self.mechs if o.alive}
            self.physics()
            self.projectiles_step()
            self.npcs_step()
            self.zone_step()
            self.sounds = [s for s in self.sounds if self.t - s[0] < 0.25]
            after = {o.id for o in self.mechs if o.alive}
            died = before - after
            if died:
                for i in died:
                    self.mechs[i].place = len(after) + 1
            self.tick += 1
            self.t = self.tick * self.dt
        us = self.mechs[self.our_slot]
        alive = [o for o in self.mechs if o.alive]
        if us.alive:
            # survivors ranked by HP at timeout
            ranked = sorted(alive, key=lambda o: -o.hp)
            us.place = 1 + ranked.index(us)
        by_w = {}
        for (tt, amt, wpn, att) in us.dmg_log:
            by_w[wpn] = by_w.get(wpn, 0) + round(amt)
        last = [a for (tt, amt, wpn, a) in us.dmg_log if tt > self.t - 15 and a is not None]
        return {"dmg_by": by_w, "attackers15": len(set(last)), "gun_acc": round(us.hits.get("gun", 0) / max(1, us.shots.get("gun", 0)), 2),
                "dealt": {k: v for k, v in us.hits.items()},
                "place": us.place, "score": 16 - us.place, "kills": us.kills, "t": round(self.t, 1),
                "cubes": us.cubes, "zone_dmg": round(us.dmg_taken["zone"]), "weapons": [w.kind if w else None for w in us.weapons],
                "alive_end": len(alive)}
