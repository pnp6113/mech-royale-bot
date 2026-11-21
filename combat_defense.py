"""Survival estimates for Mech Royale's mechfight/1 observations."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Callable


Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
LineClear = Callable[[Vec3, Vec3], bool]
GroundHeight = Callable[[float, float], float | None]

_EPS = 1e-8
_MAX_BULLETS = 32
_MAX_SHELLS = 8
_WEAPON_DEFAULTS = {
    "gun": (230.0, 45.0, 0.24),
    "mortar": (200.0, 200.0, 2.6),
    "laser": (280.0, 315.0, 1.65),
    "lightning": (45.0, 60.0, 0.25),
}


def _angle_delta(a: float, b: float) -> float:
    return (a - b + math.pi) % math.tau - math.pi


def _move_vector(move: Vec2) -> Vec2:
    scale = max(1.0, math.hypot(*move))
    return move[0] / scale, move[1] / scale


def _zone_at(zone: dict, now: float, dt: float) -> tuple[float, float, float]:
    x, y = zone["center"]
    radius = zone["radius"]
    begin = max(now, zone.get("shrink_start", math.inf))
    end = zone.get("shrink_end", math.inf)
    if now + dt <= begin or end <= begin:
        return x, y, radius
    fraction = min(1.0, (now + dt - begin) / (end - begin))
    nx, ny = zone.get("next_center", (x, y))
    nr = zone.get("next_radius", radius)
    return x + (nx - x) * fraction, y + (ny - y) * fraction, radius + (nr - radius) * fraction


def _margin(point: Vec3, circle: tuple[float, float, float]) -> float:
    return circle[2] - math.hypot(point[0] - circle[0], point[1] - circle[1])


@dataclass(frozen=True, slots=True)
class CombatRisk:
    score: float
    defensive_yaw: float | None
    zone_margin: float
    zone_urgent: bool
    visible_pressure: float
    recent_damage: float
    _sources: tuple[tuple[float, float], ...]
    _half_front_arc: float
    _rear_bonus: float

    def orientation_cost(self, yaw: float) -> float:
        """Estimated extra HP exposure from the torso's rear multiplier."""
        return self._rear_bonus * sum(
            weight for bearing, weight in self._sources
            if abs(_angle_delta(bearing, yaw)) > self._half_front_arc + _EPS
        )


