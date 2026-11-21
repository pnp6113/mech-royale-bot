"""Model of the practice house bots (OUR GUESS, not the official AI).

Rules text: 15 house bots (4 easy, 4 medium, 4 hard, 3 expert) act as one squad: they share vision, never fight
each other, and hunt you weakest first (one early, more as you thin them out, all of them late).
"""
import math
import random

import nav

SKILL = {  # aim noise (deg), leads target?, engage range, strafe
    "easy": (4.0, False, 70.0, 0.3),
    "medium": (2.5, True, 80.0, 0.6),
    "hard": (1.5, True, 90.0, 0.8),
    "expert": (0.8, True, 100.0, 1.0),
}
ORDER = {"easy": 0, "medium": 1, "hard": 2, "expert": 3}


class Squad:
    def __init__(self, match):
        self.match = match
        self.known = None  # (t, pos, vel)

    def hunters(self):
        m = self.match
        house = [o for o in m.mechs if not getattr(o, "is_us", False) and o.alive]
        dead = 15 - len(house)
        n = 1 + dead
        if m.t > 300 or m.zone_phase >= 4:
            n = len(house)
        house.sort(key=lambda o: (ORDER[o.skill], o.id))
        return {o.id for o in house[:n]}


def make_factory(seed):
    squads = {}

    def factory(match, mech):
        sq = squads.get(id(match))
        if sq is None:
            sq = squads[id(match)] = Squad(match)
        return HouseBot(match, mech, sq, random.Random(seed * 100 + mech.id))

    return factory


class HouseBot:
    def __init__(self, match, mech, squad, rng):
        self.match = match
        self.me = mech
        self.sq = squad
        self.rng = rng
        self.noise, self.lead, self.engage, self.strafe = SKILL[mech.skill]
        self.planner = nav.Planner(match.m)
        self.plan_t = -99
        self.wander = None
        self.strafe_sign = 1
        self.strafe_t = 0

    def act(self, obs):
        m = self.match
        t = obs["t"]
        me = obs["self"]
        px, py, pz = me["pos"]
        us = next((x for x in obs["mechs"] if x["kind"] == "mech" and getattr(m.mechs[x["id"]], "is_us", False)), None)
        if us is not None and t - self.me.seen_t.get(us["id"], -99) < 0.15:
            self.sq.known = (t, us["pos"], us["vel"], us["id"])
        hunting = self.me.id in self.sq.hunters()
        known = self.sq.known if (self.sq.known and t - self.sq.known[0] < 8) else None
        act = {"fire": [False, False]}
        if obs.get("offer"):
            act["pick"] = 0
        z = obs["zone"]
        nc, nr = z["next_center"], z["next_radius"]
        goal = None
        tgt = None
        if known:
            d = math.hypot(known[1][0] - px, known[1][1] - py)
            if hunting or d < 80:
                tgt = known
        if tgt:
            tp = tgt[1]
            d = math.hypot(tp[0] - px, tp[1] - py)
            # line of fire?
            c = [tp[0], tp[1], tp[2] + 4.5]
            muz = [px, py, pz + 7]
            los = d < 230 and m.m.los(muz, c)
            if los and d < self.engage:
                ux, uy = (tp[0] - px) / max(d, 1e-6), (tp[1] - py) / max(d, 1e-6)
                if t > self.strafe_t:
                    self.strafe_sign = self.rng.choice((-1, 1))
                    self.strafe_t = t + self.rng.uniform(1, 3)
                radial = max(-1, min(1, (d - self.engage * 0.6) / 20))
                mvx = ux * radial - uy * self.strafe * self.strafe_sign
                mvy = uy * radial + ux * self.strafe * self.strafe_sign
                act["move"] = [mvx, mvy]
            else:
                goal = (tp[0], tp[1])
            # aim
            aimp = list(c)
            if self.lead:
                tt = d / 252.0
                aimp = [c[0] + tgt[2][0] * tt, c[1] + tgt[2][1] * tt, c[2]]
            yaw = math.atan2(aimp[1] - py, aimp[0] - px) + math.radians(self.rng.gauss(0, self.noise))
            pit = math.atan2(aimp[2] - muz[2], max(1.0, math.hypot(aimp[0] - px, aimp[1] - py))) \
                + math.radians(self.rng.gauss(0, self.noise * 0.5))
            act["aim"] = [yaw, pit]
            err = abs((yaw - me["aim"][0] + math.pi) % (2 * math.pi) - math.pi)
            for i, w in enumerate(me["weapons"]):
                if not w:
                    continue
                k = w["kind"]
                if k == "gun" and los and d < 220 and err < 0.15:
                    act["fire"][i] = True
                elif k == "lightning" and los and d < 42 and err < 0.25:
                    act["fire"][i] = True
                elif k == "laser" and los and d < 260:
                    act["fire"][i] = not (w.get("charge", 0) >= 0.7 and err < 0.05)
                elif k == "mortar" and d > 30 and d < 200 and not los:
                    pass
        else:
            # wander inside the next circle, grab weapons nearby
            if self.wander is None or math.hypot(self.wander[0] - px, self.wander[1] - py) < 8 or \
                    math.hypot(self.wander[0] - nc[0], self.wander[1] - nc[1]) > nr * 0.8:
                a = self.rng.uniform(0, 2 * math.pi)
                r = nr * 0.6 * math.sqrt(self.rng.random())
                self.wander = (nc[0] + math.cos(a) * r, nc[1] + math.sin(a) * r)
            goal = self.wander
            for p in obs["pickups"]:
                if me["weapons"][1] is None and math.hypot(p["pos"][0] - px, p["pos"][1] - py) < 40:
                    goal = (p["pos"][0], p["pos"][1])
                    if math.hypot(p["pos"][0] - px, p["pos"][1] - py) < 3.5:
                        act["take"] = p["id"]
                    break
            act["aim"] = [math.atan2(goal[1] - py, goal[0] - px), 0]
        # always stay in the zone
        if not me["in_zone"]:
            goal = (nc[0], nc[1])
            act["boost"] = True
        if goal is not None:
            act["move"] = self.steer(goal, px, py, t)
        return act

    def steer(self, goal, px, py, t):
        m = self.match.m
        d = math.hypot(goal[0] - px, goal[1] - py)
        if d < 3:
            return [0, 0]
        if d < 50 and m.walk_clear(px, py, goal[0], goal[1]):
            return [(goal[0] - px) / d, (goal[1] - py) / d]
        pl = self.planner
        if (not pl.path or pl.path_goal is None or math.hypot(pl.path_goal[0] - goal[0], pl.path_goal[1] - goal[1]) > 15
                or t - self.plan_t > 6) and not pl.active:
            pl.start(px, py, goal[0], goal[1])
            self.plan_t = t
        if pl.active:
            pl.step(0.004)
        wp = pl.lookahead(px, py) or goal
        dx, dy = wp[0] - px, wp[1] - py
        L = math.hypot(dx, dy) or 1
        return [dx / L, dy / L]
