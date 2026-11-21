"""Local ground-level cover and firing positions for mechfight/1."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Iterable


Point = tuple[float, float, float]
PathCheck = Callable[[Point, Point], bool]

_HOLD_LIMIT = 32.0
_PEEK_LIMIT = 14.0
_NEAR_WALL = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1),
              (2, 0), (-2, 0), (0, 2), (0, -2))
_PEEK_OFFSETS = sorted((i * i + j * j, i, j) for i in range(-4, 5) for j in range(-4, 5))
_FIRE_RANGES = {"gun": 230.0, "laser": 280.0, "lightning": 45.0}


@dataclass(frozen=True, slots=True)
class CoverPair:
    hold: Point
    peek: Point
    target_id: int
    score: float
    protected_threats: tuple[int, ...]


class CoverSearch:
    def __init__(self, arena, config: dict):
        self.arena = arena
        self.mech = config.get("mech", {})
        self.npc = config.get("npc", {})
        self.weapons = config.get("weapons", {})
        self.size_per_cube = config.get("cubes", {}).get("size_per_cube", 0.05)
        self.base_height = self.mech.get("height", 9.0)
        self.base_radius = self.mech.get("radius", 2.5)
        self.muzzle_height = self.mech.get("muzzle_height", 7.0)
        self.step_up = self.mech.get("step_up", 1.2)

    def _threat_heights(self, threat: dict) -> tuple[float, float]:
        if threat["kind"] == "npc":
            height = self.npc.get("height", 3.5)
            return height, height * 0.65
        scale = 1 + self.size_per_cube * threat.get("cubes", 0)
        return self.base_height * scale, self.muzzle_height * scale

    def hidden(self, point: Point, threat: dict, *, body_height: float | None = None,
               body_radius: float | None = None) -> bool:
        """Require cover across the projected center, top, feet and sides of the mech."""
        x, y, z = point
        tx, ty, tz = threat["pos"]
        _, muzzle_z = self._threat_heights(threat)
        muzzle = tx, ty, tz + muzzle_z
        height = self.base_height if body_height is None else body_height
        radius = self.base_radius if body_radius is None else body_radius
        if self.arena.los((x, y, z + height * 0.50), muzzle):
            return False
        dx, dy = tx - x, ty - y
        length = math.hypot(dx, dy)
        if length < 0.1:
            return False
        sx, sy = -dy * radius / length, dx * radius / length
        return not any(self.arena.los(sample, muzzle) for sample in (
            (x, y, z + height),
            (x, y, z + height * 0.12),
            (x + sx, y + sy, z + height * 0.48),
            (x - sx, y - sy, z + height * 0.48),
        ))

    def can_fire(self, point: Point, target: dict, *, muzzle_height: float | None = None,
                 max_range: float = math.inf, target_point: Point | None = None,
                 projectile_speed: float | None = None) -> bool:
        """Check the observed center ray and, when supplied, the lead point ray."""
        x, y, z = point
        tx, ty, tz = target["pos"]
        height, _ = self._threat_heights(target)
        muzzle = x, y, z + (self.muzzle_height if muzzle_height is None else muzzle_height)
        center = tx, ty, tz + 0.5 * height
        distance = math.dist(muzzle, center)
        if distance > max_range or not self.arena.los(muzzle, center):
            return False
        if projectile_speed is not None and projectile_speed > 0 and target_point is None:
            velocity = target.get("vel", (0.0, 0.0, 0.0))
            if any(velocity):
                # A provisional lead lane for planning. The gun controller gates
                # live shots against its own exact intercept when firing.
                travel = distance / projectile_speed + 0.05
                target_point = tuple(p + v * travel for p, v in zip(center, velocity))
        return target_point is None or (
            math.dist(muzzle, target_point) <= max_range and self.arena.los(muzzle, target_point)
        )

    def path_clear(self, start: Point, end: Point, *, body_height: float | None = None,
                   body_radius: float | None = None) -> bool:
        """Check a direct walking path on the shared arena map."""
        m = self.arena
        x, y, z = start
        ex, ey, ez = end
        if (abs(z - m.ground(x, y)) > self.step_up + 0.3
                or abs(ez - m.ground(ex, ey)) > self.step_up + 0.3
                or m.is_blocked(ex, ey) or not m.walk_clear(x, y, ex, ey)):
            return False
        height = self.base_height if body_height is None else body_height
        radius = self.base_radius if body_radius is None else body_radius
        if height <= self.base_height + 0.05 and radius <= self.base_radius + 0.05:
            return True

        # The nav raster is built for the spawn hull. Check growing hulls against
        # the actual solid raster as well, including the headroom under bridges.
        n = max(1, math.ceil(math.hypot(ex - x, ey - y) / m.cs))
        reach = radius + m.cs * math.sqrt(0.5)
        pad = math.ceil(reach / m.cs)
        for i in range(1, n + 1):
            px, py = x + (ex - x) * i / n, y + (ey - y) * i / n
            foot = m.ground(px, py)
            cell = m.cell2(px, py)
            if cell < 0:
                return False
            row, col = divmod(cell, m.nx)
            for j in range(max(0, row - pad), min(m.ny, row + pad + 1)):
                dy = m.oy + (j + 0.5) * m.cs - py
                for k in range(max(0, col - pad), min(m.nx, col + pad + 1)):
                    idx = j * m.nx + k
                    if m.top[idx] <= foot + self.step_up or m.base[idx] >= foot + height:
                        continue
                    dx = m.ox + (k + 0.5) * m.cs - px
                    if dx * dx + dy * dy <= reach * reach:
                        return False
        return True

    @staticmethod
    def zone_safe(point: Point, obs: dict, *, seconds: float = 0.0, clearance: float = 0.0) -> bool:
        """Keep a small buffer inside the current and projected zone."""
        zone = obs.get("zone")
        if not zone:
            return True
        now = obs["t"]
        cx, cy = zone["center"]
        radius = zone["radius"]
        if math.hypot(point[0] - cx, point[1] - cy) + clearance > radius:
            return False
        start = max(now, zone.get("shrink_start", math.inf))
        end = zone.get("shrink_end", math.inf)
        if start < now + seconds and start < end:
            f = min(1.0, (now + seconds - start) / (end - start))
            nx, ny = zone["next_center"]
            cx, cy = cx + f * (nx - cx), cy + f * (ny - cy)
            radius += f * (zone["next_radius"] - radius)
        margin = min(2.0, 0.16 * radius)
        return radius > 0 and math.hypot(point[0] - cx, point[1] - cy) + margin + clearance <= radius

    def _weapon_range(self, me: dict) -> float:
        direct_ranges = []
        for weapon in me["weapons"]:
            if not weapon or weapon["kind"] not in _FIRE_RANGES:
                continue
            kind = weapon["kind"]
            reach = weapon["stats"].get("range", self.weapons.get(kind, {}).get("range", _FIRE_RANGES[kind]))
            # The static firing lane starts at the muzzle base. The gun and laser
            # start slightly forward, but reserving that distance helps a moving target.
            direct_ranges.append(reach)
        return max(direct_ranges, default=0.0)

    def _hold_points(self, origin: Point):
        m = self.arena
        row, col = divmod(m.ncell(*origin[:2]), m.nnx)
        size = math.ceil(_HOLD_LIMIT / m.ncs)
        x_start, x_end = max(0, col - size), min(m.nnx, col + size + 1)
        y_start, y_end = max(0, row - size), min(m.nny, row + size + 1)
        near_wall = set()
        for j in range(max(0, y_start - 2), min(m.nny, y_end + 2)):
            for i in range(max(0, x_start - 2), min(m.nnx, x_end + 2)):
                if m.blocked[j * m.nnx + i]:
                    near_wall.update((j + dy) * m.nnx + i + dx for dx, dy in _NEAR_WALL
                                     if x_start <= i + dx < x_end and y_start <= j + dy < y_end)
        candidates, open_ground = [], []
        for j in range(y_start, y_end):
            for i in range(x_start, x_end):
                cell = j * m.nnx + i
                if m.blocked[cell]:
                    continue
                x, y = m.ncenter(cell)
                distance = math.hypot(x - origin[0], y - origin[1])
                if 0.5 < distance <= _HOLD_LIMIT:
                    bucket = candidates if cell in near_wall else open_ground
                    bucket.append((distance, cell, x, y))
        yield 0.0, m.ncell(*origin[:2]), origin
        for distance, cell, x, y in sorted(candidates)[:40] + sorted(open_ground)[:14]:
            yield distance, cell, (x, y, m.ground(x, y))

    def find(self, obs: dict, target: dict, *, threats: Iterable[dict] = (),
             path_safe: PathCheck | None = None, max_fire_range: float | None = None,
             exclude_hold: Point | None = None, projectile_speed: float | None = None,
             require_secondary_cover: bool = False) -> CoverPair | None:
        """Find a covered hold and walkable firing position within a local search budget."""
        me = obs["self"]
        if not me["grounded"] or not me["in_zone"]:
            return None
        origin = tuple(me["pos"])
        if abs(origin[2] - self.arena.ground(*origin[:2])) > self.step_up + 0.3:
            return None
        stats = me["stats"]
        height = stats.get("height", self.base_height)
        radius = stats.get("radius", self.base_radius)
        muzzle_height = stats.get("muzzle_height", self.muzzle_height)
        walk_speed = max(1.0, stats.get("walk_speed", self.mech.get("walk_speed", 11.7)))
        reach = self._weapon_range(me) if max_fire_range is None else max_fire_range
        if reach <= 0 or math.dist(origin[:2], target["pos"][:2]) > reach + _HOLD_LIMIT + _PEEK_LIMIT:
            return None

        target_id = target["id"]
        distinct = {enemy["id"]: enemy for enemy in threats if enemy["id"] != target_id}
        others = sorted(distinct.values(), key=lambda e: (math.dist(origin, e["pos"]), e["id"]))[:4]
        m = self.arena
        peek_cache: dict[int, tuple[Point, bool]] = {}
        exposure_cache: dict[int, int] = {}
        best = None
        clear_checks = pairs = 0
        search_radius = _HOLD_LIMIT + _PEEK_LIMIT
        away_from_zone = self.zone_safe(origin, obs, seconds=max(3.2, search_radius / walk_speed + 1.0),
                                        clearance=search_radius)

        def walkable(a: Point, b: Point) -> bool:
            return (self.path_clear(a, b, body_height=height, body_radius=radius)
                    and (path_safe is None or path_safe(a, b)))

        for approach, cell, hold in self._hold_points(origin):
            if exclude_hold is not None and math.dist(hold[:2], exclude_hold[:2]) <= 4.0:
                continue
            if best is not None and 10.0 * (1 + len(others)) - 0.85 * approach - 1.15 * 2.5 <= best.score:
                continue
            arrival = approach / walk_speed
            if ((not away_from_zone and not self.zone_safe(hold, obs, seconds=max(3.2, arrival + 1.0)))
                    or not self.hidden(hold, target, body_height=height, body_radius=radius)
                    or not walkable(origin, hold)):
                continue
            row, col = divmod(cell, m.nnx)
            protected = (target_id, *(e["id"] for e in others if self.hidden(
                hold, e, body_height=height, body_radius=radius,
            )))
            if require_secondary_cover and len(protected) != 1 + len(others):
                continue
            local_checks = 0
            grid_range = (_PEEK_LIMIT / m.ncs + (math.sqrt(0.5) if approach == 0.0 else 0.0)) ** 2
            for offset, dx, dy in _PEEK_OFFSETS:
                if offset > grid_range:
                    break
                nx, ny = col + dx, row + dy
                if not (0 <= nx < m.nnx and 0 <= ny < m.nny):
                    continue
                idx = ny * m.nnx + nx
                if m.blocked[idx]:
                    continue
                x, y = m.ncenter(idx)
                separation = math.hypot(x - hold[0], y - hold[1])
                if not 2.5 <= separation <= _PEEK_LIMIT:
                    continue
                eta = arrival + separation / walk_speed + 1.0
                if not away_from_zone and not self.zone_safe((x, y, hold[2]), obs, seconds=max(2.0, eta)):
                    continue
                if idx not in peek_cache:
                    if clear_checks >= 98 or local_checks >= 20:
                        continue
                    peek = x, y, m.ground(x, y)
                    peek_cache[idx] = (peek, self.can_fire(
                        peek, target, muzzle_height=muzzle_height, max_range=reach,
                        projectile_speed=projectile_speed,
                    ))
                    clear_checks += 1
                    local_checks += 1
                peek, has_lane = peek_cache[idx]
                if not has_lane or not walkable(hold, peek) or not walkable(peek, hold):
                    continue
                if idx not in exposure_cache:
                    exposed = (not self.hidden(peek, e, body_height=height, body_radius=radius) for e in others)
                    exposure_cache[idx] = int(any(exposed)) if require_secondary_cover else sum(exposed)
                secondary_exposure = exposure_cache[idx]
                if require_secondary_cover and secondary_exposure:
                    continue
                score = 10.0 * len(protected) - 0.85 * approach - 1.15 * separation - 2.5 * secondary_exposure
                if best is None or score > best.score:
                    best = CoverPair(hold, peek, target_id, score, protected)
                pairs += 1
                if pairs >= 16:
                    return best
                if not secondary_exposure:
                    break
        return best
