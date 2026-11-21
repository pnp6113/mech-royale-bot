"""Conservative runtime classifier: practice squad (house bots never fight each other) vs free-for-all.

Practice (track rules): 15 house bots act as one squad and never fight each other, so before the late zone
the `alive` count only drops when WE kill (or, rarely, a bot dies to the zone / an NPC drone).
Free-for-all (finals): mechs kill each other from the start.

Evidence, each necessary to switch (the default is practice behaviour, which is also what qualified us):
  E1  unexplained deaths: (initial alive - alive) - our kills >= MIN_UNEXPLAINED before T_LIMIT
  E2  beams from one mech ending on a *different* mech far from us (mech-vs-mech fire), seen >= MIN_BEAMS times
FFA is declared when E1 holds, or E2 holds together with at least one unexplained death. Once FFA, it stays FFA.
"""
from __future__ import annotations

import math

MIN_UNEXPLAINED = 3
T_LIMIT = 200.0
MIN_BEAMS = 2


class ModeDetector:
    def __init__(self):
        self.mode = "practice"
        self.n0 = None
        self.our_kills = 0
        self.beam_hits = 0
        self.unexplained = 0
        self.decided_t = None
        self.reason = ""

    def update(self, obs) -> str:
        t = float(obs.get("t", 0.0))
        alive = int(obs.get("alive", 16))
        if self.n0 is None or alive > self.n0:
            self.n0 = alive
        me = obs.get("self") or {}
        my_id = me.get("id")
        px, py = (me.get("pos") or [0, 0, 0])[:2]
        for ev in obs.get("events") or []:
            if ev.get("type") == "kill":
                self.our_kills += 1
        if self.mode == "ffa":
            return self.mode
        self.unexplained = max(0, (self.n0 - alive) - self.our_kills)
        # E2: beams between two other mechs
        mechs = {m["id"]: m for m in obs.get("mechs") or [] if m.get("kind") == "mech"}
        for b in obs.get("beams") or []:
            by = b.get("by")
            path = b.get("path") or []
            if by is None or by == my_id or len(path) < 2:
                continue
            end = path[-1]
            if math.hypot(end[0] - px, end[1] - py) < 15.0:
                continue  # aimed at (or near) us: says nothing
            for mid, m in mechs.items():
                if mid in (by, my_id):
                    continue
                mp = m.get("pos") or [0, 0, 0]
                if math.hypot(end[0] - mp[0], end[1] - mp[1]) < 4.0 and 0.0 <= end[2] - mp[2] <= 10.0:
                    self.beam_hits += 1
                    break
        e1 = t < T_LIMIT and self.unexplained >= MIN_UNEXPLAINED
        e2 = self.beam_hits >= MIN_BEAMS and self.unexplained >= 1
        if e1 or e2:
            self.mode = "ffa"
            self.decided_t = t
            self.reason = f"unexplained={self.unexplained} beams={self.beam_hits} t={t:.0f}"
        return self.mode