def assess_risk(obs: dict, config: dict, *, line_clear: LineClear | None = None) -> CombatRisk:
    """Estimate nearby combat pressure and a weighted front-armor orientation."""
    me = obs["self"]
    stats = {**config.get("mech", {}), **me.get("stats", {})}
    weapons = config.get("weapons", {})
    cubes = config.get("cubes", {})
    npc = config.get("npc", {})
    x, y, z = me["pos"]
    center = (x, y, z + stats.get("height", 9.0) * 0.5)
    current_yaw = me["aim"][0]
    half_arc = math.radians(stats.get("front_arc_deg", 180.0) * 0.5)
    rear_bonus = max(0.0, stats.get("rear_mult", 1.5) - 1.0)
    sources = []
    visible_pressure = 0.0
    for enemy in obs.get("mechs", ()):
        ex, ey, ez = enemy["pos"]
        distance = math.dist((x, y, z), (ex, ey, ez))
        is_npc = enemy["kind"] == "npc"
        items = ("npc",) if is_npc else enemy.get("weapons", ())
        bearing = math.atan2(ey - y, ex - x)
        facing = abs(_angle_delta(enemy["aim"][0], bearing + math.pi))
        aim_weight = 0.30 + 0.70 * max(0.0, math.cos(facing))
        damage_scale = 1 + cubes.get("damage_per_cube", 0.04) * min(
            enemy.get("cubes", 0), cubes.get("stat_cap", 10)
        )
        threat = 0.0
        for kind in items:
            w = weapons.get(kind, {})
            if is_npc:
                reach, damage, cooldown = npc.get("aggro_range", 45.0), npc.get("damage", 25.0), npc.get("cooldown", 1.0)
            else:
                reach, damage, cooldown = _WEAPON_DEFAULTS.get(kind, (0.0, 0.0, 1.0))
            if kind == "mortar":
                speed = w.get("speed", 72.0)
                reach = speed * speed / max(1.0, w.get("gravity", 22.0))
            reach = w.get("range", reach)
            if distance > reach:
                continue
            weight = 0.15 + 0.85 * (1.0 - distance / max(1.0, reach))
            threat = max(threat, w.get("damage", damage) / max(0.1, w.get("cooldown", cooldown)) * weight)
        threat *= 0.40 * aim_weight * damage_scale
        if enemy.get("charging"):
            laser = weapons.get("laser", {})
            if distance <= laser.get("range", 280.0):
                threat += laser.get("damage", 315.0) * 0.3 * aim_weight
        if threat <= 0:
            continue
        height = npc.get("height", 3.5) * 0.5 if is_npc else stats.get("muzzle_height", 7.0)
        if line_clear is not None and not line_clear((ex, ey, ez + height), center):
            # A tracked contact behind cover remains a lesser future threat.
            threat *= 0.12
        sources.append((bearing, threat))
        visible_pressure += threat

    recent_damage = 0.0
    for event in obs.get("events", ()):
        if event.get("type") != "damaged":
            continue
        amount = max(0.0, event["amount"])
        recent_damage += amount
        bearing = event.get("bearing")
        if bearing is not None and amount > 0:
            sources.append((bearing, 0.80 * amount))

    sound_pressure = 0.0
    for sound in obs.get("sounds", ()):
        if sound["dist"] == "far" or sound["age"] >= 0.35:
            continue
        weight = {"laser_charge": 18.0, "lightning": 12.0, "gun": 9.0, "mortar": 6.0, "npc_gun": 3.0}.get(
            sound["kind"], 0.0
        ) * (1 - sound["age"] / 0.35)
        if sound["dist"] == "mid":
            weight *= 0.35
        if weight > 0 and all(abs(_angle_delta(sound["bearing"], bearing)) > 0.25 for bearing, _ in sources):
            sources.append((sound["bearing"], weight))
            sound_pressure += weight

    projectile_pressure = 0.0
    velocity = me.get("vel", (0.0, 0.0, 0.0))
    endpoint = tuple(p + 0.45 * v for p, v in zip(me["pos"], velocity))
    for projectile in obs.get("projectiles", ()):
        kind = projectile["kind"]
        if kind not in ("gun", "npc"):
            continue
        origin = tuple(projectile["pos"])
        pv = projectile["vel"]
        end = tuple(p + 0.45 * v for p, v in zip(origin, pv))
        contact = _contact(origin, end, (x, y, z), endpoint, stats.get("radius", 2.5),
                           stats.get("height", 9.0), 0.5)
        if contact is None or (line_clear is not None and not line_clear(origin, _lerp(origin, end, contact))):
            continue
        damage = npc.get("damage", 25.0) if kind == "npc" else weapons.get("gun", {}).get("damage", 45.0)
        weight = 0.55 * damage
        projectile_pressure += weight
        sources.append((math.atan2(-pv[1], -pv[0]), weight))

    zone = obs.get("zone")
    zone_margin = float("inf")
    zone_exposure = 0.0
    zone_urgent = False
    if zone:
        now = obs.get("t", 0.0)
        zone_margin = _margin(tuple(me["pos"]), _zone_at(zone, now, 0.0))
        future_margin = _margin(tuple(me["pos"]), _zone_at(zone, now, 0.75))
        zone_urgent = not me.get("in_zone", zone_margin >= 0) or future_margin <= stats.get("radius", 2.5)
        if zone_urgent:
            zone_exposure = zone.get("dps", 0.0) * (0.75 if zone_margin < 0 else 0.25)
    pressure = visible_pressure + 0.65 * recent_damage + sound_pressure + projectile_pressure + zone_exposure
    score = -math.expm1(-pressure / max(60.0, 0.25 * me["hp"]))
    # Merge nearby bearings before evaluating front-arc coverage in crowded observations.
    directional = {}
    for bearing, weight in sources:
        key = round(_angle_delta(bearing, 0.0) * 8)
        prior_bearing, prior_weight = directional.get(key, (bearing, 0.0))
        total_weight = prior_weight + weight
        heading = prior_bearing + _angle_delta(bearing, prior_bearing) * weight / total_weight
        directional[key] = heading, total_weight
    sources = tuple(directional.values())
    result = CombatRisk(score, None, zone_margin, zone_urgent, visible_pressure, recent_damage,
                        sources, half_arc, rear_bonus)
    if not sources:
        return result
    safe_arc = max(0.0, half_arc - 0.08)
    candidates = [current_yaw]
    for bearing, _ in sources:
        candidates.extend((bearing, bearing - safe_arc, bearing + safe_arc, bearing - half_arc, bearing + half_arc))

    def cost(yaw):
        rear, edge = 0.0, 0.0
        for bearing, weight in sources:
            angle = abs(_angle_delta(bearing, yaw))
            if angle > half_arc + _EPS:
                rear += weight
            elif angle > safe_arc + _EPS:
                edge += weight
        return rear_bonus * rear, rear_bonus * edge, abs(_angle_delta(yaw, current_yaw))

    yaw = min(candidates, key=cost)
    return replace(result, defensive_yaw=_angle_delta(yaw, 0.0))


