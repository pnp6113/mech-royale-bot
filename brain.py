"""Last Mech Standing: survival-first utility bot for mechfight/1.

One decision per observation. Every number that the server sends in hello.config / obs.self.stats is read
from there; the constants below are only tuning weights.
"""
from __future__ import annotations

import math
import random
import time

import mechfight as mf

try:
    import geom
    import nav
except Exception:  # pragma: no cover - map code is optional, the bot still runs without it
    geom = nav = None

TAU = 2 * math.pi

# Strategy knobs: one place to change per experiment (see docs/EXPERIMENTS.md)
TUNE = {
    "retreat_hp": 0.32,     # retreat below this HP fraction (0 disables); v1b tested 0 -> 3.33 vs 3.42
    "cube_grab_r": 25.0,
    "weapon_rush_r": 80.0,
    "gun_want": 55.0,       # preferred fight distance with gun only (m)
    "laser_want": 110.0,    # preferred fight distance with a laser (m)
    "lightning_want": 0.62, # preferred distance as a fraction of lightning range
    "strafe": 0.9,          # sideways weight while fighting
    "npc_hp_min": 0.6,      # hunt NPC drones only above this HP fraction  # with slot 2 empty, go for a seen lightning/laser within this radius even mid-fight
    "smart_targets": True,  # target bonuses: charging lasers in lightning range, mortar carriers vs laser, low HP
    "cover": True,          # retreat to a spot that breaks line of fire (regen needs 5 s without damage)
    "regen_hold": 0.75,     # below this HP fraction with no enemies: stop shooting loot and hold still (0 = off)    # grab cubes within this radius in any non-zone mode (0 disables; v1 had none)
}

# preference for loot weapons (higher is better); the gun is the spawn weapon
WEAPON_VALUE = {"lightning": 3.0, "laser": 2.6, "mortar": 1.2, "gun": 1.0}

UPGRADE_VALUE = {
    "plating": 10.0, "armor": 9.6, "nanites": 7.5, "gyros": 4.5, "servos": 6.0, "capacitor": 5.0,
    "reactor": 5.5, "jets": 3.5, "sensors": 4.0,
    "gun_rate": 7.2, "gun_damage": 7.4, "gun_mag": 6.0, "gun_velocity": 5.5,
    "lightning_efficiency": 8.0, "lightning_range": 7.0, "lightning_chain": 4.0,
    "laser_damage": 7.6, "laser_charge": 7.0, "laser_pierce": 2.0,
    "mortar_reload": 4.5, "mortar_splash": 3.5, "mortar_velocity": 3.5,
}


def wrap(a: float) -> float:
    return (a + math.pi) % TAU - math.pi


def norm2(x: float, y: float):
    d = math.hypot(x, y)
    if d < 1e-9:
        return 0.0, 0.0, 0.0
    return x / d, y / d, d


