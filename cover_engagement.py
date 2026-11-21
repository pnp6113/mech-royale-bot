"""Short, stateful ground-cover engagements for the v6 gun policy."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class CoverOrder:
    move: tuple[float, float]
    phase: str
    reload: bool = False
    gun_clear: bool = False


class CoverEngagement:
    """Move between one protected hold and one firing point using live observations."""

    def __init__(self, search, config):
        self.search = search
        self.config = config
        self.pair = None
        self.phase = None
        self.phase_since = 0.0
        self.started_at = 0.0
        self.next_search = 0.0
        self.next_check = 0.0
        self.progress_at = 0.0
        self.progress_dist = 0.0
        self.ammo_at_peek = 0
        self.bursts = 0
        self.failed_hold = None
        self.failed_until = 0.0

    def decide(self, obs, target, *, threats=(), eligible=True):
        now = obs.get("t", 0.0)
        me = obs["self"]
        if not eligible or target is None or not me.get("grounded", True) or not me.get("in_zone", True):
            return self._stop(now, 0.35)
        gun = next((w for w in me.get("weapons", ()) if w and w["kind"] == "gun"), None)
        if gun is None:
            return self._stop(now, 0.35)

        pos = me["pos"]
        distance = math.dist(pos[:2], target["pos"][:2])
        if (distance < 22.0 or distance > 86.0 or self._secondary_priority(me, target, distance)):
            return self._stop(now, 0.35)

        stats = me.get("stats", {})
        height = stats.get("height", self.config.get("mech", {}).get("height", 9.0))
        radius = stats.get("radius", self.config.get("mech", {}).get("radius", 2.5))
        muzzle = stats.get("muzzle_height", self.config.get("mech", {}).get("muzzle_height", 7.0))
        gun_stats = self.config.get("weapons", {}).get("gun", {}) | gun.get("stats", {})
        speed = gun_stats.get("speed", 252.0)
        ammo = gun.get("ammo", gun_stats.get("mag", 12))
        reloading = gun.get("reload", 0.0) > 0.0
        hull = {"body_height": height, "body_radius": radius}
        nearby = self._nearby(pos, target, threats)
        if self.pair is not None and self.pair.target_id != target["id"]:
            return self._stop(now, 0.45)

        if self.pair is None:
            if now < self.next_search:
                return None
            self.next_search = now + 0.9
            exclude = self.failed_hold if now < self.failed_until else None
            pair = self.search.find(
                obs, target, threats=nearby, exclude_hold=exclude, projectile_speed=speed,
                max_fire_range=gun_stats.get("range", 230.0), require_secondary_cover=True,
            )
            if pair is None:
                return None
            self.pair = pair
            self.started_at = now
            self.next_check = now
            self.bursts = 0
            # Stay on offense if we already have a clean shot and the peek is close.
            immediate_peek = (
                not reloading and ammo > 3 and math.dist(pos[:2], pair.peek[:2]) <= 8.0
                and self.search.path_clear(pos, pair.peek, **hull)
                and self.search.can_fire(pos, target, muzzle_height=muzzle, projectile_speed=speed)
            )
            self._phase("peek" if immediate_peek else "take_cover", now, pos)
            self.ammo_at_peek = ammo

        pair = self.pair
        if now - self.started_at >= 12.0 or (self.bursts >= 3 and self.phase == "hold"):
            return self._stop(now, 1.4, exclude=True)
        if now >= self.next_check:
            self.next_check = now + 0.25
            if not self._usable(obs, target, nearby, hull, muzzle, speed):
                return self._stop(now, 0.35, exclude=True)

        hold_dist = math.dist(pos[:2], pair.hold[:2])
        peek_dist = math.dist(pos[:2], pair.peek[:2])
        if self.phase in ("take_cover", "retract") and hold_dist <= 1.0:
            self._phase("hold", now, pos)

        if self.phase == "hold":
            if hold_dist > 1.6:
                self._phase("retract", now, pos)
            elif reloading or ammo <= 3 or now - self.phase_since < 0.35 or gun.get("cooldown", 0.0) > 0.18:
                return CoverOrder((0.0, 0.0), "hold", ammo <= 3 and not reloading)
            else:
                self._phase("peek", now, pos)
                self.ammo_at_peek = ammo

        clear = self.search.can_fire(pos, target, muzzle_height=muzzle, projectile_speed=speed)
        if self.phase == "peek" and peek_dist <= 1.0 and clear:
            self._phase("fire", now, pos)

        if self.phase in ("peek", "fire"):
            damage = sum(e["amount"] for e in obs.get("events", ()) if e["type"] == "damaged" and e.get("by") is not None)
            shot_limit = 3 if (target.get("hp") or 1400.0) <= 180.0 else 2
            end_burst = self.phase == "fire" and (
                now - self.phase_since >= 0.65 or self.ammo_at_peek - ammo >= shot_limit or not clear
            )
            if end_burst or reloading or ammo <= 3 or damage >= 90.0:
                self.bursts += 1
                self._phase("retract", now, pos)

        if self.phase == "fire":
            return CoverOrder((0.0, 0.0), "fire", gun_clear=clear)

        destination = pair.peek if self.phase == "peek" else pair.hold
        distance_left = math.dist(pos[:2], destination[:2])
        if distance_left <= 0.4 or not self.search.path_clear(pos, destination, **hull):
            return self._stop(now, 0.8, exclude=True)
        if now - self.progress_at >= 0.8:
            if self.progress_dist - distance_left < 0.9:
                return self._stop(now, 1.0, exclude=True)
            self.progress_at, self.progress_dist = now, distance_left

        scale = 1.0 / max(distance_left, 4.0)
        move = ((destination[0] - pos[0]) * scale, (destination[1] - pos[1]) * scale)
        return CoverOrder(move, self.phase, gun_clear=clear)

    def _secondary_priority(self, me, target, distance):
        rules = self.config.get("weapons", {})
        for weapon in me["weapons"]:
            if weapon is None:
                continue
            if weapon["kind"] == "laser":
                return True
            if weapon["kind"] == "lightning":
                reach = weapon.get("stats", {}).get("range", rules.get("lightning", {}).get("range", 45.0))
                if distance <= 1.1 * reach:
                    return True
        enemy_arc = rules.get("lightning", {}).get("range", 45.0)
        return "lightning" in target.get("weapons", ()) and distance < 1.12 * enemy_arc

    @staticmethod
    def _nearby(pos, target, threats):
        others = [e for e in threats if e["id"] != target["id"] and math.dist(pos[:2], e["pos"][:2]) <= 150.0]
        return tuple(sorted(others, key=lambda e: (math.dist(pos[:2], e["pos"][:2]), e["id"]))[:3])

    def _usable(self, obs, target, nearby, hull, muzzle, speed):
        pair = self.pair
        if not self.search.zone_safe(pair.hold, obs, seconds=3.2) or not self.search.zone_safe(pair.peek, obs, seconds=2.0):
            return False
        if not self.search.hidden(pair.hold, target, **hull):
            return False
        if not self.search.can_fire(pair.peek, target, muzzle_height=muzzle, projectile_speed=speed):
            return False
        # Any independent crossfire angle turns a planned one-on-one peek into a relocation.
        return all(
            self.search.hidden(pair.hold, enemy, **hull)
            and self.search.hidden(pair.peek, enemy, **hull)
            for enemy in nearby
        )

    def _phase(self, name, now, pos):
        self.phase, self.phase_since = name, now
        self.progress_at = now
        point = self.pair.peek if name in ("peek", "fire") else self.pair.hold
        self.progress_dist = math.dist(pos[:2], point[:2])

    def _stop(self, now, pause, *, exclude=False):
        if self.pair is not None:
            if exclude:
                self.failed_hold, self.failed_until = self.pair.hold, now + 4.0
            self.pair = self.phase = None
            self.next_search = now + pause
        return None
