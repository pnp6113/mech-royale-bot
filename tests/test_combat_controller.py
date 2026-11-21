"""Deterministic controller tests using settings from the official match fixture."""

import gzip
import json
from pathlib import Path
import unittest

from combat import CombatController


with gzip.open(Path(__file__).resolve().parents[1] / "kit/check/stream.jsonl.gz", "rt") as stream:
    CONFIG = json.loads(next(stream))["config"]


def weapon(kind, **state):
    result = {"kind": kind, "cooldown": 0.0, "stats": dict(CONFIG["weapons"][kind])}
    if kind == "gun":
        result.update(ammo=12, reload=0.0)
    if kind == "laser":
        result["charge"] = 0.0
    result.update(state)
    return result


def enemy(id_=1, pos=(80.0, 0.0, 0.0), **fields):
    return {
        "id": id_, "kind": "mech", "pos": list(pos), "vel": [0.0, 0.0, 0.0],
        "aim": [3.14159, 0.0], "weapons": ["gun"], "cubes": 0,
        "hp": 1400.0, "max_hp": 1400.0, "charging": False, **fields,
    }


def observation(*weapons, **fields):
    self_obs = {
        "id": 0, "pos": [0.0, 0.0, 0.0], "vel": [0.0, 0.0, 0.0], "aim": [0.0, -0.03],
        "grounded": True, "in_zone": True, "energy": 100.0, "max_energy": 100.0,
        "hp": 1400.0, "max_hp": 1400.0, "cubes": 0, "upgrades": [],
        "stats": dict(CONFIG["mech"]), "weapons": list(weapons) + [None] * (2 - len(weapons)),
    }
    return {
        "type": "obs", "self": self_obs, "tick": 200, "t": 10.0, "alive": 16,
        "zone": {
            "center": [0.0, 0.0], "radius": 300.0, "next_center": [0.0, 0.0],
            "next_radius": 200.0, "shrink_start": 60.0, "shrink_end": 110.0, "dps": 20.0,
        },
        "events": [], "sounds": [], "beams": [], "mechs": [], "projectiles": [], "crates": [],
        "cubes": [], "pickups": [], "drops": [], "offer": None, **fields,
    }


class FlatWall:
    def ground(self, x, y):
        return 0.0

    def los(self, start, end):
        return max(start[0], end[0]) < 50.0 or min(start[0], end[0]) >= 50.0


class CombatControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = CombatController(CONFIG)

    def test_targets_with_actual_slew_and_second_tick_cooldown(self):
        obs = observation(weapon("gun", cooldown=0.08), mechs=[enemy(pos=(80.0, 30.0, 0.0))])
        result = self.controller.decide(obs)
        self.assertEqual(result.target_id, 1)
        self.assertEqual(result.fire, (True, False))
        # The first tick reaches 0.20 rad, well short of the 0.36-rad target.
        obs["self"]["weapons"][0]["cooldown"] = 0.0
        self.assertEqual(self.controller.decide(obs).fire, (False, False))

    def test_stays_on_navigation_plan_when_quiet(self):
        result = self.controller.decide(
            observation(weapon("gun", ammo=2)), move=(0.2, 0.3), boost=True,
            default_aim=(1.2, 0.05),
        )
        self.assertEqual(result.action_fields(), {
            "move": [0.2, 0.3], "boost": True, "aim": [1.2, 0.05],
            "fire": [False, False], "reload": True,
        })

    def test_hold_fire_faces_recent_attacker(self):
        obs = observation(weapon("gun"), mechs=[enemy()], events=[{
            "type": "damaged", "amount": 200, "weapon": "gun", "bearing": 3.0, "by": 2,
        }])
        result = self.controller.decide(obs, engage=False, allow_dodge=False, move=(-1.0, 0.0))
        self.assertEqual(result.fire, (False, False))
        self.assertEqual(result.move, (-1.0, 0.0))
        self.assertLess(result.risk.orientation_cost(result.aim[0]), result.risk.orientation_cost(0.0))
        obs.update(t=10.1, tick=202, events=[], mechs=[])
        self.assertEqual(self.controller.decide(obs, engage=False).aim, (3.0, 0.0))

    def test_uses_map_cover_and_explicit_loot_targets(self):
        obs = observation(weapon("gun"), mechs=[enemy()], crates=[{
            "id": 1001, "pos": [25.0, 0.0, 0.0], "hp": 300,
        }])
        controller = CombatController(CONFIG, arena=FlatWall())
        self.assertEqual(controller.decide(obs, allow_dodge=False).fire, (False, False))
        result = controller.decide(obs, target_ids=[1001], allow_dodge=False)
        self.assertEqual(result.target_id, 1001)
        self.assertEqual(result.fire, (True, False))

    def test_lightning_conserves_energy_and_respects_first_target(self):
        obs = observation(weapon("lightning"), mechs=[enemy(pos=(35.0, 0.0, 0.0))], crates=[{
            "id": 1001, "pos": [15.0, 0.0, 0.0], "hp": 300,
        }])
        self.assertEqual(self.controller.decide(obs).fire, (False, False))
        obs["crates"][0]["pos"][0] = 23.0
        self.assertEqual(self.controller.decide(obs).fire, (True, False))  # Arc jumps from crate to mech.
        obs["crates"].clear()
        self.assertEqual(self.controller.decide(obs).fire, (True, False))
        obs["self"]["energy"] = 1.0
        self.assertEqual(self.controller.decide(obs).fire, (False, False))

    def test_laser_hold_release_mobility_and_short_state(self):
        obs = observation(None, weapon("laser"), mechs=[enemy(weapons=["mortar"])])
        self.assertEqual(self.controller.decide(obs, boost=True).fire, (False, False))
        self.assertEqual(self.controller.decide(obs).fire, (False, True))
        laser = obs["self"]["weapons"][1]
        laser["charge"] = 0.2
        obs["tick"], obs["t"] = 204, 10.2
        self.assertEqual(self.controller.decide(obs).fire, (False, True))
        laser["charge"] = laser["stats"]["charge"]
        obs["tick"], obs["t"] = 216, 10.8
        result = self.controller.decide(obs)
        self.assertEqual(result.weapon, "laser")
        self.assertEqual(result.fire, (False, False))
        laser["charge"] = 0.0
        obs.update(tick=218, t=10.9, events=[{"type": "effect", "effect": "short", "by": 1}])
        self.assertEqual(self.controller.decide(obs).fire, (False, False))
        obs.update(tick=250, t=12.5, events=[])
        self.assertEqual(self.controller.decide(obs).fire, (False, False))
        obs.update(tick=260, t=13.0)
        self.assertEqual(self.controller.decide(obs).fire, (False, True))

    def test_charging_laser_holds_trigger_while_slewing(self):
        obs = observation(weapon("laser", charge=0.4), mechs=[enemy(pos=(60.0, 60.0, 0.0))])
        result = self.controller.decide(obs)
        self.assertGreater(result.aim[0], 0.7)
        self.assertEqual(result.fire, (True, False))
        obs["self"]["aim"] = list(result.aim)
        self.assertEqual(self.controller.decide(obs, boost=True).fire, (False, False))


if __name__ == "__main__":
    unittest.main()