class Bot:
    name = "LastMechStanding"

    def __init__(self, hello):
        cfg = hello.get("config") or {}
        self.cfg = cfg
        self.rng = random.Random(hello.get("seed", 0))
        self.slot = hello.get("slot", -1)
        self.map = None
        self.planner = None
        mp = hello.get("map") or {}
        if geom is not None:
            import os
            t_map = time.perf_counter()
            dirs = [d for d in (mp.get("dir"), "/arena") if d]
            for d in dirs:
                if not os.path.isdir(d):
                    continue
                try:
                    self.map = geom.MapModel(d, mp.get("files") or [], cfg.get("mech") or {})
                    self.planner = nav.Planner(self.map)
                    mf.log(f"map loaded from {d} in {time.perf_counter() - t_map:.2f} s")
                    break
                except Exception as e:  # keep playing without a map
                    mf.log("map load failed:", d, repr(e))
                    self.map = None
            if self.map is None:
                mf.log("map unavailable, tried", dirs)
        self.weapons_cfg = cfg.get("weapons") or {}
        self.loot_cfg = cfg.get("loot") or {}
        self.vision_cfg = cfg.get("vision") or {}
        self.cubes_cfg = cfg.get("cubes") or {}
        self.drops_cfg = cfg.get("drops") or {}
        self.upgrade_defs = {u.get("id"): u for u in (cfg.get("upgrades") or [])}
        # memory
        self.t = 0.0
        self.last_hurt_t = -99.0
        self.last_hit_t = -99.0
        self.hurt_log = []  # (t, amount, bearing, by)
        self.attackers = {}  # id -> (t, dmg)
        self.enemy_mem = {}  # id -> dict(pos, vel, t, hp, max_hp, weapons)
        self.pickup_mem = {}  # id -> (kind, pos, t)
        self.pos_hist = []  # (t, x, y)
        self.stuck_until = -1.0
        self.stuck_dir = (0.0, 0.0)
        self.strafe_sign = 1.0
        self.strafe_switch_t = 0.0
        self.goal = None
        self.goal_kind = ""
        self.plan_t = -99.0
        self.last_move = (0.0, 0.0)
        self.mode = "start"
        self.prev_cmd_aim = None
        self.laser_hold = False
        self.hop_req = None  # (height, t) set by steer_to when the next waypoint is a hop landing
        self.loot_black = {}  # id -> t until which we ignore it
        self.loot_track = None  # (id, since_t, hp_at_since)
        self.cover_cache = None
        # the static map is large and permanent: keep the cyclic GC from rescanning it every tick
        import gc
        gc.collect()
        gc.freeze()

    # ------------------------------------------------------------------ helpers
    def wstats(self, w, kind):
        if w and w.get("stats"):
            return w["stats"]
        return self.weapons_cfg.get(kind, {})

    def los(self, a, b) -> bool:
        if self.map is None:
            return True
        return self.map.los(a, b)

    def ground(self, x, y, fallback=0.0):
        if self.map is None:
            return fallback
        return self.map.ground(x, y)

    # ------------------------------------------------------------------ main
    def act(self, obs):
        t0 = time.perf_counter()
        self.t = t = float(obs.get("t", 0.0))
        me = obs["self"]
        px, py, pz = me["pos"]
        vx, vy, vz = (me.get("vel") or [0, 0, 0])[:3]
        stats = me.get("stats") or {}
        scale = 1.0 + float((self.cubes_cfg.get("size_per_cube") or 0.05)) * float(me.get("cubes", 0))
        muzzle = [px, py, pz + float(stats.get("muzzle_height", 7.0))]
        eye = [px, py, pz + float(stats.get("eye_height", 8.0))]
        hp, max_hp = float(me.get("hp", 1)), float(me.get("max_hp", 1400))
        hp_frac = hp / max(1.0, max_hp)
        energy = float(me.get("energy", 0))
        walk = float(stats.get("walk_speed", 11.7))
        act = {}

        weapons = me.get("weapons") or [None, None]
        kinds = [w.get("kind") if w else None for w in weapons]

        # ---- events
        for ev in obs.get("events") or []:
            typ = ev.get("type")
            if typ == "damaged":
                self.last_hurt_t = t
                self.hurt_log.append((t, float(ev.get("amount", 0)), ev.get("bearing"), ev.get("by")))
                by = ev.get("by")
                if by is not None:
                    pt, pd = self.attackers.get(by, (t, 0.0))
                    self.attackers[by] = (t, pd * 0.8 + float(ev.get("amount", 0)))
            elif typ == "hit":
                self.last_hit_t = t
            elif typ in ("took", "take_failed"):
                self.pickup_mem.pop(ev.get("id"), None)
        self.hurt_log = [h for h in self.hurt_log if t - h[0] < 6.0]
        for k in [k for k, v in self.attackers.items() if t - v[0] > 8.0]:
            del self.attackers[k]

        # ---- world lists
        mechs = obs.get("mechs") or []
        enemies = [m for m in mechs if m.get("kind") == "mech"]
        npcs = [m for m in mechs if m.get("kind") == "npc"]
        for e in enemies:
            self.enemy_mem[e["id"]] = {"pos": e["pos"], "vel": e.get("vel") or [0, 0, 0], "t": t,
                                       "hp": e.get("hp"), "weapons": e.get("weapons") or []}
        for k in [k for k, v in self.enemy_mem.items() if t - v["t"] > 20.0]:
            del self.enemy_mem[k]
        for p in obs.get("pickups") or []:
            self.pickup_mem[p["id"]] = (p.get("kind"), p["pos"], t)
        seen_ids = {p["id"] for p in obs.get("pickups") or []}
        for k in list(self.pickup_mem):
            kind, ppos, pt = self.pickup_mem[k]
            if k not in seen_ids and (math.hypot(ppos[0] - px, ppos[1] - py) < 30 or t - pt > 90):
                del self.pickup_mem[k]

        # ---- zone
        zone = obs.get("zone") or {}
        zc = zone.get("center") or [0.0, 0.0]
        zr = float(zone.get("radius", 1e4))
        nc = zone.get("next_center") or zc
        nr = float(zone.get("next_radius", zr))
        ss = float(zone.get("shrink_start", 1e9))
        se = float(zone.get("shrink_end", 1e9))
        if t <= ss:
            ec, er = zc, zr
        elif t >= se:
            ec, er = nc, nr
        else:
            f = (t - ss) / max(1e-6, se - ss)
            ec = [zc[0] + (nc[0] - zc[0]) * f, zc[1] + (nc[1] - zc[1]) * f]
            er = zr + (nr - zr) * f
        d_next = math.hypot(px - nc[0], py - nc[1])
        d_eff = math.hypot(px - ec[0], py - ec[1])
        in_zone = me.get("in_zone", True)
        margin = min(25.0, 0.25 * nr + 4.0)
        need = d_next - (nr - margin)
        travel = max(0.0, need) / (walk * 0.75)
        zone_urgent = (not in_zone) or (need > 0 and t + travel + 12.0 > ss)
        # where we must be: inside the next circle
        if d_next > 1e-6:
            keep = min(d_next, max(0.0, nr - margin) * 0.7)
            safe_pt = (nc[0] + (px - nc[0]) / d_next * keep, nc[1] + (py - nc[1]) / d_next * keep)
        else:
            safe_pt = (nc[0], nc[1])

        # ---- threats & target
        mycenter = [px, py, pz + 4.5 * scale]
        tgt = None
        best = 1e18
        threats = []
        for e in enemies:
            ex, ey, ez = e["pos"]
            d = math.hypot(ex - px, ey - py)
            if d > 320:
                continue
            ehp = float(e.get("hp") if e.get("hp") is not None else 1400)
            ehp_frac = ehp / max(1.0, float(e.get("max_hp") or 1400))
            e_center = [ex, ey, ez + 4.5 * (1 + 0.05 * float(e.get("cubes", 0) or 0))]
            vis = d < 260 and self.los(muzzle, e_center)
            recent = e["id"] in self.attackers
            score = d + ehp / 12.0 - (60.0 if recent else 0.0) - (80.0 if vis else 0.0)
            ew = e.get("weapons") or []
            if TUNE["smart_targets"]:
                if e.get("charging") and "lightning" in kinds and d < 45:
                    score -= 90.0  # lightning shorts a charging laser
                if "mortar" in ew and "laser" in kinds:
                    score -= 25.0  # cook-off: +60% laser damage
                if ehp_frac < 0.3:
                    score -= 40.0  # finish it: kill = cubes
            threats.append((e, d, vis, ehp_frac))
            if score < best:
                best, tgt = score, (e, d, vis, e_center)
        n_close = sum(1 for e, d, vis, _ in threats if d < 120)

        # ---- mode
        retreat = hp_frac < TUNE["retreat_hp"] and threats and (tgt is None or float(tgt[0].get("hp") or 1400) > hp * 0.9)
        if zone_urgent:
            mode = "zone"
        elif retreat:
            mode = "retreat"
        elif tgt is not None and (tgt[2] or tgt[1] < 90):
            mode = "fight"
        else:
            mode = "loot"
        self.mode = mode

        # ---- movement goal
        move = [0.0, 0.0]
        goal = None
        loot_target = None  # (kind, obj) to shoot or stand on
        if mode == "zone":
            goal = safe_pt if not in_zone or need > 0 else None
        elif mode == "retreat":
            cx = sum(e["pos"][0] for e, d, v, _ in threats) / len(threats)
            cy = sum(e["pos"][1] for e, d, v, _ in threats) / len(threats)
            ax, ay, _ = norm2(px - cx, py - cy)
            gx, gy = px + ax * 60, py + ay * 60
            if TUNE["cover"]:
                cov = self.find_cover(threats, px, py, pz, ax, ay, nc, nr, margin, t)
                if cov is not None:
                    gx, gy = cov
            # keep inside the zone we'll need
            dx, dy = gx - nc[0], gy - nc[1]
            dd = math.hypot(dx, dy)
            lim = max(10.0, nr - margin)
            if dd > lim:
                gx, gy = nc[0] + dx / dd * lim, nc[1] + dy / dd * lim
            goal = (gx, gy)
        elif mode == "fight":
            pass
        else:
            goal, loot_target = self.choose_loot(obs, me, kinds, px, py, pz, nc, nr, margin, safe_pt, need, ss, t, walk)
            if TUNE["regen_hold"] and hp_frac < TUNE["regen_hold"] and not threats and in_zone and need <= 0:
                # regen needs regen_delay s without dealing or taking damage: don't shoot loot, stand still (silent)
                self.mode = mode = "regen"
                loot_target = None
                if goal is not None and not any(abs(goal[0] - c["pos"][0]) < 1 and abs(goal[1] - c["pos"][1]) < 1
                                                for c in obs.get("cubes") or []):
                    goal = None

        # path following
        dirx = diry = 0.0
        if goal is not None:
            dirx, diry = self.steer_to(goal, px, py, t0)
        if mode == "fight" and tgt is not None:
            dirx, diry = self.fight_move(tgt, kinds, weapons, px, py, hp_frac, ec, er, t, t0)
        # weapon rush: an empty second slot is the biggest power gap; grab a good weapon even mid-fight
        rush_r = TUNE["weapon_rush_r"]
        if rush_r > 0 and mode in ("fight", "loot") and len(kinds) > 1 and kinds[1] is None:
            best_p, bd = None, rush_r
            for pid, (kind, ppos, pt) in self.pickup_mem.items():
                if kind in ("lightning", "laser") and kind not in kinds:
                    d = math.hypot(ppos[0] - px, ppos[1] - py)
                    if d < bd and abs(ppos[2] - pz) < 4.0:
                        best_p, bd = ppos, d
            if best_p is not None:
                wx, wy = self.steer_to((best_p[0], best_p[1]), px, py, t0)
                if mode == "fight":
                    dirx, diry = dirx * 0.35 + wx * 1.3, diry * 0.35 + wy * 1.3
                else:
                    dirx, diry = wx, wy
        # grab nearby power cubes (kills drop victim cubes + 2): +HP, +damage, upgrade offers
        grab_r = TUNE["cube_grab_r"]
        if grab_r > 0 and mode != "zone":
            near_c, nd = None, grab_r
            for c in obs.get("cubes") or []:
                d = math.hypot(c["pos"][0] - px, c["pos"][1] - py)
                if d < nd and abs(c["pos"][2] - pz) < 4.0:
                    near_c, nd = c, d
            if near_c is not None:
                cx, cy, _ = norm2(near_c["pos"][0] - px, near_c["pos"][1] - py)
                if mode == "fight":
                    dirx, diry = dirx * 0.6 + cx * 1.2, diry * 0.6 + cy * 1.2
                else:
                    dirx, diry = cx, cy
        # stay inside the effective circle while fighting / looting
        if mode in ("fight", "loot", "retreat") and d_eff > er - 12:
            ix, iy, _ = norm2(ec[0] - px, ec[1] - py)
            dirx, diry = dirx * 0.4 + ix, diry * 0.4 + iy

        # ---- dodging
        dodge_x, dodge_y, laser_threat = self.dodge(obs, me, mycenter, px, py, pz, vx, vy, scale)
        if dodge_x or dodge_y:
            dirx, diry = dirx * 0.5 + dodge_x * 1.6, diry * 0.5 + dodge_y * 1.6

        mx, my, ml = norm2(dirx, diry)
        hopping = self.hop_req is not None and t - self.hop_req[1] < 0.35
        if ml > 0 and not hopping:
            mx, my = self.avoid_walls(mx, my, px, py)

        # ---- stuck handling
        self.pos_hist.append((t, px, py))
        self.pos_hist = [p for p in self.pos_hist if t - p[0] <= 1.6]
        jump = False
        if t < self.stuck_until:
            mx, my = self.stuck_dir
            jump = bool(me.get("grounded"))
        elif ml > 0 and len(self.pos_hist) >= 10 and t - self.pos_hist[0][0] >= 1.4:
            moved = math.hypot(px - self.pos_hist[0][1], py - self.pos_hist[0][2])
            if moved < 2.0 and not self.charging(weapons):
                ang = math.atan2(my, mx) + self.rng.choice((-1, 1)) * self.rng.uniform(1.2, 2.2)
                self.stuck_dir = (math.cos(ang), math.sin(ang))
                self.stuck_until = t + 0.9
                self.plan_t = -99.0  # replan
                jump = bool(me.get("grounded"))
                self.pos_hist = []
        move = [mx, my]

        # ---- boost
        boost = False
        if mode in ("zone",) and energy > 15:
            boost = not in_zone or need > nr * 0.15 or (t > ss and need > 0)
        if mode == "retreat" and energy > 30:
            boost = True
        if laser_threat and energy > 10:
            boost = True
        if mode == "loot" and not threats and energy > 70 and goal is not None:
            boost = True
        if jump and energy > 20:
            boost = True  # jet over the obstacle
        # planned hop over a wall / cliff: jump, then jet while our feet are below the top
        hr = self.hop_req
        if hr is not None and t - hr[1] < 0.35:
            if pz < hr[0] + 1.5 and energy > 3:
                boost = True
                if me.get("grounded"):
                    jump = True
        self.hop_req = None if (hr is None or t - hr[1] >= 0.35) else hr

        # ---- aim and fire
        aim, fire = self.aim_and_fire(obs, me, weapons, kinds, tgt, loot_target, muzzle, eye, px, py, pz,
                                      mx, my, energy, mode, npcs, t)

        act["move"] = [round(move[0], 4), round(move[1], 4)]
        act["boost"] = bool(boost)
        act["jump"] = bool(jump)
        act["aim"] = [round(aim[0], 5), round(aim[1], 5)]
        act["fire"] = fire

        # ---- reload
        w0 = weapons[0] if weapons else None
        for w in weapons:
            if w and w.get("kind") == "gun":
                if w.get("reload", 0) == 0 and w.get("ammo", 99) <= 3 and not (tgt and tgt[2] and tgt[1] < 150):
                    act["reload"] = True

        # ---- pickups / swaps
        take = self.pick_up(obs, me, weapons, kinds, px, py, pz)
        if take is not None:
            act["take"] = take[0]
            if take[1] is not None:
                act["drop"] = take[1]

        # ---- upgrade offers
        offer = obs.get("offer")
        if offer and offer.get("choices"):
            act["pick"] = self.pick_upgrade(offer["choices"], kinds, me)

        mf.draw.label(f"{mode} hp{hp:.0f} e{energy:.0f}")
        return act

    # ------------------------------------------------------------------ movement pieces
    def steer_to(self, goal, px, py, t0):
        gx, gy = goal
        if self.map is None or self.planner is None:
            x, y, _ = norm2(gx - px, gy - py)
            return x, y
        pl = self.planner
        # direct if clear and short
        dist = math.hypot(gx - px, gy - py)
        if dist < 6.0:
            x, y, _ = norm2(gx - px, gy - py)
            return x * dist / 6.0, y * dist / 6.0
        if dist < 60 and self.map.walk_clear(px, py, gx, gy):
            x, y, _ = norm2(gx - px, gy - py)
            return x, y
        replan = (pl.path_goal is None or math.hypot(pl.path_goal[0] - gx, pl.path_goal[1] - gy) > 12.0
                  or self.t - self.plan_t > 5.0 or not pl.path)
        if replan and not pl.active:
            pl.start(px, py, gx, gy)
            self.plan_t = self.t
        if pl.active:
            budget = 0.008 - (time.perf_counter() - t0)
            if budget > 0.002:
                pl.step(budget)
        wp = pl.lookahead(px, py) if pl.path else None
        if wp is None:
            wp = goal
        x, y, d = norm2(wp[0] - px, wp[1] - py)
        if len(wp) > 2 and wp[2] is not None and d < 26.0:
            self.hop_req = (float(wp[2]), self.t)  # jump + jet until our feet clear this height
        return x, y

    def find_cover(self, threats, px, py, pz, ax, ay, nc, nr, margin, t):
        """Nearest free spot (within ~45 m) where buildings block every nearby threat's line of fire to us."""
        m = self.map
        if m is None:
            return None
        c = self.cover_cache
        if c is not None and t - c[0] < 1.0:
            return c[1]
        shooters = [(e["pos"][0], e["pos"][1], e["pos"][2] + 7.0) for e, d, vis, _ in threats if d < 200][:4]
        if not shooters:
            return None
        lim = max(10.0, nr - margin)
        best, bs = None, 1e18
        base = math.atan2(ay, ax)
        for r in (10.0, 18.0, 28.0, 40.0):
            for k in range(12):
                a = base + (k - 5.5) * (TAU / 12)
                x, y = px + math.cos(a) * r, py + math.sin(a) * r
                if m.is_blocked(x, y) or math.hypot(x - nc[0], y - nc[1]) > lim:
                    continue
                z = m.ground(x, y) + 4.5
                if any(m.los(sh, (x, y, z)) for sh in shooters):
                    continue
                # prefer close spots that are away from threats
                score = r - 6.0 * math.cos(a - base)
                if score < bs:
                    bs, best = score, (x, y)
            if best is not None:
                break
        self.cover_cache = (t, best)
        return best

    def avoid_walls(self, mx, my, px, py):
        m = self.map
        if m is None:
            return mx, my
        base = math.atan2(my, mx)

        def free(a):
            ca, sa = math.cos(a), math.sin(a)
            for d in (3.0, 6.0):
                k = m.ncell(px + ca * d, py + sa * d)
                if m.blocked[k] and k != m.ncell(px, py):
                    return False
            return True

        if free(base):
            return mx, my
        for off in (0.35, 0.7, 1.05, 1.4, 1.75, 2.1, 2.6):
            for s in (1, -1):
                a = base + s * off
                if free(a):
                    return math.cos(a), math.sin(a)
        return mx, my

    def fight_move(self, tgt, kinds, weapons, px, py, hp_frac, ec, er, t, t0):
        e, d, vis, ec3 = tgt
        ex, ey = e["pos"][0], e["pos"][1]
        has_l = "lightning" in kinds
        has_laser = "laser" in kinds
        lr = float(self.wstats(None, "lightning").get("range", 45))
        if has_l and hp_frac > 0.45:
            want = lr * TUNE["lightning_want"]
        elif has_laser:
            want = TUNE["laser_want"]
        else:
            want = TUNE["gun_want"] if hp_frac > 0.5 else TUNE["gun_want"] + 30.0
        enemy_w = e.get("weapons") or []
        if "lightning" in enemy_w and not has_l:
            want = max(want, lr + 20.0)  # stay out of their arc
        if not vis:
            # no line of fire: path toward it (around buildings)
            return self.steer_to((ex, ey), px, py, t0)
        ux, uy, _ = norm2(ex - px, ey - py)
        radial = max(-1.0, min(1.0, (d - want) / 25.0))
        if t > self.strafe_switch_t:
            self.strafe_sign = self.rng.choice((-1.0, 1.0))
            self.strafe_switch_t = t + self.rng.uniform(0.8, 2.2)
        tx, ty = -uy * self.strafe_sign, ux * self.strafe_sign
        st = TUNE["strafe"]
        return ux * radial + tx * st, uy * radial + ty * st

    def dodge(self, obs, me, mycenter, px, py, pz, vx, vy, scale):
        rad = 2.5 * scale
        dx = dy = 0.0
        laser_threat = False
        g = float(self.weapons_cfg.get("mortar", {}).get("gravity", 22.0))
        splash = float(self.weapons_cfg.get("mortar", {}).get("splash_radius", 7.0))
        for p in obs.get("projectiles") or []:
            kind = p.get("kind")
            ppos, pvel = p.get("pos"), p.get("vel")
            if not ppos or not pvel:
                continue
            if kind == "mortar":
                # predict landing
                x, y, z = ppos
                ux, uy, uz = pvel
                land = None
                for s in range(1, 70):
                    tt = s * 0.1
                    zz = z + uz * tt - 0.5 * g * tt * tt
                    xx, yy = x + ux * tt, y + uy * tt
                    if zz <= self.ground(xx, yy, pz) + 0.5:
                        land = (xx, yy, tt)
                        break
                if land is None:
                    continue
                lx, ly, tt = land
                fx, fy = px + vx * tt, py + vy * tt
                dd = math.hypot(fx - lx, fy - ly)
                if dd < splash + rad + 3.0 and tt < 3.5:
                    ax, ay, _ = norm2(px - lx, py - ly)
                    if ax == 0 and ay == 0:
                        ax, ay = 1.0, 0.0
                    w = 1.5 * (1.0 - dd / (splash + rad + 3.0))
                    dx += ax * w
                    dy += ay * w
                continue
            rx, ry, rz = ppos[0] - mycenter[0], ppos[1] - mycenter[1], ppos[2] - mycenter[2]
            wx, wy, wz = pvel[0] - vx, pvel[1] - vy, pvel[2]
            vv = wx * wx + wy * wy + wz * wz
            if vv < 1.0:
                continue
            ts = -(rx * wx + ry * wy + rz * wz) / vv
            if ts <= 0 or ts > 1.2:
                continue
            cx, cy, cz = rx + wx * ts, ry + wy * ts, rz + wz * ts
            miss = math.sqrt(cx * cx + cy * cy + cz * cz)
            if miss < rad + 2.0:
                ox, oy, ol = norm2(cx, cy)
                if ol < 0.3:
                    ox, oy, _ = norm2(-wy, wx)
                w = (1.0 - ts / 1.2) * (1.0 - miss / (rad + 2.0) * 0.5)
                dx += ox * w
                dy += oy * w
        # charging lasers aimed at us
        for e in obs.get("mechs") or []:
            if e.get("kind") != "mech" or not e.get("charging"):
                continue
            ex, ey = e["pos"][0], e["pos"][1]
            d = math.hypot(px - ex, py - ey)
            if d > 300:
                continue
            ay = (e.get("aim") or [0, 0])[0]
            bearing = math.atan2(py - ey, px - ex)
            if abs(wrap(bearing - ay)) < 0.12 + 8.0 / max(d, 10):
                laser_threat = True
                ox, oy = -math.sin(bearing), math.cos(bearing)
                # dodge to the side we're already moving to
                if ox * vx + oy * vy < 0:
                    ox, oy = -ox, -oy
                dx += ox * 1.2
                dy += oy * 1.2
        return dx, dy, laser_threat

    # ------------------------------------------------------------------ loot / objectives
    def choose_loot(self, obs, me, kinds, px, py, pz, nc, nr, margin, safe_pt, need, ss, t, walk):
        best, best_goal, best_tgt = 0.0, None, None
        hp_frac = float(me.get("hp", 1)) / max(1.0, float(me.get("max_hp", 1400)))
        lim = max(10.0, nr - margin)

        def inside(x, y):
            return math.hypot(x - nc[0], y - nc[1]) < lim or t + 25 < ss

        slot_vals = [WEAPON_VALUE.get(k, 0.0) if k else 0.0 for k in kinds]
        # weapon pickups
        for pid, (kind, ppos, pt) in self.pickup_mem.items():
            if kind in kinds:
                continue
            v = WEAPON_VALUE.get(kind, 0.0)
            gain = v - min(slot_vals[1] if len(slot_vals) > 1 else 0.0, 99)
            if kinds[1] is not None and gain <= 0.3:
                continue
            d = math.hypot(ppos[0] - px, ppos[1] - py)
            if d > 220 or not inside(ppos[0], ppos[1]):
                continue
            score = (6.0 + 4.0 * gain) / (d + 25.0)
            if score > best:
                best, best_goal, best_tgt = score, (ppos[0], ppos[1]), None
        # cubes
        for c in obs.get("cubes") or []:
            d = math.hypot(c["pos"][0] - px, c["pos"][1] - py)
            if d < 150 and inside(c["pos"][0], c["pos"][1]):
                score = 5.0 / (d + 25.0)
                if score > best:
                    best, best_goal, best_tgt = score, (c["pos"][0], c["pos"][1]), None
        # supply drops: stand alone in the ring
        for dr in obs.get("drops") or []:
            dpos = dr.get("pos")
            if not dpos:
                continue
            d = math.hypot(dpos[0] - px, dpos[1] - py)
            eta = d / (walk * 1.2)
            if d > 260 or eta > float(dr.get("lands_in", 0)) + 25:
                continue
            crowd = sum(1 for e in obs.get("mechs") or [] if e.get("kind") == "mech"
                        and math.hypot(e["pos"][0] - dpos[0], e["pos"][1] - dpos[1]) < 70)
            if crowd:
                continue
            score = (9.0 + 6.0 * (1 - hp_frac)) / (d + 25.0)
            if score > best:
                best, best_goal, best_tgt = score, (dpos[0], dpos[1]), None
        # crates (shoot them) and NPC drones (only when healthy)
        muzzle = [px, py, pz + 7.0]
        bl = self.loot_black
        for c in obs.get("crates") or []:
            cp = c["pos"]
            if bl.get(c["id"], -1) > t:
                continue
            d = math.hypot(cp[0] - px, cp[1] - py)
            if d < 140 and inside(cp[0], cp[1]):
                score = 3.5 / (d + 30.0)
                if score > best:
                    stand = self.stand_off(cp, px, py, d, 30.0, [cp[0], cp[1], cp[2] + 1.2], muzzle)
                    best, best_goal, best_tgt = score, stand, ("crate", c)
        if hp_frac > TUNE["npc_hp_min"]:
            for n in obs.get("mechs") or []:
                if n.get("kind") != "npc" or bl.get(n["id"], -1) > t:
                    continue
                npos = n["pos"]
                d = math.hypot(npos[0] - px, npos[1] - py)
                if d < 150 and inside(npos[0], npos[1]):
                    score = 4.5 / (d + 30.0) * (0.5 + 0.5 * (350.0 / max(50.0, float(n.get("hp") or 350))))
                    if score > best:
                        stand = self.stand_off(npos, px, py, d, 55.0, [npos[0], npos[1], npos[2] + 1.75], muzzle)
                        best, best_goal, best_tgt = score, stand, ("npc", n)
        # give up on a shoot-target that isn't getting damaged (no line of fire, unreachable, ...)
        if best_tgt is not None:
            oid = best_tgt[1]["id"]
            hpv = float(best_tgt[1].get("hp") or 0)
            lt = self.loot_track
            if lt is None or lt[0] != oid or hpv < lt[2] - 1:
                self.loot_track = (oid, t, hpv)
            elif t - lt[1] > 10.0:
                bl[oid] = t + 60.0
                self.loot_track = None
        if best_goal is None:
            # roam: hold a spot inside the next circle; drift toward its centre late in the match
            return (safe_pt if need > -10 or nr < 120 else None), None
        return best_goal, best_tgt

    def stand_off(self, tp, px, py, d, want, tc=None, muzzle=None):
        if d <= want:
            if tc is None or self.los(muzzle, tc):
                return (px, py)
            return (tp[0], tp[1])  # no line of fire from here: close in
        ux, uy, _ = norm2(px - tp[0], py - tp[1])
        return (tp[0] + ux * want * 0.8, tp[1] + uy * want * 0.8)

    # ------------------------------------------------------------------ aim & fire
    def charging(self, weapons):
        return any(w and w.get("kind") == "laser" and float(w.get("charge", 0) or 0) > 0 for w in weapons)

    def aim_and_fire(self, obs, me, weapons, kinds, tgt, loot_target, muzzle, eye, px, py, pz, mx, my,
                     energy, mode, npcs, t):
        aim_now = me.get("aim") or [0.0, 0.0]
        fire = [False, False]
        stats = me.get("stats") or {}
        min_p, max_p = float(stats.get("min_pitch", -0.7)), float(stats.get("max_pitch", 1.3))
        aim = None
        target_pt = None  # 3D centre to shoot
        target_vel = (0.0, 0.0, 0.0)
        target_r = 2.5
        tvis = False
        if tgt is not None:
            e, d, vis, c3 = tgt
            target_pt = c3
            target_vel = tuple((e.get("vel") or [0, 0, 0])[:3])
            target_r = 2.5 * (1 + 0.05 * float(e.get("cubes", 0) or 0))
            tvis = vis
        elif loot_target is not None:
            kind, obj = loot_target
            p = obj["pos"]
            if kind == "crate":
                target_pt = [p[0], p[1], p[2] + 1.2]
                target_r = 1.3
            else:
                target_pt = [p[0], p[1], p[2] + 1.75]
                target_vel = tuple((obj.get("vel") or [0, 0, 0])[:3])
                target_r = 1.6
            d = math.hypot(p[0] - px, p[1] - py)
            tvis = d < 200 and self.los(muzzle, target_pt)
        # opportunistic NPC in the way when nothing else to shoot
        if target_pt is None:
            for n in npcs:
                d = math.hypot(n["pos"][0] - px, n["pos"][1] - py)
                if d < 60:
                    c = [n["pos"][0], n["pos"][1], n["pos"][2] + 1.75]
                    if self.los(muzzle, c):
                        target_pt, target_vel, target_r, tvis = c, tuple((n.get("vel") or [0, 0, 0])[:3]), 1.6, True
                        break

        gun_i = kinds.index("gun") if "gun" in kinds else -1
        laser_i = kinds.index("laser") if "laser" in kinds else -1
        mortar_i = kinds.index("mortar") if "mortar" in kinds else -1
        light_i = kinds.index("lightning") if "lightning" in kinds else -1

        if target_pt is not None:
            dist3 = math.dist(muzzle, target_pt)
            gs = self.wstats(weapons[gun_i] if gun_i >= 0 else None, "gun")
            speed = float(gs.get("speed", 252))
            # gun lead (bullets do not inherit our velocity: none documented)
            lead = self.intercept(muzzle, target_pt, target_vel, speed)
            aim_lead = self.aim_at(muzzle, lead)
            aim_direct = self.aim_at(muzzle, target_pt)
            aim = aim_lead
            # laser: hitscan, aim straight at it while releasing
            if laser_i >= 0:
                lw = weapons[laser_i]
                ls = self.wstats(lw, "laser")
                charge = float(lw.get("charge", 0) or 0)
                full = float(ls.get("charge", 0.75))
                lrange = float(ls.get("range", 280))
                if tvis and dist3 < lrange and (tgt is not None or charge > 0):
                    if charge > 0 and charge >= full - 0.12:
                        # predict 0.1 s ahead (the action applies next tick)
                        pred = [target_pt[0] + target_vel[0] * 0.1, target_pt[1] + target_vel[1] * 0.1,
                                target_pt[2] + target_vel[2] * 0.1]
                        aim = self.aim_at(muzzle, pred)
                        err = self.miss(aim_now, self.aim_at(muzzle, target_pt), dist3)
                        fire[laser_i] = not (err < target_r * 0.8)  # release (False) when on target
                        if charge < full - 0.02:
                            fire[laser_i] = True
                    elif float(lw.get("cooldown", 0) or 0) <= 0.1:
                        fire[laser_i] = True  # charge
                elif charge > 0:
                    # lost the target while charging: release toward last aim
                    fire[laser_i] = False
            # mortar: lob when no direct line or the target hides
            if mortar_i >= 0 and tgt is not None:
                mw = weapons[mortar_i]
                ms = self.wstats(mw, "mortar")
                dh = math.hypot(target_pt[0] - px, target_pt[1] - py)
                if 22.0 < dh < 230.0 and float(mw.get("cooldown", 0) or 0) <= 0.1 and (not tvis or gun_i < 0 or dh > 90):
                    sol = self.mortar_solution(muzzle, tgt[0], ms, high=not tvis)
                    if sol is not None and (gun_i < 0 or not tvis or (weapons[gun_i] or {}).get("reload", 0) > 0):
                        aim = sol
                        if self.miss(aim_now, sol, dh) < 4.0:
                            fire[mortar_i] = True
            aim = [aim[0], max(min_p, min(max_p, aim[1]))]
            # gun
            if gun_i >= 0 and tvis:
                gw = weapons[gun_i]
                if gw.get("reload", 0) == 0 and gw.get("ammo", 1) > 0 and dist3 < float(gs.get("range", 230)) * 0.97:
                    err = self.miss(aim_now, aim_lead, dist3)
                    if err < target_r * 0.9 + 0.4:
                        fire[gun_i] = True
            # lightning: instant, nearest target in the cone
            if light_i >= 0:
                lw = weapons[light_i]
                ls = self.wstats(lw, "lightning")
                rng = float(ls.get("range", 45)) * 0.93
                cone = math.radians(float(ls.get("cone_deg", 30))) * 0.5
                cost = float(ls.get("energy", 3))
                if float(lw.get("cooldown", 0) or 0) <= 0.1 and energy >= cost + (8 if mode == "retreat" else 2):
                    for cand in (obs.get("mechs") or []):
                        cp = cand["pos"]
                        dd = math.hypot(cp[0] - px, cp[1] - py)
                        if dd > rng:
                            continue
                        b = math.atan2(cp[1] - py, cp[0] - px)
                        if abs(wrap(b - aim_now[0])) < cone and self.los(muzzle, [cp[0], cp[1], cp[2] + 3.0]):
                            fire[light_i] = True
                            break
                    if not fire[light_i]:
                        for c in obs.get("crates") or []:
                            cp = c["pos"]
                            dd = math.hypot(cp[0] - px, cp[1] - py)
                            b = math.atan2(cp[1] - py, cp[0] - px)
                            if dd < rng and abs(wrap(b - aim_now[0])) < cone and energy > 40 and tgt is None:
                                fire[light_i] = True
                                break
        if aim is None:
            # nobody to shoot: face the latest attacker / loud noise, else scan around the move direction
            yaw = None
            if self.hurt_log and t - self.hurt_log[-1][0] < 3.0 and self.hurt_log[-1][2] is not None:
                yaw = float(self.hurt_log[-1][2])
            if yaw is None:
                for s in obs.get("sounds") or []:
                    if s.get("kind") in ("gun", "laser", "laser_charge", "lightning", "mortar", "step", "boost", "jet") \
                            and s.get("dist") in ("near", "mid") and s.get("bearing") is not None:
                        yaw = float(s["bearing"])
                        break
            if yaw is None:
                for k, em in self.enemy_mem.items():
                    if t - em["t"] < 8.0:
                        yaw = math.atan2(em["pos"][1] - py, em["pos"][0] - px)
                        break
            if yaw is None:
                base = math.atan2(my, mx) if (mx or my) else float(aim_now[0])
                yaw = base + 1.1 * math.sin(t * 1.3)
            aim = [wrap(yaw), 0.0]
            # keep the laser charging off when idle
            for i, w in enumerate(weapons):
                if w and w.get("kind") == "laser" and float(w.get("charge", 0) or 0) > 0:
                    fire[i] = False
        return aim, fire

    @staticmethod
    def aim_at(src, dst):
        dx, dy, dz = dst[0] - src[0], dst[1] - src[1], dst[2] - src[2]
        return [math.atan2(dy, dx), math.atan2(dz, math.hypot(dx, dy))]

    @staticmethod
    def miss(aim_now, aim_want, dist):
        dyaw = wrap(aim_want[0] - aim_now[0]) * math.cos(aim_want[1])
        dp = aim_want[1] - aim_now[1]
        return dist * math.sqrt(dyaw * dyaw + dp * dp)

    @staticmethod
    def intercept(src, pt, vel, speed):
        rx, ry, rz = pt[0] - src[0], pt[1] - src[1], pt[2] - src[2]
        vx, vy, vz = vel
        a = vx * vx + vy * vy + vz * vz - speed * speed
        b = 2 * (rx * vx + ry * vy + rz * vz)
        c = rx * rx + ry * ry + rz * rz
        tt = None
        if abs(a) < 1e-9:
            if abs(b) > 1e-9:
                tt = -c / b
        else:
            disc = b * b - 4 * a * c
            if disc >= 0:
                sq = math.sqrt(disc)
                cands = [x for x in ((-b - sq) / (2 * a), (-b + sq) / (2 * a)) if x > 0]
                if cands:
                    tt = min(cands)
        if tt is None or tt > 3.0:
            tt = math.sqrt(c) / speed
        tt += 0.05  # half a decision of latency
        return [pt[0] + vx * tt, pt[1] + vy * tt, pt[2] + vz * min(tt, 0.5)]

    def mortar_solution(self, muzzle, e, ms, high=False):
        v = float(ms.get("speed", 72))
        g = float(ms.get("gravity", 22))
        ex, ey, ez = e["pos"]
        evx, evy = (e.get("vel") or [0, 0, 0])[:2]
        src = [muzzle[0], muzzle[1], muzzle[2] + 2.5]
        tt = 2.0
        for _ in range(3):
            tx, ty = ex + evx * tt, ey + evy * tt
            R = math.hypot(tx - src[0], ty - src[1])
            dz = ez + 1.0 - src[2]
            disc = v ** 4 - g * (g * R * R + 2 * dz * v * v)
            if disc < 0 or R < 1:
                return None
            sq = math.sqrt(disc)
            th = math.atan((v * v + sq) / (g * R)) if high else math.atan((v * v - sq) / (g * R))
            tt = R / max(1e-3, v * math.cos(th))
        return [math.atan2(ty - src[1], tx - src[0]), th]

    # ------------------------------------------------------------------ items / upgrades
    def pick_up(self, obs, me, weapons, kinds, px, py, pz):
        reach = float(self.loot_cfg.get("pickup_radius", 4.0)) - 0.3
        for p in obs.get("pickups") or []:
            kind = p.get("kind")
            if kind in kinds:
                continue
            pp = p["pos"]
            if math.hypot(pp[0] - px, pp[1] - py) > reach or abs(pp[2] - pz) > 3.8:
                continue
            if None in kinds:
                return p["id"], None
            # both full: swap out the weakest non-charging slot if the new one is better
            vals = [(WEAPON_VALUE.get(k, 0), i) for i, k in enumerate(kinds)]
            vals.sort()
            worst_v, worst_i = vals[0]
            w = weapons[worst_i]
            if w and w.get("kind") == "laser" and float(w.get("charge", 0) or 0) > 0:
                continue
            if WEAPON_VALUE.get(kind, 0) > worst_v + 0.3 and kinds[worst_i] != "gun":
                return p["id"], worst_i
        return None

    def pick_upgrade(self, choices, kinds, me):
        best_i, best_v = 0, -1e9
        hp_frac = float(me.get("hp", 1)) / max(1.0, float(me.get("max_hp", 1400)))
        for i, c in enumerate(choices):
            cid = c.get("id")
            v = UPGRADE_VALUE.get(cid, 1.0)
            req = (self.upgrade_defs.get(cid) or {}).get("requires")
            if req and req not in kinds:
                v -= 5.0
            if cid == "plating" and hp_frac < 0.5:
                v += 1.0
            if v > best_v:
                best_v, best_i = v, i
        return best_i
