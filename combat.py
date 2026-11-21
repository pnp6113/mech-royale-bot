"""Opt-in combat decisions for the mechfight/1 Python bot."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, ceil, hypot, pi

from combat_defense import CombatRisk, DodgeDecision, assess_risk, choose_dodge
from combat_targeting import AimSolution, aim_after_slew, fire_on_aim, solve_aim, trace_lightning, weapon_stats


@dataclass(frozen=True, slots=True)
class CombatDecision:
    move: tuple[float, float]
    boost: bool
    aim: tuple[float, float]
    fire: tuple[bool, bool]
    reload: bool
    risk: CombatRisk
    dodge: DodgeDecision | None
    target_id: int | None = None
    weapon: str | None = None

    def action_fields(self) -> dict:
        """Return the action fields this controller owns, ready to merge."""
        return {
            "move": list(self.move),
            "boost": self.boost,
            "aim": list(self.aim),
            "fire": list(self.fire),
            "reload": self.reload,
        }


@dataclass(frozen=True, slots=True)
class _Opportunity:
    slot: int
    kind: str
    target: dict
    solution: AimSolution
    delay: float
    ready: bool
    value: float
    trigger: bool = True


def _angle_gap(a, b):
    return abs((a - b + pi) % (2 * pi) - pi)


class CombatController:
    """One instance per match. Navigation and loot decisions stay with the caller."""

    def __init__(self, config, *, arena=None):
        self.config = config
        match = config.get("match", {})
        self.step = 1.0 / match.get("tick_hz", 20)
        self.interval = self.step * match.get("decide_every", 2)
        self.arena = arena
        self.line_clear = arena.los if arena is not None else None
        self.ground_height = arena.ground if arena is not None else None
        self.laser_blocked_until = float("-inf")
        self.charge_since = None
        self.last_damage_bearing = None
        self.last_damage_time = float("-inf")

    def decide(
        self, obs, *, move=(0.0, 0.0), boost=False, engage=True,
        target_ids=None, default_aim=None, allow_dodge=True, path_safe=None, jump=False,
    ) -> CombatDecision:
        """Evaluate one observation and optionally override a planned movement vector.

        By default, engage observed opponent mechs. Pass target_ids to allow an
        observed NPC or crate, or to restrict attacks to specific opponents.
        """
        me = obs["self"]
        now = obs["t"]
        weapons = me["weapons"]
        direction_damage = 0.0
        for event in obs["events"]:
            if event["type"] == "effect" and event["effect"] == "short":
                seconds = self.config.get("weapons", {}).get("lightning", {}).get("short_s", 2.0)
                self.laser_blocked_until = max(self.laser_blocked_until, now + seconds)
            if event["type"] == "damaged" and event.get("bearing") is not None and event["amount"] > direction_damage:
                direction_damage = event["amount"]
                self.last_damage_bearing = event["bearing"]
                self.last_damage_time = now

        risk = assess_risk(obs, self.config, line_clear=self.line_clear)
        dodge = None
        move = tuple(move)
        if allow_dodge:
            if path_safe is None and self.arena is not None and me["grounded"]:
                path_safe = self._walk_safe
            dodge = choose_dodge(
                obs, self.config, move, boost, path_safe=path_safe,
                ground_height=self.ground_height, line_clear=self.line_clear, assessment=risk,
                jump=jump,
            )
            move, boost = dodge.move, dodge.boost

        fallback = tuple(default_aim if default_aim is not None else me["aim"])
        if risk.defensive_yaw is not None:
            fallback = (risk.defensive_yaw, 0.0)
        elif now - self.last_damage_time <= 0.85:
            fallback = (self.last_damage_bearing, 0.0)
        imminent = dodge is not None and dodge.threatened
        keep_mobile = boost or jump or imminent or risk.zone_urgent or risk.score >= 0.55
        targets = self._targets(obs, target_ids) if engage else []
        scene = (*obs["mechs"], *obs["crates"]) if targets else ()
        laser_slot = next((
            i for i, w in enumerate(weapons) if w and w["kind"] == "laser" and w.get("charge", 0) > 0
        ), None)
        options = []
        active = None
        if laser_slot is None:
            self.charge_since = None
        else:
            if self.charge_since is None:
                self.charge_since = now - weapons[laser_slot]["charge"]
            active = self._active_laser(obs, laser_slot, targets, scene, risk, keep_mobile)
            if active is not None:
                options.append(active)
        for slot, weapon in enumerate(weapons):
            if weapon is None or slot == laser_slot or not targets:
                continue
            if weapon["kind"] == "laser" and (keep_mobile or now + self.step < self.laser_blocked_until):
                continue
            options.extend(self._opportunities(me, slot, targets, scene, risk))

        if options:
            best = max(options, key=lambda option: option.value)
            if active is not None and active.value > 0 and active.value >= 0.55 * best.value:
                best = active
            if best.value > 0:
                return self._commit(me, best, options, scene, move, boost, risk, dodge)

        return CombatDecision(move, boost, fallback, (False, False), self._reload(weapons, risk), risk, dodge)

    def _walk_safe(self, start, end):
        arena = self.arena
        return (
            arena.walk_clear(start[0], start[1], end[0], end[1])
            and arena.ground(end[0], end[1]) - start[2] <= arena.step_up + hypot(
                end[0] - start[0], end[1] - start[1],
            ) * arena.max_rise / arena.ncs
        )

    def _targets(self, obs, target_ids):
        attackers = {
            e["by"] for e in obs["events"]
            if e["type"] == "damaged" and e.get("by") is not None
        }
        allowed = None if target_ids is None else set(target_ids)
        targets = [
            m for m in obs["mechs"]
            if (m["kind"] == "mech" if allowed is None else m["id"] in allowed)
        ]
        if allowed is not None:
            targets.extend({**c, "kind": "crate"} for c in obs["crates"] if c["id"] in allowed)
        x, y, z = obs["self"]["pos"]
        current_yaw = obs["self"]["aim"][0]

        def priority(target):
            tx, ty, tz = target["pos"]
            return (
                hypot(tx - x, ty - y, tz - z)
                + 15.0 * _angle_gap(atan2(ty - y, tx - x), current_yaw)
                - 35.0 * (target["id"] in attackers)
                - 20.0 * target.get("charging", False),
                target["id"],
            )

        return sorted(targets, key=priority)[:8]

    def _shot_delay(self, cooldown):
        return max(1, ceil(cooldown / self.step - 1e-8)) * self.step

    def _opportunities(self, me, slot, targets, scene, risk):
        weapon = me["weapons"][slot]
        kind = weapon["kind"]
        stats = weapon_stats(weapon, self.config)
        cooldown = weapon["cooldown"]
        delay = self._shot_delay(cooldown)
        if kind == "gun":
            if weapon.get("ammo", stats.get("mag", 12)) <= 0 or weapon.get("reload", 0) > self.interval:
                return []
            delay = self._shot_delay(max(cooldown, weapon.get("reload", 0)))
        elif kind == "lightning":
            # Reserve roughly one second of boost while disengaging.
            reserve = me["stats"].get("boost_drain", 17.0) if risk.zone_urgent or risk.score > 0.45 else 0.0
            if me["energy"] < stats.get("energy", 3.0) + reserve:
                return []
        if delay > 2 * self.interval + 1e-8:
            return []

        fire_delay = delay
        charge = None
        if kind == "laser":
            charge = stats.get("charge", 0.75)
            fire_delay += charge
        options = []
        for target in targets:
            solution = self._solve(me, target, weapon, fire_delay, charge)
            if solution is None:
                continue
            ready = delay <= self.interval + 1e-8
            damage = solution.expected_damage
            if ready:
                at_release = self._trigger_damage(me, target, weapon, solution, solution.aim, fire_delay, scene)
                ready = at_release is not None
                if ready:
                    damage = at_release
            value = self._value(me, target, kind, stats, solution.aim, damage, risk, ready, charge or 0.0)
            if cooldown > self.interval:
                value *= 0.7
            options.append(_Opportunity(slot, kind, target, solution, fire_delay, ready, value))
        return options

    def _active_laser(self, obs, slot, targets, scene, risk, keep_mobile):
        me = obs["self"]
        weapon = me["weapons"][slot]
        stats = weapon_stats(weapon, self.config)
        full = stats.get("charge", 0.75)
        charge = weapon["charge"]
        minimum = stats.get("min_charge", 0.15)
        delay = self._shot_delay(max(0.0, minimum - charge))
        force_release = obs["t"] + self.interval >= self.charge_since + full + stats.get("hold_s", 2.0)
        best = None
        for target in targets:
            solution = self._solve(me, target, weapon, delay, max(minimum, charge))
            if solution is None:
                continue
            ready = self._trigger_damage(me, target, weapon, solution, solution.aim, delay, scene) is not None
            hp = target.get("hp")
            finish = hp is not None and solution.expected_damage >= 1.10 * hp
            # Hold to gather charge and slew, then release promptly when aligned.
            release = force_release or (ready and (keep_mobile or finish or charge >= full - 1e-8))
            if keep_mobile and not release:
                continue
            value = self._value(me, target, "laser", stats, solution.aim, solution.expected_damage, risk, ready, 0.0)
            if release:
                value *= 1.35
            option = _Opportunity(slot, "laser", target, solution, delay, ready, value, not release)
            if best is None or option.value > best.value:
                best = option
        return best

    def _solve(self, me, target, weapon, delay, charge):
        return solve_aim(
            me, target, weapon, self.config, delay, line_clear=self.line_clear,
            ground_z=self.ground_height, charge=charge,
        )

    def _value(self, me, target, kind, stats, aim, damage, risk, ready, charge_time):
        cycle = stats.get("cooldown", self.interval) + charge_time
        if kind == "gun":
            cycle += stats.get("reload", 2.4) / max(1.0, stats.get("mag", 12))
        hp = target.get("hp")
        pressure = min(1.0, damage / hp) if hp is not None and hp > 0 else 0.0
        value = damage / max(self.interval, cycle) * (1.0 + 0.35 * pressure)
        target_weapons = target.get("weapons", ())
        if kind == "lightning" and "laser" in target_weapons:
            value *= 1.7 if target.get("charging") else 1.2
        elif kind == "mortar" and target["kind"] == "mech":
            value *= 1.35 if "lightning" in target_weapons else 1.15
        if target["kind"] != "mech":
            value *= 0.55 * (1.0 - risk.score)
        if not ready:
            value *= 0.45
        if kind == "laser" and charge_time:
            value *= 0.65 * (1.0 - risk.score)
        yaw, pitch = aim
        current_yaw, current_pitch = me["aim"]
        value /= 1.0 + 0.7 * _angle_gap(yaw, current_yaw) + 0.35 * abs(pitch - current_pitch)
        if risk.defensive_yaw is not None:
            extra_rear_exposure = risk.orientation_cost(yaw) - risk.orientation_cost(risk.defensive_yaw)
            value -= (0.6 + 1.4 * risk.score) * max(0.0, extra_rear_exposure)
        return value

    def _trigger_damage(self, me, target, weapon, solution, command, delay, scene):
        blockers = scene if weapon["kind"] in ("gun", "laser") else ()
        if not fire_on_aim(
            solution, me, self.config, delay, desired=command, line_clear=self.line_clear, blockers=blockers,
        ):
            return None
        if weapon["kind"] != "lightning":
            return solution.expected_damage
        actual = aim_after_slew(me, command, self.config, delay)
        hits = trace_lightning(
            me, scene, weapon, self.config, actual, delay, line_clear=self.line_clear,
        )
        intended = next((hit.damage for hit in hits if hit.target_id == target["id"]), None)
        if intended is None:
            return None
        opponents = {actor["id"] for actor in scene if actor.get("kind") == "mech"}
        return intended + 0.35 * sum(
            hit.damage for hit in hits if hit.target_id in opponents and hit.target_id != target["id"]
        )

    def _commit(self, me, best, options, scene, move, boost, risk, dodge):
        weapons = me["weapons"]
        aim = best.solution.aim
        fire = [False, False]
        already_charging = best.kind == "laser" and weapons[best.slot].get("charge", 0.0) > 0
        fire[best.slot] = best.trigger and (best.ready or already_charging)
        for option in options:
            if option.slot == best.slot or option.kind == "laser" or not option.ready or option.value <= 0:
                continue
            if self._trigger_damage(
                me, option.target, weapons[option.slot], option.solution, aim, option.delay, scene,
            ) is not None:
                fire[option.slot] = True
        reload = self._reload(weapons, risk) and not any(
            held and weapon["kind"] == "gun" for held, weapon in zip(fire, weapons)
        ) and best.kind != "gun"
        return CombatDecision(
            move, boost, aim, tuple(fire), reload, risk, dodge, best.target["id"], best.kind,
        )

    @staticmethod
    def _reload(weapons, risk):
        for weapon in weapons:
            if weapon is None or weapon["kind"] != "gun" or weapon.get("reload", 0) > 0:
                continue
            ammo = weapon.get("ammo", 12)
            if ammo == 0 or (ammo <= 3 and risk.score < 0.3 and risk.visible_pressure < 0.15):
                return True
        return False