@dataclass(frozen=True, slots=True)
class DodgeDecision:
    move: Vec2
    boost: bool
    threatened: bool
    changed: bool
    baseline_damage: float
    expected_damage: float


@dataclass(frozen=True, slots=True)
class _Projectile:
    pos: Vec3
    vel: Vec3
    radius: float
    damage: float
    through: float
    gravity: float = 0.0
    splash: float = 0.0
    splash_radius: float = 0.0
    splash_edge: float = 0.0
    impact: Vec3 | None = None
    from_time: float = 0.0

    def at(self, t: float) -> Vec3:
        x, y, z = self.pos
        vx, vy, vz = self.vel
        return x + vx * t, y + vy * t, z + vz * t - 0.5 * self.gravity * t * t


def _horizontal_interval(x: float, y: float, dx: float, dy: float, radius: float) -> Vec2 | None:
    """Times on the infinite line when the moving point is inside the disk."""
    a = dx * dx + dy * dy
    c = x * x + y * y - radius * radius
    if a <= _EPS:
        return (-math.inf, math.inf) if c <= 0 else None
    b = x * dx + y * dy
    discriminant = b * b - a * c
    if discriminant < 0:
        return None
    root = math.sqrt(discriminant)
    return (-b - root) / a, (-b + root) / a


def _contact(a: Vec3, b: Vec3, feet_a: Vec3, feet_b: Vec3, radius: float, height: float,
             padding: float) -> float | None:
    """First fraction of a segment that intersects a translating vertical cylinder."""
    x, y, z = a[0] - feet_a[0], a[1] - feet_a[1], a[2] - feet_a[2]
    bx, by, bz = b[0] - feet_b[0], b[1] - feet_b[1], b[2] - feet_b[2]
    # Most segments begin and end on the same side of the cylinder.
    rr = radius + padding
    if ((x > rr and bx > rr) or (x < -rr and bx < -rr) or (y > rr and by > rr)
            or (y < -rr and by < -rr) or (z > height + padding and bz > height + padding)
            or (z < -padding and bz < -padding)):
        return None
    interval = _horizontal_interval(x, y, bx - x, by - y, rr)
    if interval is None:
        return None
    start, end = max(0.0, interval[0]), min(1.0, interval[1])
    dz = bz - z
    if abs(dz) <= _EPS:
        return start if -padding <= z <= height + padding and start <= end else None
    lo, hi = sorted(((-padding - z) / dz, (height + padding - z) / dz))
    start, end = max(start, lo), min(end, hi)
    return start if start <= end else None


def _lerp(a: Vec3, b: Vec3, fraction: float) -> Vec3:
    return tuple(start + fraction * (end - start) for start, end in zip(a, b))


