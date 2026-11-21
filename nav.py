"""Grid A* on the 4 m walkability grid, resumable across ticks so one decision never blows the budget."""
from __future__ import annotations

import array
import heapq
import math
import time

SQ2 = math.sqrt(2.0)


class Planner:
    def __init__(self, m):
        self.m = m
        n = m.nnx * m.nny
        self.g = array.array("f", [0.0]) * n
        self.came = array.array("i", [-1]) * n
        self.stamp = array.array("i", [0]) * n
        self.closed = array.array("i", [0]) * n
        self.gen = 0
        self.active = False
        self.goal_k = -1
        self.path = []  # list of (x, y) waypoints, start -> goal
        self.path_goal = None
        self.extra_cost = None  # optional callable k -> cost added per cell

    def start(self, sx: float, sy: float, gx: float, gy: float, extra_cost=None) -> None:
        m = self.m
        self.gen += 1
        gen = self.gen
        s = m.nearest_free(m.ncell(sx, sy), 6)
        gk = m.nearest_free(m.ncell(gx, gy), 15)
        self.goal_k = gk
        self.start_k = s
        self.req_goal = (gx, gy)
        self.extra_cost = extra_cost
        self.stamp[s] = gen
        self.g[s] = 0.0
        self.came[s] = -1
        self.open = [(self._h(s), 0.0, s)]
        self.active = True
        self.best_k, self.best_h = s, self._h(s)
        self.expanded = 0

    def _h(self, k: int) -> float:
        nnx = self.m.nnx
        J, I = divmod(k, nnx)
        GJ, GI = divmod(self.goal_k, nnx)
        dx, dy = abs(I - GI), abs(J - GJ)
        return (max(dx, dy) + (SQ2 - 1) * min(dx, dy)) * self.m.ncs

    def step(self, budget_s: float) -> bool:
        """Expand until done or out of time. Returns True when a path is ready."""
        if not self.active:
            return False
        m = self.m
        nnx, nny = m.nnx, m.nny
        blocked, gnd, max_rise = m.blocked, m.nground, m.max_rise
        g, came, stamp, closed, gen = self.g, self.came, self.stamp, self.closed, self.gen
        cs = m.ncs
        goal = self.goal_k
        op = self.open
        extra = self.extra_cost
        t_end = time.perf_counter() + budget_s
        n_since = 0
        while op:
            n_since += 1
            if n_since >= 64:
                n_since = 0
                if time.perf_counter() > t_end:
                    return False
            f, gk_, k = heapq.heappop(op)
            if closed[k] == gen:
                continue
            closed[k] = gen
            self.expanded += 1
            if k == goal:
                self._finish(k)
                return True
            h = f - gk_
            if h < self.best_h:
                self.best_h, self.best_k = h, k
            if self.expanded > 20000:
                self._finish(self.best_k)
                return True
            J, I = divmod(k, nnx)
            gz = gnd[k]
            for dI, dJ, c in ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                              (1, 1, SQ2), (1, -1, SQ2), (-1, 1, SQ2), (-1, -1, SQ2)):
                I2, J2 = I + dI, J + dJ
                if I2 < 0 or J2 < 0 or I2 >= nnx or J2 >= nny:
                    continue
                k2 = J2 * nnx + I2
                if blocked[k2] or closed[k2] == gen:
                    continue
                if dI and dJ and (blocked[J * nnx + I2] or blocked[J2 * nnx + I]):
                    continue
                rise = gnd[k2] - gz
                if rise > max_rise * c:
                    continue
                ng = gk_ + c * cs + (rise * 0.5 if rise > 0 else 0.0)
                if extra is not None:
                    ng += extra(k2)
                if stamp[k2] != gen or ng < g[k2]:
                    stamp[k2] = gen
                    g[k2] = ng
                    came[k2] = k
                    heapq.heappush(op, (ng + self._h(k2), ng, k2))
        self._finish(self.best_k)
        return True

    def _finish(self, k: int) -> None:
        m = self.m
        cells = []
        while k != -1 and len(cells) < 5000:
            cells.append(k)
            if k == self.start_k:
                break
            k = self.came[k]
        cells.reverse()
        pts = [m.ncenter(c) for c in cells]
        if cells and cells[-1] == self.goal_k and not m.is_blocked(*self.req_goal):
            pts.append(self.req_goal)
        self.path = pts
        self.path_goal = self.req_goal
        self.active = False

    def lookahead(self, x: float, y: float, max_d: float = 40.0):
        """Farthest path point reachable in a straight clear line (string pulling), trimming passed points."""
        m = self.m
        p = self.path
        if not p:
            return None
        # drop waypoints we've reached
        best_i, best_d = 0, 1e18
        for i in range(min(len(p), 30)):
            d = (p[i][0] - x) ** 2 + (p[i][1] - y) ** 2
            if d < best_d:
                best_i, best_d = i, d
        if best_i:
            del p[:best_i]
        target = p[0]
        for i in range(1, min(len(p), 14)):
            q = p[i]
            if (q[0] - x) ** 2 + (q[1] - y) ** 2 > max_d * max_d:
                break
            if m.walk_clear(x, y, q[0], q[1]):
                target = q
            else:
                break
        if len(p) == 1 and (target[0] - x) ** 2 + (target[1] - y) ** 2 < 4.0:
            return None
        return target
