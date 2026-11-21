"""Deterministic targeting checks using values from the official replay hello."""
import gzip
import json
import math
from pathlib import Path
import unittest

from combat_targeting import aim_after_slew, fire_on_aim, solve_aim, trace_lightning


class TargetingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stream = Path(__file__).resolve().parents[1] / "kit" / "check" / "stream.jsonl.gz"
        with gzip.open(stream, "rt", encoding="utf-8") as source:
            cls.config = json.loads(next(source))["config"]

    def setUp(self):
        self.shooter = {"id": 1, "pos": (0, 0, 0), "vel": (0, 0, 0), "aim": (0, 0), "cubes": 0}

    def weapon(self, kind, **changes):
        return {"kind": kind, "cooldown": 0, "stats": self.config["weapons"][kind] | changes}

    def target(self, x, y=0, z=0, *, id_=2, **changes):
        return {"id": id_, "kind": "mech", "pos": (x, y, z), "vel": (0, 0, 0), "aim": (math.pi, 0)} | changes

    def test_gun_leads_motion_from_future_muzzle_with_barrel_offset(self):
        self.shooter["vel"] = (8, 0, 0)
        target = self.target(100, z=2.5, vel=(0, 12, 0))
        weapon = self.weapon("gun", speed=220)
        solution = solve_aim(self.shooter, target, weapon, self.config, 0.05)
        flight = solution.flight_time
        self.assertAlmostEqual(solution.impact[1], 12 * (0.05 + flight), places=7)
        self.assertAlmostEqual(math.hypot(100 - 8 * 0.05, solution.impact[1]), 3 + 220 * flight, places=7)
        self.assertAlmostEqual(solution.aim[0], math.atan2(solution.impact[1], 99.6), places=7)
        self.assertTrue(fire_on_aim(solution, self.shooter, self.config, 0.05))

    def test_fire_uses_actual_aim_and_wraps_yaw(self):
        target = self.target(90)
        solution = solve_aim(self.shooter, target, self.weapon("gun"), self.config)
        self.shooter["aim"] = (math.pi - 0.05, 0)
        self.assertFalse(fire_on_aim(solution, self.shooter, self.config, 0.05))
        self.assertTrue(fire_on_aim(solution, self.shooter, self.config, 1))
        actual = aim_after_slew(self.shooter, (-math.pi + 0.07, 0.25), self.config, 0.05)
        self.assertAlmostEqual(actual[0], -math.pi + 0.07)
        self.assertAlmostEqual(actual[1], 0.15)

    def test_direct_shot_peeks_above_center_cover(self):
        def clear(start, end):
            if not min(start[0], end[0]) < 85 < max(start[0], end[0]):
                return True
            crossing = (85 - start[0]) / (end[0] - start[0])
            return start[2] + (end[2] - start[2]) * crossing > 6

        solution = solve_aim(self.shooter, self.target(90), self.weapon("gun"), self.config, line_clear=clear)
        self.assertIsNotNone(solution)
        self.assertGreater(solution.impact[2], 6)
        self.assertTrue(fire_on_aim(solution, self.shooter, self.config, 0.05, line_clear=clear))
        self.assertFalse(fire_on_aim(solution, self.shooter, self.config, 0.05, desired=(0, -0.05), line_clear=clear))

    def test_laser_aims_at_release_and_scales_charge_cookoff_rear_and_cubes(self):
        self.shooter["cubes"] = 20
        enemy = self.target(80, vel=(0, 12, 0), weapons=["mortar"], aim=(0, 0))
        weapon = self.weapon("laser")
        short = solve_aim(self.shooter, enemy, weapon, self.config, 0.05, charge=0.15)
        full = solve_aim(self.shooter, enemy, weapon, self.config, 0.05, charge=0.75)
        self.assertEqual(full.flight_time, 0)
        self.assertAlmostEqual(full.impact[1], 0.6)
        self.assertAlmostEqual(full.nominal_damage, 315 * 1.6 * 1.5 * 1.4)
        self.assertAlmostEqual(short.nominal_damage / full.nominal_damage, 0.35 + 0.65 * 0.2)

    def test_laser_pierce_with_intervening_crate(self):
        crate = {"id": 1001, "pos": (25, 0, 5), "hp": 300}
        enemy = self.target(80)
        for pierce, works in ((0, False), (1, True)):
            weapon = self.weapon("laser", pierce=pierce)
            solution = solve_aim(self.shooter, enemy, weapon, self.config)
            self.assertEqual(fire_on_aim(solution, self.shooter, self.config, 0.05, blockers=(crate, enemy)), works)

    def test_laser_charging_uses_effective_slew_multiplier(self):
        self.shooter["weapons"] = [self.weapon("laser", charge_turn_mult=0.5) | {"charge": 0.2}, None]
        actual = aim_after_slew(self.shooter, (1, 0.5), self.config, 0.1)
        self.assertAlmostEqual(actual[0], 0.2)
        self.assertAlmostEqual(actual[1], 0.15)

    def test_mortar_intercept_obeys_gravity_and_launch_offset(self):
        enemy = self.target(110, 2, vel=(0, 8, 0))
        solution = solve_aim(self.shooter, enemy, self.weapon("mortar"), self.config, 0.1)
        self.assertIsNotNone(solution)
        yaw, pitch = solution.aim
        t = solution.flight_time
        travel = 3 + 72 * t
        shell = (travel * math.cos(pitch) * math.cos(yaw), travel * math.cos(pitch) * math.sin(yaw),
                 9.5 + travel * math.sin(pitch) - 11 * t ** 2)
        for got, want in zip(shell, (110, 2 + 8 * (t + 0.1), 4.5)):
            self.assertAlmostEqual(got, want, places=4)

    def test_mortar_tries_high_arc_when_low_is_blocked(self):
        def wall(start, end):
            if not min(start[0], end[0]) < 73 < max(start[0], end[0]):
                return True
            crossing = (73 - start[0]) / (end[0] - start[0])
            return start[2] + (end[2] - start[2]) * crossing > 33

        enemy = self.target(150)
        weapon = self.weapon("mortar")
        low = solve_aim(self.shooter, enemy, weapon, self.config)
        high = solve_aim(self.shooter, enemy, weapon, self.config, line_clear=wall)
        self.assertIsNotNone(high)
        self.assertGreater(high.aim[1], low.aim[1])
        self.assertGreater(high.flight_time, low.flight_time)
        self.assertFalse(fire_on_aim(low, self.shooter, self.config, 1, line_clear=wall))
        self.assertTrue(fire_on_aim(high, self.shooter, self.config, 1, line_clear=wall))

    def test_mortar_filters_self_splash_and_unreachable_targets(self):
        weapon = self.weapon("mortar")
        self.assertIsNone(solve_aim(self.shooter, self.target(8), weapon, self.config))
        self.assertIsNone(solve_aim(self.shooter, self.target(270), weapon, self.config))
        self.assertIsNone(solve_aim(self.shooter, self.target(100), self.weapon("mortar", ttl=0.5), self.config))
        self.shooter["vel"] = (15, 0, 0)
        self.assertIsNone(solve_aim(self.shooter, self.target(10), weapon, self.config))

    def test_lightning_uses_yaw_cone_nearest_target_and_chain_falloff(self):
        crate = {"id": 1001, "pos": (16, 0, 2.5), "hp": 300}
        enemy = self.target(27)
        third = self.target(37, id_=3)
        off_axis = self.target(0, 6, id_=4)
        weapon = self.weapon("lightning")
        hits = trace_lightning(self.shooter, (enemy, off_axis, third, crate), weapon, self.config, (0, 0))
        self.assertEqual([hit.target_id for hit in hits], [1001, 2, 3])
        for result, nominal in zip(hits, (60, 42, 29.4)):
            self.assertAlmostEqual(result.damage, nominal)
        primary = trace_lightning(self.shooter, (enemy, crate), weapon, self.config, (0, 0), include_chain=False)
        self.assertEqual([hit.target_id for hit in primary], [1001])
        self.assertFalse(trace_lightning(self.shooter, (enemy,), weapon, self.config, (0, 0),
                                         line_clear=lambda a, b: False))

    def test_lightning_fire_checks_achieved_yaw_at_release(self):
        angle = math.radians(26)
        enemy = self.target(30 * math.cos(angle), 30 * math.sin(angle))
        weapon = self.weapon("lightning")
        solution = solve_aim(self.shooter, enemy, weapon, self.config)
        self.assertTrue(fire_on_aim(solution, self.shooter, self.config, 0, desired=(0, 0)))
        self.shooter["aim"] = (math.radians(-10), 0)
        self.assertFalse(fire_on_aim(solution, self.shooter, self.config, 0, desired=(0, 0)))


if __name__ == "__main__":
    unittest.main()