def _static_impact(projectile: _Projectile, step: float, ground_height: GroundHeight | None,
                   fallback_height: float, line_clear: LineClear | None) -> tuple[float, Vec3 | None]:
    duration = projectile.through
    if not projectile.splash and not projectile.gravity:
        if line_clear is None or line_clear(projectile.pos, projectile.at(duration)):
            return duration, None
        lo, hi = projectile.from_time, duration
        if lo > 0 and not line_clear(projectile.pos, projectile.at(lo)):
            return lo, None
        for _ in range(5):
            mid = (lo + hi) * 0.5
            if line_clear(projectile.pos, projectile.at(mid)):
                lo = mid
            else:
                hi = mid
        return lo, None

    def blocked(a: Vec3, b: Vec3) -> bool:
        ground = ground_height(b[0], b[1]) if ground_height is not None else None
        return b[2] <= (fallback_height if ground is None else ground) or (
            line_clear is not None and not line_clear(a, b)
        )

    previous, point = 0.0, projectile.pos
    for tick in range(1, math.ceil(duration / step) + 1):
        time = min(duration, tick * step)
        end = projectile.at(time)
        if blocked(point, end):
            lo, hi = previous, time
            for _ in range(7):
                mid = (lo + hi) * 0.5
                if blocked(point, projectile.at(mid)):
                    hi = mid
                else:
                    lo = mid
            return hi, projectile.at(hi)
        previous, point = time, end
    return duration, None


def _projectiles(obs: dict, config: dict, stats: dict, horizon: float, step: float,
                 ground_height: GroundHeight | None, line_clear: LineClear | None) -> list[_Projectile]:
    me = obs["self"]
    x, y, z = me["pos"]
    reach_speed = max(stats.get("boost_speed", 19.5), stats.get("walk_speed", 11.7), math.hypot(*me["vel"][:2]))
    hit_radius = stats.get("radius", 2.5)
    cfg = config.get("weapons", {})
    gun, mortar = cfg.get("gun", {}), cfg.get("mortar", {})
    sizes = config.get("projectile", {})
    bullets, shells = [], []
    for entity in obs.get("projectiles", ()):
        kind = entity["kind"]
        if kind not in ("gun", "npc", "mortar"):
            continue
        pos, vel = tuple(entity["pos"]), tuple(entity["vel"])
        if kind == "mortar":
            p = _Projectile(pos, vel, 0.0, mortar.get("damage", 200.0), min(horizon, mortar.get("ttl", 7.0)),
                            mortar.get("gravity", 22.0), mortar.get("splash", 160.0),
                            mortar.get("splash_radius", 7.0), mortar.get("splash_edge", 0.3))
            group = shells
        else:
            speed = math.hypot(*vel)
            duration = min(horizon, gun.get("range", 230.0) / max(1.0, speed)) if kind == "gun" else horizon
            damage = gun.get("damage", 45.0) if kind == "gun" else config.get("npc", {}).get("damage", 25.0)
            p = _Projectile(pos, vel, sizes.get(f"{kind}_radius", 0.3), damage, duration)
            group = bullets
        rx, ry = pos[0] - x, pos[1] - y
        vx, vy = vel[:2]
        clearance = hit_radius + p.radius + p.splash_radius
        reach = clearance + reach_speed * p.through
        interval = _horizontal_interval(rx, ry, vx, vy, reach)
        if interval is None or interval[0] > p.through or interval[1] < 0:
            continue
        closest_t = min(p.through, max(0.0, -(rx * vx + ry * vy) / max(_EPS, vx * vx + vy * vy)))
        closest_dist = math.hypot(rx + vx * closest_t, ry + vy * closest_t)
        # Prioritize projectiles that could get close early if the observation is crowded.
        p = replace(p, through=min(p.through, interval[1]), from_time=max(0.0, interval[0]))
        group.append((max(0.0, closest_dist - reach_speed * closest_t), closest_t, entity["id"], p))

    selected = []
    for items, limit in ((bullets, _MAX_BULLETS), (shells, _MAX_SHELLS)):
        items.sort(key=lambda item: item[:3])
        for _, _, _, projectile in items[:limit]:
            through, impact = _static_impact(projectile, step, ground_height, z, line_clear)
            if through > projectile.from_time:
                selected.append(replace(projectile, through=through, impact=impact))
    return selected


