"""Pure mechfight/1 targeting and trigger geometry.

The caller supplies release timing and optional static cover queries. Motion, spread,
and contact estimates are bot-side predictions based on the current observation.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Iterable, Sequence


Vec3 = tuple[float, float, float]
Aim = tuple[float, float]
LineClear = Callable[[Vec3, Vec3], bool]
GroundZ = Callable[[float, float], float]
ZERO = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class AimSolution:
    kind: str
    aim: Aim
    flight_time: float
    expected_damage: float
    hit_confidence: float
    nominal_damage: float
    impact: Vec3
    distance: float
    _origin: Vec3
    _offset: float
    _center_at_release: Vec3
    _velocity: Vec3
    _radius: float
    _height: float
    _speed: float = 0.0
    _range: float = 0.0
    _gravity: float = 0.0
    _cone: float = 0.0
    _target_id: int | None = None
    _pierce: int = 0


@dataclass(frozen=True, slots=True)
class LightningHit:
    target_id: int
    damage: float


def wrap_angle(angle: float) -> float:
    return (angle + math.pi) % math.tau - math.pi


def weapon_stats(weapon: dict, config: dict) -> dict:
    return (config.get("weapons", {}).get(weapon["kind"], {}) | weapon.get("stats", {}))


def _mech_stat(shooter: dict, config: dict, key: str, default: float) -> float:
    return shooter.get("stats", {}).get(key, config.get("mech", {}).get(key, default))


def _size_scale(entity: dict, config: dict) -> float:
    return 1 + config.get("cubes", {}).get("size_per_cube", 0.05) * entity.get("cubes", 0)


def _entity_kind(entity: dict) -> str:
    return entity.get("kind", "crate" if entity.get("id", 0) >= 1000 else "mech")


def target_dimensions(entity: dict, config: dict) -> tuple[float, float]:
    kind = _entity_kind(entity)
    if kind == "npc":
        stats = config.get("npc", {})
        return stats.get("radius", 1.8), stats.get("height", 3.5)
    if kind == "crate":
        stats = config.get("crates", {})
        return stats.get("radius", 1.4), stats.get("height", 2.4)
    stats = config.get("mech", {})
    scale = _size_scale(entity, config)
    return stats.get("radius", 2.5) * scale, stats.get("height", 9.0) * scale


def _direction(aim: Sequence[float]) -> Vec3:
    yaw, pitch = aim
    horizontal = math.cos(pitch)
    return horizontal * math.cos(yaw), horizontal * math.sin(yaw), math.sin(pitch)


def _aim_at(delta: Vec3) -> Aim:
    dx, dy, dz = delta
    return math.atan2(dy, dx), math.atan2(dz, math.hypot(dx, dy))


def _advance(pos: Sequence[float], vel: Sequence[float], elapsed: float) -> Vec3:
    return pos[0] + vel[0] * elapsed, pos[1] + vel[1] * elapsed, pos[2] + vel[2] * elapsed


def _delta(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _origin(shooter: dict, config: dict, kind: str, delay: float) -> tuple[Vec3, float]:
    x, y, z = _advance(shooter["pos"], shooter.get("vel", ZERO), delay)
    mech = config.get("mech", {})
    scale = _size_scale(shooter, config)
    radius = _mech_stat(shooter, config, "radius", 2.5)
    if "radius" not in shooter.get("stats", {}):
        radius *= scale
    if kind == "mortar":
        muzzle_height = shooter.get("stats", {}).get("height", mech.get("height", 9.0) * scale) + 0.5
    else:
        muzzle_height = shooter.get("stats", {}).get("muzzle_height", mech.get("muzzle_height", 7.0) * scale)
    return (x, y, z + muzzle_height), radius + 0.5


def aim_after_slew(shooter: dict, desired: Aim, config: dict, elapsed: float) -> Aim:
    """Advance actual torso aim under the observed turn and pitch limits."""
    yaw, pitch = shooter["aim"]
    min_pitch = _mech_stat(shooter, config, "min_pitch", -0.7)
    max_pitch = _mech_stat(shooter, config, "max_pitch", 1.3)
    turn = _mech_stat(shooter, config, "turn_rate", 4.0)
    pitch_rate = _mech_stat(shooter, config, "pitch_rate", 3.0)
    for weapon in shooter.get("weapons", ()):
        if weapon and weapon["kind"] == "laser" and weapon.get("charge", 0.0) > 0:
            multiplier = weapon_stats(weapon, config).get("charge_turn_mult", 1.0)
            turn *= multiplier
            pitch_rate *= multiplier
            break
    yaw_step = turn * elapsed
    pitch_step = pitch_rate * elapsed
    yaw_delta = wrap_angle(desired[0] - yaw)
    pitch_delta = max(min_pitch, min(max_pitch, desired[1])) - pitch
    return (
        wrap_angle(yaw + max(-yaw_step, min(yaw_step, yaw_delta))),
        pitch + max(-pitch_step, min(pitch_step, pitch_delta)),
    )


def _positive_root(a: float, b: float, c: float) -> float | None:
    if abs(a) < 1e-9:
        return -c / b if abs(b) > 1e-9 and -c / b >= 0 else None
    discriminant = b * b - 4 * a * c
    if discriminant < 0:
        return None
    q = -0.5 * (b + math.copysign(math.sqrt(discriminant), b))
    roots = (q / a, c / q) if q else (0.0,)
    return min((root for root in roots if root >= 0), default=None)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cylinder_entry(pos: Vec3, vel: Vec3, center: Vec3, radius: float, height: float,
                    reach: float) -> float | None:
    """First parameter in [0, reach] inside a vertical cylinder."""
    x, y, z = _delta(pos, center)
    vx, vy, vz = vel
    half_height = height * 0.5
    lo, hi = 0.0, reach
    if abs(vz) > 1e-9:
        slab_a, slab_b = (-half_height - z) / vz, (half_height - z) / vz
        lo, hi = max(lo, min(slab_a, slab_b)), min(hi, max(slab_a, slab_b))
    elif abs(z) > half_height:
        return None
    a = vx * vx + vy * vy
    c = x * x + y * y - radius * radius
    if a > 1e-9:
        b = x * vx + y * vy
        disc = b * b - a * c
        if disc < 0:
            return None
        root = math.sqrt(disc)
        lo, hi = max(lo, (-b - root) / a), min(hi, (-b + root) / a)
    elif c > 0:
        return None
    return lo if lo <= hi else None


def _straight_entry(origin: Vec3, offset: float, direction: Vec3, center: Vec3,
                    velocity: Vec3, radius: float, height: float, speed: float,
                    reach: float) -> Vec3 | None:
    muzzle = _advance(origin, direction, offset)
    if speed > 0:
        shot_velocity = direction[0] * speed, direction[1] * speed, direction[2] * speed
        hit = _cylinder_entry(muzzle, _delta(shot_velocity, velocity), center, radius, height, reach / speed)
        return None if hit is None else _advance(muzzle, shot_velocity, hit)
    hit = _cylinder_entry(muzzle, direction, center, radius, height, reach)
    return None if hit is None else _advance(muzzle, direction, hit)


def _cover_clear(line_clear: LineClear | None, a: Vec3, b: Vec3) -> bool:
    return line_clear is None or line_clear(a, b)


def _scaled_damage(shooter: dict, config: dict, damage: float) -> float:
    cubes = config.get("cubes", {})
    return damage * (1 + cubes.get("damage_per_cube", 0.04) * min(shooter.get("cubes", 0), cubes.get("stat_cap", 10)))


def _base_damage(target: dict, config: dict, shooter: dict, source: Vec3,
                 impact: Vec3, damage: float) -> float:
    damage = _scaled_damage(shooter, config, damage)
    if _entity_kind(target) == "mech" and target.get("aim") is not None:
        mech = config.get("mech", {})
        facing = target["aim"][0]
        bearing = math.atan2(source[1] - impact[1], source[0] - impact[0])
        if abs(wrap_angle(bearing - facing)) > math.radians(mech.get("front_arc_deg", 180)) * 0.5:
            damage *= mech.get("rear_mult", 1.5)
    return damage


def _confidence(radius: float, flight: float, delay: float, lateral_speed: float,
                distance: float, spread: float = 0.0) -> float:
    # Gaussian spread is documented. The horizontal acceleration term is a heuristic.
    horizon = flight + delay
    uncertainty = lateral_speed * 0.12 * horizon * horizon + 0.3 * horizon
    uncertainty = math.hypot(uncertainty, distance * math.tan(spread))
    if uncertainty <= 1e-6:
        return 1.0
    return math.erf(radius / (math.sqrt(2) * uncertainty))


def _arc_position(origin: Vec3, direction: Vec3, offset: float, speed: float,
                  gravity: float, elapsed: float) -> Vec3:
    x, y, z = _advance(origin, direction, offset + speed * elapsed)
    return x, y, z - 0.5 * gravity * elapsed * elapsed


def _arc_clear(line_clear: LineClear | None, origin: Vec3, direction: Vec3, offset: float,
               speed: float, gravity: float, elapsed: float) -> bool:
    if line_clear is None:
        return True
    # Chords sag below the real arc. Limit the maximum vertical error to 0.2 m.
    count = max(1, math.ceil(elapsed * math.sqrt(gravity / 1.6)))
    previous = _advance(origin, direction, offset)
    if not line_clear(origin, previous):
        return False
    for index in range(1, count + 1):
        point = _arc_position(origin, direction, offset, speed, gravity, elapsed * index / count)
        if not line_clear(previous, point):
            return False
        previous = point
    return True


def _bisect_root(function: Callable[[float], float], start: float, end: float,
                 start_value: float) -> float:
    for _ in range(20):
        midpoint = (start + end) * 0.5
        if (function(midpoint) > 0) == (start_value > 0):
            start = midpoint
        else:
            end = midpoint
    return (start + end) * 0.5


def _mortar_roots(residual: Callable[[float], float], ttl: float) -> list[float]:
    """Bracket the short and long arcs, including a near-tangent max-range shot."""
    roots = []
    previous_time, previous_value = 0.0, residual(0.0)
    minimum_time, minimum_value = previous_time, previous_value
    spacing = ttl / 36
    for index in range(1, 37):
        sample_time = spacing * index
        value = residual(sample_time)
        if (value > 0) != (previous_value > 0):
            roots.append(_bisect_root(residual, previous_time, sample_time, previous_value))
        if value < minimum_value:
            minimum_time, minimum_value = sample_time, value
        previous_time, previous_value = sample_time, value
    if not roots and minimum_value < 3.0 and previous_value > 0:
        lo, hi = max(0.0, minimum_time - spacing), min(ttl, minimum_time + spacing)
        for _ in range(20):
            left, right = (2 * lo + hi) / 3, (lo + 2 * hi) / 3
            if residual(left) < residual(right):
                hi = right
            else:
                lo = left
        midpoint = (lo + hi) * 0.5
        if residual(midpoint) <= 0:
            roots.append(_bisect_root(residual, 0.0, midpoint, residual(0.0)))
            roots.append(_bisect_root(residual, midpoint, ttl, residual(midpoint)))
    return roots


def _mortar_solution(shooter: dict, target: dict, stats: dict, config: dict, delay: float,
                     line_clear: LineClear | None, ground_z: GroundZ | None) -> AimSolution | None:
    speed = stats.get("speed", 72.0)
    gravity = stats.get("gravity", 22.0)
    ttl = stats.get("ttl", 7.0)
    splash_radius = stats.get("splash_radius", 7.0)
    origin, offset = _origin(shooter, config, "mortar", delay)
    radius, height = target_dimensions(target, config)
    velocity = tuple(target.get("vel", ZERO))
    feet = _advance(target["pos"], velocity, delay)
    cx, cy, z = feet[0], feet[1], feet[2] + height * 0.5
    dx, dy, dz = _delta((cx, cy, z), origin)
    vx, vy, vz = velocity
    # An observed vertical velocity identifies an airborne target. Use world gravity
    # and a locally sampled floor to cap the fall. Grounded targets stay at their z.
    target_gravity = config.get("mech", {}).get("gravity", 22.0) if abs(vz) > 0.2 else 0.0
    floor = None
    if ground_z is not None and target_gravity:
        floor = ground_z(cx, cy) + height * 0.5

    def impact_height(flight: float) -> float:
        center_z = z + vz * flight - 0.5 * target_gravity * (flight + delay) ** 2
        return max(center_z, floor) if floor is not None else center_z

    def residual(flight: float) -> float:
        corrected_z = (impact_height(flight) - origin[2]) if target_gravity else dz
        return math.hypot(dx + vx * flight, dy + vy * flight, corrected_z + 0.5 * gravity * flight * flight) - (offset + speed * flight)

    if speed <= 0 or ttl <= 0:
        return None
    min_pitch = _mech_stat(shooter, config, "min_pitch", -0.7)
    max_pitch = _mech_stat(shooter, config, "max_pitch", 1.3)
    lateral_speed = math.hypot(vx, vy)
    shooter_velocity = shooter.get("vel", ZERO)
    shooter_feet = _advance(shooter["pos"], shooter_velocity, delay)
    for flight in _mortar_roots(residual, ttl):
        if flight < 1e-5:
            continue
        impact = cx + vx * flight, cy + vy * flight, impact_height(flight)
        near_future = _advance(shooter_feet, shooter_velocity, min(flight, 0.35))
        safety_radius = splash_radius + radius
        if min(math.hypot(impact[0] - shooter_feet[0], impact[1] - shooter_feet[1]),
               math.hypot(impact[0] - near_future[0], impact[1] - near_future[1])) <= safety_radius:
            continue
        dx_i, dy_i, dz_i = _delta(impact, origin)
        aim = _aim_at((dx_i, dy_i, dz_i + 0.5 * gravity * flight * flight))
        if not min_pitch <= aim[1] <= max_pitch:
            continue
        direction = _direction(aim)
        if not _arc_clear(line_clear, origin, direction, offset, speed, gravity, flight):
            continue
        nominal = stats.get("damage", 200.0) + stats.get("splash", 160.0)
        # At nominal direct contact, the splash center can be a body radius from the
        # target center. A head-on impact has no reliable rear-bearing advantage.
        if splash_radius > 0:
            nominal -= stats.get("splash", 160.0) * (1 - stats.get("splash_edge", 0.3)) * min(1.0, radius / splash_radius)
        nominal = _scaled_damage(shooter, config, nominal)
        distance = math.dist(origin, impact)
        confidence = _confidence(radius + splash_radius * 0.4, flight, delay, lateral_speed, distance)
        return AimSolution("mortar", aim, flight, nominal * confidence, confidence, nominal, impact,
                           max(0.0, distance - offset), origin, offset, (cx, cy, z), velocity,
                           radius, height, speed, 0.0, gravity)
    return None


def solve_aim(shooter: dict, target: dict, weapon: dict, config: dict, release_delay: float = 0.0,
              *, line_clear: LineClear | None = None, ground_z: GroundZ | None = None,
              charge: float | None = None) -> AimSolution | None:
    """Find a legal shot using self/target/weapon observations and hello.config.

    `charge` is the projected laser charge at release. Callers own trigger scheduling.
    `line_clear` checks static shot cover, including terrain and prisms.
    """
    kind = weapon["kind"]
    stats = weapon_stats(weapon, config)
    if kind == "mortar":
        return _mortar_solution(shooter, target, stats, config, release_delay, line_clear, ground_z)
    if kind not in ("gun", "laser", "lightning"):
        return None
    speed = stats.get("speed", 252.0) if kind == "gun" else 0.0
    reach = stats.get("range", {"gun": 230.0, "laser": 280.0, "lightning": 45.0}[kind])
    origin, offset = _origin(shooter, config, kind, release_delay)
    radius, height = target_dimensions(target, config)
    velocity = tuple(target.get("vel", ZERO))
    feet = _advance(target["pos"], velocity, release_delay)
    center = (feet[0], feet[1], feet[2] + height * 0.5)
    cone = math.radians(stats.get("cone_deg", 30))
    min_pitch = _mech_stat(shooter, config, "min_pitch", -0.7)
    max_pitch = _mech_stat(shooter, config, "max_pitch", 1.3)
    projectile_radius = config.get("projectile", {}).get("gun_radius", 0.3) if kind == "gun" else 0.0
    # Try center first, then peek the top or lower torso if cover blocks the center.
    for fraction in (0.5,) if kind == "lightning" else (0.5, 0.8, 0.2):
        point = (feet[0], feet[1], feet[2] + height * fraction)
        relative = _delta(point, origin)
        flight = 0.0
        if kind == "gun":
            if speed <= 0:
                return None
            # |relative + target_velocity*t| = muzzle_offset + projectile_speed*t.
            flight = _positive_root(_dot(velocity, velocity) - speed * speed,
                                    2 * (_dot(relative, velocity) - offset * speed),
                                    _dot(relative, relative) - offset * offset)
            if flight is None:
                continue
            relative = _advance(relative, velocity, flight)
        impact = _advance(origin, relative, 1.0)
        yaw, pitch = _aim_at(relative)
        aim = yaw, max(min_pitch, min(max_pitch, pitch))
        direction = _direction(aim)
        muzzle = _advance(origin, direction, offset)
        distance = math.dist(muzzle, impact)
        if kind == "lightning":
            if distance > reach:
                continue
            entry = impact
        else:
            entry = _straight_entry(origin, offset, direction, center, velocity, radius + projectile_radius,
                                    height + projectile_radius * 2, speed, reach)
            if entry is None:
                continue
        if not _cover_clear(line_clear, origin, entry):
            continue
        damage = stats.get("damage", {"gun": 45.0, "laser": 315.0, "lightning": 60.0}[kind])
        if kind == "laser":
            full = stats.get("charge", 0.75)
            minimum = stats.get("min_charge", 0.15)
            released = max(minimum, weapon.get("charge", 0.0) if charge is None else charge)
            fraction = min(1.0, released / full) if full > 0 else 1.0
            start = stats.get("min_frac", 0.35)
            damage *= start + (1.0 - start) * fraction
            if "mortar" in target.get("weapons", ()):
                damage *= 1 + stats.get("cookoff", 0.6)
        damage = _base_damage(target, config, shooter, origin, impact, damage)
        confidence = _confidence(radius + projectile_radius, flight, release_delay, math.hypot(*velocity[:2]),
                                 distance, math.radians(stats.get("spread_deg", 0.0)))
        return AimSolution(kind, aim, flight, damage * confidence, confidence, damage, impact,
                           distance, origin, offset, center, velocity, radius + projectile_radius,
                           height + projectile_radius * 2, speed, reach, 0.0, cone,
                           target.get("id"), int(stats.get("pierce", 0)))
    return None


def fire_on_aim(solution: AimSolution, shooter: dict, config: dict, elapsed: float, *,
                desired: Aim | None = None, line_clear: LineClear | None = None,
                blockers: Iterable[dict] = ()) -> bool:
    """Check a candidate against the actual aim at the earliest possible release."""
    command = solution.aim if desired is None else desired
    actual = aim_after_slew(shooter, command, config, elapsed)
    direction = _direction(actual)
    if solution.kind == "lightning":
        muzzle = _advance(solution._origin, direction, solution._offset)
        return (abs(wrap_angle(actual[0] - solution.aim[0])) <= solution._cone
                and math.dist(muzzle, solution.impact) <= solution._range
                and _cover_clear(line_clear, solution._origin, solution.impact))
    if solution.kind == "mortar":
        impact = _arc_position(solution._origin, direction, solution._offset, solution._speed,
                               solution._gravity, solution.flight_time)
        dx, dy, dz = _delta(impact, solution.impact)
        return (math.hypot(dx, dy) <= solution._radius and abs(dz) <= solution._height * 0.5
                and _arc_clear(line_clear, solution._origin, direction, solution._offset,
                               solution._speed, solution._gravity, solution.flight_time))
    entry = _straight_entry(solution._origin, solution._offset, direction, solution._center_at_release,
                            solution._velocity, solution._radius, solution._height, solution._speed, solution._range)
    if entry is None or not _cover_clear(line_clear, solution._origin, entry):
        return False
    target_distance = math.dist(solution._origin, entry)
    pierce = solution._pierce
    projectile_radius = config.get("projectile", {}).get("gun_radius", 0.3) if solution.kind == "gun" else 0.0
    for entity in blockers:
        if entity.get("id") in (solution._target_id, shooter.get("id")) or entity.get("hp") == 0:
            continue
        velocity = tuple(entity.get("vel", ZERO))
        x, y, z = _advance(entity["pos"], velocity, elapsed)
        radius, height = target_dimensions(entity, config)
        hit = _straight_entry(solution._origin, solution._offset, direction, (x, y, z + height * 0.5),
                              velocity, radius + projectile_radius, height + projectile_radius * 2,
                              solution._speed, solution._range)
        if hit is not None and math.dist(solution._origin, hit) < target_distance:
            if pierce <= 0:
                return False
            pierce -= 1
    return True


def trace_lightning(shooter: dict, candidates: Iterable[dict], weapon: dict, config: dict,
                    aim: Aim, release_delay: float = 0.0, *,
                    line_clear: LineClear | None = None, include_chain: bool = True) -> tuple[LightningHit, ...]:
    """Predict the nearest visible primary target and nearest eligible chain jumps."""
    stats = weapon_stats(weapon, config)
    origin, offset = _origin(shooter, config, "lightning", release_delay)
    muzzle = _advance(origin, _direction(aim), offset)
    cone = math.radians(stats.get("cone_deg", 30.0))
    reach = stats.get("range", 45.0)
    chain = int(stats.get("chain", 2)) if include_chain else 0
    chain_range = stats.get("chain_range", 14.0)
    points = []
    for target in candidates:
        if target["id"] == shooter.get("id") or target.get("hp", 1) == 0:
            continue
        feet = _advance(target["pos"], target.get("vel", ZERO), release_delay)
        _, height = target_dimensions(target, config)
        point = feet[0], feet[1], feet[2] + height * 0.5
        if math.dist(muzzle, point) <= reach + chain * chain_range:
            points.append((target, point))
    remaining = {target["id"]: (target, point) for target, point in points}
    hits = []
    source = muzzle
    for jump in range(chain + 1):
        possible = []
        for target_id, (target, point) in remaining.items():
            distance = math.dist(source, point)
            if distance > reach:
                continue
            yaw = math.atan2(point[1] - origin[1], point[0] - origin[0])
            if jump == 0 and abs(wrap_angle(yaw - aim[0])) > cone:
                continue
            possible.append((distance, target_id, target, point))
        hop = next((candidate for candidate in sorted(possible)
                    if _cover_clear(line_clear, origin if jump == 0 else source, candidate[3])), None)
        if hop is None:
            break
        _, target_id, target, point = hop
        damage = stats.get("damage", 60.0) * stats.get("chain_falloff", 0.7) ** jump
        hits.append(LightningHit(target_id, _base_damage(target, config, shooter, source, point, damage)))
        del remaining[target_id]
        source, reach = point, chain_range
    return tuple(hits)
