"""Deterministic defense scenarios for the standalone combat module."""

import gzip
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from combat_defense import assess_risk, choose_dodge

with gzip.open(ROOT / "kit" / "check" / "stream.jsonl.gz", "rt", encoding="utf-8") as stream:
    CONFIG = json.loads(next(stream))["config"]


def observation(*, me=None, zone=None, **rest):
    own = {
        "id": 0, "pos": [0.0, 0.0, 0.0], "vel": [0.0, 0.0, 0.0], "aim": [0.0, 0.0],
        "hp": 1400, "max_hp": 1400, "energy": 100, "max_energy": 100, "grounded": True,
        "cubes": 0, "upgrades": [], "in_zone": True, "weapons": [None, None], "stats": CONFIG["mech"],
    }
    ring = {"center": [0.0, 0.0], "radius": 150.0, "next_center": [0.0, 0.0], "next_radius": 70.0,
            "shrink_start": 60.0, "shrink_end": 100.0, "dps": 20.0}
    own.update(me or {})
    ring.update(zone or {})
    return {"type": "obs", "tick": 300, "t": 15.0, "alive": 10, "self": own, "zone": ring, **rest}


def projectile(kind, pos, vel):
    return {"id": 200, "kind": kind, "pos": pos, "vel": vel}


def actor(pos, aim, *, charging=False, weapons=("gun",)):
    return {"id": 1, "kind": "mech", "pos": pos, "vel": [0, 0, 0], "aim": aim,
            "hp": 1400, "max_hp": 1400, "cubes": 0, "weapons": list(weapons), "charging": charging}


class CombatDefenseTest(unittest.TestCase):
    def test_keeps_navigation_in_quiet_observation(self):
        result = choose_dodge(observation(), CONFIG, (0.7, -0.3), True)
        self.assertEqual(result.move, (0.7, -0.3))
        self.assertTrue(result.boost)
        self.assertFalse(result.threatened)

    def test_swept_hit_uses_full_mech_height_and_steers_laterally(self):
        p = projectile("gun", [113, 0, 8.8], [-252, 0, 0])
        result = choose_dodge(observation(projectiles=[p]), CONFIG, (0, 0))
        self.assertTrue(result.changed)
        self.assertGreater(abs(result.move[1]), 0.5)
        self.assertEqual(result.baseline_damage, CONFIG["weapons"]["gun"]["damage"])
        self.assertEqual(result.expected_damage, 0)
        p["pos"][2] = 10.0
        above = choose_dodge(observation(projectiles=[p]), CONFIG, (0, 0))
        self.assertFalse(above.threatened)

    def test_elapsed_travel_uses_acceleration(self):
        p = projectile("gun", [35, 0, 5], [-252, 0, 0])
        result = choose_dodge(observation(projectiles=[p]), CONFIG, (0, 0))
        self.assertTrue(result.threatened)
        self.assertFalse(result.changed)
        self.assertEqual(result.expected_damage, result.baseline_damage)

    def test_candidate_must_pass_callers_path_check(self):
        p = projectile("gun", [113, 0, 5], [-252, 0, 0])
        obs = observation(projectiles=[p])
        y_positive_only = lambda a, b: b[1] > a[1]
        result = choose_dodge(obs, CONFIG, (0, 0), path_safe=y_positive_only)
        self.assertTrue(result.changed)
        self.assertGreater(result.move[1], 0.5)
        blocked = choose_dodge(obs, CONFIG, (0, 0), path_safe=lambda a, b: False)
        self.assertFalse(blocked.changed)

    def test_wall_removes_projectile_threat(self):
        p = projectile("gun", [100, 0, 4], [-252, 0, 0])
        obs = observation(projectiles=[p])
        clear = lambda a, b: not a[0] > 55 > b[0]
        result = choose_dodge(obs, CONFIG, (0, 0), line_clear=clear)
        self.assertFalse(result.threatened)
        self.assertEqual(assess_risk(obs, CONFIG, line_clear=clear).score, 0)

    def test_mortar_splash_falloff_and_dodge(self):
        p = projectile("mortar", [6, 0, 22], [0, 0, -2])
        result = choose_dodge(observation(projectiles=[p]), CONFIG, (0, 0), ground_height=lambda x, y: 0)
        self.assertTrue(result.changed)
        self.assertLess(result.move[0], 0)
        self.assertAlmostEqual(result.baseline_damage, 104.0, delta=0.001)
        self.assertEqual(result.expected_damage, 0)

    def test_high_mortar_impact_is_outside_vertical_blast_radius(self):
        p = projectile("mortar", [0, 0, 25], [0, 0, -2])
        result = choose_dodge(observation(projectiles=[p]), CONFIG, (0, 0), ground_height=lambda x, y: 20)
        self.assertFalse(result.threatened)

    def test_charging_beam_responds_to_pitch(self):
        laser = actor([140, 0, 0], [math.pi, 0], charging=True, weapons=("laser",))
        result = choose_dodge(observation(mechs=[laser]), CONFIG, (0, 0))
        self.assertTrue(result.changed)
        self.assertGreater(abs(result.move[1]), 0.5)
        self.assertLess(result.expected_damage, result.baseline_damage)
        laser["aim"][1] = 0.3
        high = choose_dodge(observation(mechs=[laser]), CONFIG, (0, 0))
        self.assertFalse(high.threatened)

    def test_faces_unseen_recent_attacker_using_world_bearing(self):
        event = {"type": "damaged", "weapon": "gun", "amount": 90, "bearing": math.pi, "by": 3, "rear": True}
        risk = assess_risk(observation(events=[event]), CONFIG)
        self.assertEqual(risk.recent_damage, 90)
        self.assertGreater(risk.orientation_cost(0), 0)
        self.assertEqual(risk.orientation_cost(risk.defensive_yaw), 0)

    def test_risk_increases_when_low_hp_or_the_zone_shrinks_onto_us(self):
        enemy = actor([40, 0, 0], [math.pi, 0])
        healthy = assess_risk(observation(mechs=[enemy]), CONFIG)
        weak = assess_risk(observation(mechs=[enemy], me={"hp": 200}), CONFIG)
        self.assertGreater(weak.score, healthy.score)
        shrinking = assess_risk(observation(me={"pos": [10, 0, 0]}, zone={
            "radius": 12, "next_radius": 0, "shrink_start": 10, "shrink_end": 17,
        }), CONFIG)
        self.assertTrue(shrinking.zone_urgent)
        self.assertGreater(shrinking.zone_margin, 0)

    def test_damage_estimate_respects_rear_and_armor(self):
        bullet = projectile("gun", [-35, 0, 5], [252, 0, 0])
        stats = {**CONFIG["mech"], "armor": 0.20}
        result = choose_dodge(observation(projectiles=[bullet], me={"stats": stats}), CONFIG, (0, 0))
        self.assertAlmostEqual(result.baseline_damage, 45 * 1.5 * 0.8)


if __name__ == "__main__":
    unittest.main()