def _charging_beams(obs: dict, config: dict, height: float, line_clear: LineClear | None):
    weapon = config.get("weapons", {}).get("laser", {})
    reach = weapon.get("range", 280.0)
    me = obs["self"]
    x, y, z = me["pos"]
    center = x, y, z + height * 0.6
    for enemy in obs.get("mechs", ()):
        if not enemy.get("charging"):
            continue
        ex, ey, ez = enemy["pos"]
        yaw, pitch = enemy["aim"]
        if math.hypot(ex - x, ey - y) > reach + 10.0:
            continue
        if abs(_angle_delta(math.atan2(y - ey, x - ex), yaw)) > 0.18 + 8.0 / max(8.0, math.hypot(ex - x, ey - y)):
            continue
        cfg = config.get("mech", {})
        scale = 1 + config.get("cubes", {}).get("size_per_cube", 0.05) * enemy.get("cubes", 0)
        base = ex, ey, ez + cfg.get("muzzle_height", 7.0) * scale
        if line_clear is not None and not line_clear(base, center):
            continue
        unit = math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch)
        offset = cfg.get("radius", 2.5) * scale + 0.5
        origin = tuple(base[i] + unit[i] * offset for i in range(3))
        end = tuple(origin[i] + unit[i] * reach for i in range(3))
        cookoff = any(w and w["kind"] == "mortar" for w in me.get("weapons", ()))
        damage = weapon.get("damage", 315.0) * (1 + (weapon.get("cookoff", 0.6) if cookoff else 0.0))
        yield origin, end, 0.45 * damage


def _simulate(me: dict, stats: dict, move: Vec2, boost: bool, jump: bool, times: list[float],
              laser_charging: bool, ground_height: GroundHeight | None, local_ground: float | None) -> list[Vec3]:
    x, y, z = me["pos"]
    vx, vy, vz = me["vel"]
    grounded = me.get("grounded", True)
    moving = abs(move[0]) + abs(move[1]) > _EPS
    air_speed = stats.get("walk_speed", 11.7)
    charge_mult = stats.get("charge_move_mult", 0.5) if laser_charging else 1.0
    energy = me.get("energy", 0.0)
    if grounded and jump:
        grounded, vz = False, stats.get("jump_speed", 11.0)
    path = [(x, y, z)]
    for prior, now in zip(times, times[1:]):
        dt = now - prior
        drain = stats.get("boost_drain", 17.0) if grounded else stats.get("jet_drain", 15.0)
        powered = boost and (moving or not grounded) and energy >= drain * dt
        if powered:
            energy -= drain * dt
        speed = stats.get("boost_speed", 19.5) if grounded and powered else air_speed
        goal_x, goal_y = move[0] * speed * charge_mult, move[1] * speed * charge_mult
        dx, dy = goal_x - vx, goal_y - vy
        accel = stats.get("ground_accel", 58.5) if grounded else stats.get("air_accel", 12.0)
        fraction = min(1.0, dt * accel / max(_EPS, math.hypot(dx, dy)))
        ax, ay = dx * fraction, dy * fraction
        x, y = x + dt * (vx + 0.5 * ax), y + dt * (vy + 0.5 * ay)
        vx, vy = vx + ax, vy + ay
        if not grounded:
            next_vz = vz - dt * stats.get("gravity", 22.0)
            if powered:
                next_vz = min(stats.get("max_rise_speed", 10.0), next_vz + dt * stats.get("jet_accel", 34.0))
            z, vz = z + dt * (vz + next_vz) * 0.5, next_vz
            floor = ground_height(x, y) if ground_height is not None and vz <= 0 else None
            if floor is not None and z <= floor:
                z, vz, grounded = floor, 0.0, True
        elif local_ground is not None and ground_height is not None:
            ground = ground_height(x, y)
            if ground is not None:
                z = ground + local_ground
        path.append((x, y, z))
    return path


