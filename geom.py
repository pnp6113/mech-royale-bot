"""Static map model: terrain heights, solid prisms on a 2 m grid, a 4 m walkability grid, line of sight.

Built once from hello.map (world.json + terrain.i16), standard library only. All coordinates are the
protocol frame: x east, y north, z up, meters.
"""
from __future__ import annotations

import array
import json
import math
import os

INF = float("inf")
NO_SOLID = -1.0e9


class MapModel:
    def __init__(self, map_dir: str, files, mech_cfg: dict):
        world_path = terrain_path = None
        for f in files or []:
            if f.endswith("world.json"):
                world_path = os.path.join(map_dir, f)
            elif f.endswith(".i16"):
                terrain_path = os.path.join(map_dir, f)
        world_path = world_path or os.path.join(map_dir, "v2/world.json")
        with open(world_path) as fh:
            world = json.load(fh)
        t = world["terrain"]
        self.t_minx = float(t["minX"])
        self.t_miny = float(t["minY"])
        self.t_step = float(t.get("step", 1.0))
        self.t_nx = int(t["nx"])
        self.t_ny = int(t["ny"])
        scale = float(t.get("scale", 0.01))
        terrain_path = terrain_path or os.path.join(os.path.dirname(world_path), t.get("file", "terrain.i16"))
        h = array.array("h")
        with open(terrain_path, "rb") as fh:
            h.frombytes(fh.read())
        if array.array("h", [1]).tobytes()[0] != 1:  # big-endian host: file is little-endian
            h.byteswap()
        self.terrain = array.array("f", (v * scale for v in h))
        b = world["bounds"]
        self.minx, self.maxx = float(b["minX"]), float(b["maxX"])
        self.miny, self.maxy = float(b["minY"]), float(b["maxY"])

        self.step_up = float(mech_cfg.get("step_up", 1.2))
        self.mech_h = float(mech_cfg.get("height", 9.0))

        # ---- 2 m solid grid
        self.cs = 2.0
        self.ox, self.oy = self.minx, self.miny
        self.nx = int(math.ceil((self.maxx - self.minx) / self.cs))
        self.ny = int(math.ceil((self.maxy - self.miny) / self.cs))
        n = self.nx * self.ny
        self.top = array.array("f", [NO_SOLID]) * n
        self.base = array.array("f", [INF]) * n
        for p in world.get("prisms", []):
            self._raster(p)
        # ground at 2 m cell centres
        self.ground2 = array.array("f", [0.0]) * n
        for j in range(self.ny):
            y = self.oy + (j + 0.5) * self.cs
            row = j * self.nx
            for i in range(self.nx):
                self.ground2[row + i] = self.ground(self.ox + (i + 0.5) * self.cs, y)
        # wall = solid that blocks a walking mech in that cell
        self.wall2 = bytearray(n)
        for k in range(n):
            tp = self.top[k]
            if tp > NO_SOLID:
                g = self.ground2[k]
                if tp > g + self.step_up and self.base[k] < g + self.mech_h:
                    self.wall2[k] = 1

        # ---- 4 m navigation grid (cell blocked if a wall is within ~3.2 m of its centre)
        self.ncs = 4.0
        self.nnx = self.nx // 2
        self.nny = self.ny // 2
        nn = self.nnx * self.nny
        self.blocked = bytearray(nn)
        self.nground = array.array("f", [0.0]) * nn
        offs = [(-2, -1), (-2, 0), (-1, -2), (-1, -1), (-1, 0), (-1, 1), (0, -2), (0, -1), (0, 0), (0, 1), (1, -1), (1, 0)]
        offs = [(a, b) for a in (-2, -1, 0, 1) for b in (-2, -1, 0, 1) if not (a in (-2, 1) and b in (-2, 1))]
        for J in range(self.nny):
            for I in range(self.nnx):
                k = J * self.nnx + I
                ci, cj = 2 * I + 1, 2 * J + 1  # 2 m cell index right above-right of the centre
                bl = 0
                for di, dj in offs:
                    i2, j2 = ci + di, cj + dj
                    if 0 <= i2 < self.nx and 0 <= j2 < self.ny and self.wall2[j2 * self.nx + i2]:
                        bl = 1
                        break
                # the map edge is a wall
                if I == 0 or J == 0 or I == self.nnx - 1 or J == self.nny - 1:
                    bl = 1
                self.blocked[k] = bl
                x, y = self.ncenter(k)
                self.nground[k] = self.ground(x, y)
        # steep terrain between neighbouring nav cells blocks the uphill direction (handled in nav)
        self.max_rise = self.ncs * math.tan(math.radians(float(mech_cfg.get("max_slope_deg", 50)))) * 0.9

    # ------------------------------------------------------------------ rasterising
    def _raster(self, p: dict) -> None:
        rings = [p.get("ring") or []] + list(p.get("holes") or [])
        top, base = float(p["top"]), float(p["base"])
        edges = []
        miny, maxy, minx, maxx = INF, -INF, INF, -INF
        for r in rings:
            m = len(r) // 2
            for a in range(m):
                x1, y1 = r[2 * a], r[2 * a + 1]
                x2, y2 = r[(2 * a + 2) % (2 * m)], r[(2 * a + 3) % (2 * m)]
                edges.append((x1, y1, x2, y2))
                miny, maxy = min(miny, y1), max(maxy, y1)
                minx, maxx = min(minx, x1), max(maxx, x1)
        if not edges:
            return
        cs, ox, oy, nx, ny = self.cs, self.ox, self.oy, self.nx, self.ny
        j0 = max(0, int((miny - oy) / cs) - 1)
        j1 = min(ny - 1, int((maxy - oy) / cs) + 1)
        for j in range(j0, j1 + 1):
            yc = oy + (j + 0.5) * cs
            xs = []
            for x1, y1, x2, y2 in edges:
                if (y1 <= yc < y2) or (y2 <= yc < y1):
                    xs.append(x1 + (yc - y1) * (x2 - x1) / (y2 - y1))
            xs.sort()
            for a in range(0, len(xs) - 1, 2):
                i0 = max(0, int(math.ceil((xs[a] - ox) / cs - 0.5)))
                i1 = min(nx - 1, int(math.floor((xs[a + 1] - ox) / cs - 0.5)))
                for i in range(i0, i1 + 1):
                    self._mark(j * nx + i, top, base)
        # edges: mark every cell an edge passes through (thin walls)
        for x1, y1, x2, y2 in edges:
            L = math.hypot(x2 - x1, y2 - y1)
            steps = max(1, int(L / 0.8))
            for s in range(steps + 1):
                x = x1 + (x2 - x1) * s / steps
                y = y1 + (y2 - y1) * s / steps
                i = int((x - ox) / cs)
                j = int((y - oy) / cs)
                if 0 <= i < nx and 0 <= j < ny:
                    self._mark(j * nx + i, top, base)

    def _mark(self, k: int, top: float, base: float) -> None:
        if top > self.top[k]:
            self.top[k] = top
        if base < self.base[k]:
            self.base[k] = base

    # ------------------------------------------------------------------ queries
    def ground(self, x: float, y: float) -> float:
        fx = (x - self.t_minx) / self.t_step
        fy = (y - self.t_miny) / self.t_step
        nx, ny = self.t_nx, self.t_ny
        if fx < 0:
            fx = 0.0
        elif fx > nx - 1.001:
            fx = nx - 1.001
        if fy < 0:
            fy = 0.0
        elif fy > ny - 1.001:
            fy = ny - 1.001
        i, j = int(fx), int(fy)
        ax, ay = fx - i, fy - j
        t = self.terrain
        k = j * nx + i
        h00, h10, h01, h11 = t[k], t[k + 1], t[k + nx], t[k + nx + 1]
        return (h00 * (1 - ax) + h10 * ax) * (1 - ay) + (h01 * (1 - ax) + h11 * ax) * ay

    def cell2(self, x: float, y: float) -> int:
        i = int((x - self.ox) / self.cs)
        j = int((y - self.oy) / self.cs)
        if 0 <= i < self.nx and 0 <= j < self.ny:
            return j * self.nx + i
        return -1

    def ncell(self, x: float, y: float) -> int:
        I = int((x - self.ox) / self.ncs)
        J = int((y - self.oy) / self.ncs)
        if I < 0:
            I = 0
        elif I >= self.nnx:
            I = self.nnx - 1
        if J < 0:
            J = 0
        elif J >= self.nny:
            J = self.nny - 1
        return J * self.nnx + I

    def ncenter(self, k: int):
        J, I = divmod(k, self.nnx)
        return self.ox + (I + 0.5) * self.ncs, self.oy + (J + 0.5) * self.ncs

    def is_blocked(self, x: float, y: float) -> bool:
        return bool(self.blocked[self.ncell(x, y)])

    def nearest_free(self, k: int, max_r: int = 12) -> int:
        if not self.blocked[k]:
            return k
        J0, I0 = divmod(k, self.nnx)
        best, bd = -1, INF
        for r in range(1, max_r + 1):
            for dJ in range(-r, r + 1):
                for dI in (-r, r) if abs(dJ) != r else range(-r, r + 1):
                    I, J = I0 + dI, J0 + dJ
                    if 0 <= I < self.nnx and 0 <= J < self.nny:
                        kk = J * self.nnx + I
                        if not self.blocked[kk]:
                            d = dI * dI + dJ * dJ
                            if d < bd:
                                bd, best = d, kk
            if best >= 0:
                return best
        return k

    def walk_clear(self, x0: float, y0: float, x1: float, y1: float) -> bool:
        """Straight walk between two points crosses no blocked nav cell (start cell ignored)."""
        d = math.hypot(x1 - x0, y1 - y0)
        n = int(d / 2.0) + 1
        k0 = self.ncell(x0, y0)
        for s in range(1, n + 1):
            x = x0 + (x1 - x0) * s / n
            y = y0 + (y1 - y0) * s / n
            k = self.ncell(x, y)
            if k != k0 and self.blocked[k]:
                return False
        return True

    def los(self, a, b, step: float = 2.0) -> bool:
        """Line of fire/sight between two 3D points through buildings and terrain (trees ignored)."""
        ax, ay, az = a[0], a[1], a[2]
        dx, dy, dz = b[0] - ax, b[1] - ay, b[2] - az
        d = math.sqrt(dx * dx + dy * dy + dz * dz)
        if d < 1e-6:
            return True
        n = int(d / step) + 1
        top, base, gr = self.top, self.base, self.ground2
        ox, oy, cs, nx, ny = self.ox, self.oy, self.cs, self.nx, self.ny
        for s in range(1, n):
            f = s / n
            x, y, z = ax + dx * f, ay + dy * f, az + dz * f
            i = int((x - ox) / cs)
            j = int((y - oy) / cs)
            if not (0 <= i < nx and 0 <= j < ny):
                return False
            k = j * nx + i
            if z < gr[k] - 0.4:
                return False
            tp = top[k]
            if tp > NO_SOLID and base[k] - 0.3 <= z <= tp + 0.3:
                return False
        return True