def _falloff(impact: Vec3, feet: Vec3, radius: float, height: float, projectile: _Projectile,
             line_clear: LineClear | None) -> float:
    horizontal = max(0.0, math.hypot(impact[0] - feet[0], impact[1] - feet[1]) - radius)
    vertical = max(0.0, feet[2] - impact[2], impact[2] - feet[2] - height)
    distance = math.hypot(horizontal, vertical)
    if distance > projectile.splash_radius or projectile.splash_radius <= _EPS:
        return 0.0
    center = feet[0], feet[1], feet[2] + height * 0.5
    if line_clear is not None and not line_clear((impact[0], impact[1], impact[2] + 0.05), center):
        return 0.0
    return projectile.splash * (1 - (1 - projectile.splash_edge) * distance / projectile.splash_radius)


def _path_damage(path: list[Vec3], times: list[float], tracks, beams, stats: dict, yaw: float,
                 line_clear: LineClear | None) -> float:
    radius, height = stats.get("radius", 2.5), stats.get("height", 9.0)
    rear = stats.get("rear_mult", 1.5)
    half_arc = math.radians(stats.get("front_arc_deg", 180.0) * 0.5)

    def armor_multiplier(dx, dy):
        is_rear = (abs(dx) + abs(dy) > _EPS and abs(_angle_delta(math.atan2(dy, dx), yaw)) > half_arc)
        return rear if is_rear else 1.0

    damage = 0.0
    for projectile, first_index, segments in tracks:
        for i, (a, b, fraction) in enumerate(segments, first_index):
            start = path[i]
            end = path[i + 1] if fraction == 1.0 else _lerp(start, path[i + 1], fraction)
            contact = _contact(a, b, start, end, radius, height, projectile.radius)
            if contact is not None:
                if projectile.splash:
                    impact, feet = _lerp(a, b, contact), _lerp(start, end, contact)
                    multiplier = armor_multiplier(impact[0] - feet[0], impact[1] - feet[1])
                    damage += multiplier * (projectile.damage + _falloff(impact, feet, radius, height, projectile, None))
                else:
                    damage += projectile.damage * armor_multiplier(-projectile.vel[0], -projectile.vel[1])
                break
        else:
            if projectile.impact is not None and segments:
                end_index = first_index + len(segments)
                feet = _lerp(path[end_index - 1], path[end_index], segments[-1][2])
                impact = projectile.impact
                damage += _falloff(impact, feet, radius, height, projectile, line_clear) * armor_multiplier(
                    impact[0] - feet[0], impact[1] - feet[1]
                )
    for origin, end, expected in beams:
        # The charge can release during the next few observations. Sample the observed lane.
        checks = hits = 0
        for point, time in zip(path, times):
            if 0.10 <= time <= 0.36:
                checks += 1
                hits += _contact(origin, end, point, point, radius, height, 0.5) is not None
        if checks:
            multiplier = armor_multiplier(origin[0] - path[0][0], origin[1] - path[0][1])
            damage += expected * multiplier * hits / checks
    return damage * max(0.0, 1 - stats.get("armor", 0.0))


def choose_dodge(obs: dict, config: dict, move: Vec2, boost: bool = False, *, jump: bool = False,
                 path_safe: LineClear | None = None, ground_height: GroundHeight | None = None,
                 line_clear: LineClear | None = None, assessment: CombatRisk | None = None) -> DodgeDecision:
    """Replace an unsafe move only when an admissible path predicts less damage.

    Damage estimates use the observed torso yaw and live self armor. The map
    callbacks optionally constrain candidate movement and clip projectile paths.
    """
    move = _move_vector(move)
    me = obs["self"]
    yaw = me["aim"][0]
    stats = {**config.get("mech", {}), **me.get("stats", {})}
    match = config.get("match", {})
    step = 1 / max(10.0, min(40.0, match.get("tick_hz", 20)))
    horizon = 1.35
    times = [min(horizon, i * step) for i in range(math.ceil(horizon / step) + 1)]
    beams = tuple(_charging_beams(obs, config, stats.get("height", 9.0), line_clear))
    projectiles = _projectiles(obs, config, stats, horizon, step, ground_height, line_clear)
    if not projectiles and not beams:
        return DodgeDecision(move, boost, False, False, 0.0, 0.0)
    tracks = []
    for projectile in projectiles:
        segments = []
        first_index = int(projectile.from_time / step)
        a = projectile.at(times[first_index])
        for index in range(first_index, len(times) - 1):
            start, end = times[index], times[index + 1]
            if start >= projectile.through:
                break
            stop = min(end, projectile.through)
            b = projectile.at(stop)
            segments.append((a, b, (stop - start) / (end - start)))
            a = b
        tracks.append((projectile, first_index, segments))

    charging = any(w and w["kind"] == "laser" and w.get("charge", 0.0) > 0 for w in me.get("weapons", ()))
    x, y, z = me["pos"]
    local_ground = ground_height(x, y) if ground_height is not None and me.get("grounded", True) else None
    support_offset = z - local_ground if local_ground is not None and abs(z - local_ground) <= stats.get("step_up", 1.2) else None
    baseline = _simulate(me, stats, move, boost, jump, times, charging, ground_height, support_offset)
    baseline_damage = _path_damage(baseline, times, tracks, beams, stats, yaw, line_clear)
    if baseline_damage <= _EPS:
        return DodgeDecision(move, boost, False, False, 0.0, 0.0)

    zone = obs.get("zone")
    circles = [_zone_at(zone, obs.get("t", 0.0), time) for time in times] if zone else ()
    dps = zone.get("dps", 0.0) if zone else 0.0

    def zone_cost(path):
        return dps * step * sum(_margin(point, circle) < 0 for point, circle in zip(path[1:], circles[1:]))

    zone_weight = 1.5 if assessment is not None and assessment.zone_urgent else 1.0
    best = (baseline_damage + zone_weight * zone_cost(baseline), move, boost, baseline_damage)
    angle = math.atan2(move[1], move[0]) if math.hypot(*move) > _EPS else yaw
    candidates = [move, (0.0, 0.0)]
    if projectiles:
        lead = max(projectiles, key=lambda p: p.damage + p.splash)
        if lead.impact is not None:
            away_x, away_y = x - lead.impact[0], y - lead.impact[1]
        else:
            away_x, away_y = -lead.vel[1], lead.vel[0]
        length = math.hypot(away_x, away_y)
        if length > _EPS:
            candidates.extend(((away_x / length, away_y / length), (-away_x / length, -away_y / length)))
    candidates.extend((math.cos(angle + i * math.pi / 4), math.sin(angle + i * math.pi / 4)) for i in range(8))
    decision_s = step * match.get("decide_every", 2)
    required = stats.get("boost_drain", 17.0) if me.get("grounded", True) and not jump else stats.get("jet_drain", 15.0)
    can_boost = me.get("energy", 0.0) >= required * decision_s
    tried = {(round(move[0], 5), round(move[1], 5), boost)}
    for candidate in candidates:
        for power in (False, True) if can_boost else (False,):
            key = round(candidate[0], 5), round(candidate[1], 5), power
            if key in tried:
                continue
            tried.add(key)
            path = _simulate(me, stats, candidate, power, jump, times, charging, ground_height, support_offset)
            damage = _path_damage(path, times, tracks, beams, stats, yaw, line_clear)
            if damage >= baseline_damage - _EPS:
                continue
            plan_cost = 2.0 * math.dist(candidate, move) + (1.0 + required * decision_s * 0.1 if power and not boost else 0.0)
            cost = damage + zone_weight * zone_cost(path) + plan_cost
            if cost < best[0] - _EPS:
                mid = min(len(path) - 1, math.ceil(0.4 / step))
                if path_safe is not None and (not path_safe(path[0], path[mid]) or not path_safe(path[mid], path[-1])):
                    continue
                best = cost, candidate, power, damage
    initial_cost = baseline_damage + zone_weight * zone_cost(baseline)
    if best[0] + 2.0 >= initial_cost:
        return DodgeDecision(move, boost, True, False, baseline_damage, baseline_damage)
    return DodgeDecision(best[1], best[2], True, True, baseline_damage, best[3])
