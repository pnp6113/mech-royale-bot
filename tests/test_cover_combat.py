"""Deterministic cover regressions. Run with python -m unittest discover -s tests."""

import copy
import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import brain
from cover_engagement import CoverEngagement, CoverOrder
from cover_geometry import CoverPair, CoverSearch
from geom import MapModel
from kit.check.check import Schema


ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "kit"
with gzip.open(KIT / "check" / "stream.jsonl.gz", "rt", encoding="utf-8") as stream:
    HELLO = json.loads(next(stream))
    TEMPLATE = next(message for message in map(json.loads, stream) if message["type"] == "obs")
HELLO["map"]["dir"] = str(KIT / "arena")
CONFIG = HELLO["config"]


def actor(actor_id, position, **extra):
    return {
        "id": actor_id, "kind": "mech", "pos": list(position), "vel": [0.0, 0.0, 0.0],
        "aim": [0.0, 0.0], "hp": CONFIG["mech"]["hp"], "max_hp": CONFIG["mech"]["hp"],
        "cubes": 0, "weapons": ["gun"], "charging": False,
    } | extra


def observation(position=(0.0, 0.0, 0.0), *, t=30.0, enemies=(), hp_fraction=1.0,
                ammo=None, reload=0.0, cooldown=0.0, zone=None, **self_overrides):
    obs = copy.deepcopy(TEMPLATE)
    obs.update(t=t, tick=round(t * CONFIG["match"]["tick_hz"]), mechs=list(enemies), projectiles=[],
               crates=[], cubes=[], pickups=[], sounds=[], beams=[], events=[], drops=[], offer=None)
    gun = CONFIG["weapons"]["gun"]
    obs["self"].update(pos=list(position), vel=[0.0, 0.0, 0.0], aim=[0.0, 0.0], grounded=True,
                       in_zone=True, cubes=0, hp=hp_fraction * CONFIG["mech"]["hp"],
                       weapons=[{"kind": "gun", "cooldown": cooldown,
                                 "ammo": gun["mag"] if ammo is None else ammo,
                                 "reload": reload, "stats": gun.copy()}, None])
    obs["self"].update(self_overrides)
    x, y, _ = position
    obs["zone"] = zone or {
        "center": [x, y], "radius": 100.0, "next_center": [x, y], "next_radius": 80.0,
        "shrink_start": 60.0, "shrink_end": 110.0, "dps": 20.0,
    }
    return obs


class CoverGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.arena = MapModel(HELLO["map"]["dir"], HELLO["map"]["files"], CONFIG["mech"])
        cls.search = CoverSearch(cls.arena, CONFIG)
        cls.schema = Schema(KIT / "mechfight.schema.json")

    def point(self, x, y):
        return (x, y, self.arena.ground(x, y))

    def setUp(self):
        # The solid at Women's Field House blocks fire from the southwest. The
        # northward route around that corner stays on walkable terrain.
        self.hold = self.point(-373.3, -251.2)
        self.peek = self.point(-373.3, -237.2)
        self.enemy = actor(5, self.point(-429.3, -270.2))
        self.second = actor(7, self.point(-437.3, -271.2))

    def test_campus_corner_has_body_cover_and_bidirectional_peek(self):
        self.assertTrue(self.search.hidden(self.hold, self.enemy))
        self.assertFalse(self.search.can_fire(self.hold, self.enemy))
        self.assertTrue(self.search.can_fire(self.peek, self.enemy))
        self.assertTrue(self.search.path_clear(self.hold, self.peek))
        self.assertTrue(self.search.path_clear(self.peek, self.hold))

    def test_search_returns_reachable_pair_and_accounts_for_both_attackers(self):
        obs = observation(self.hold, enemies=(self.enemy, self.second))
        pair = self.search.find(obs, self.enemy, threats=(self.enemy, self.second))
        self.assertIsNotNone(pair)
        self.assertEqual(pair.target_id, self.enemy["id"])
        self.assertTrue(self.search.path_clear(self.hold, pair.hold))
        self.assertTrue(self.search.path_clear(pair.hold, pair.peek))
        self.assertTrue(self.search.path_clear(pair.peek, pair.hold))
        self.assertTrue(self.search.hidden(pair.hold, self.enemy))
        self.assertTrue(self.search.hidden(pair.hold, self.second))
        self.assertTrue(self.search.can_fire(pair.peek, self.enemy))
        self.assertEqual(set(pair.protected_threats), {self.enemy["id"], self.second["id"]})

    def test_zone_constriction_and_rejected_route_cannot_supply_cover(self):
        obs = observation(self.hold, enemies=(self.enemy,))
        self.assertIsNone(self.search.find(obs, self.enemy, path_safe=lambda start, end: False))
        obs["zone"].update(radius=2.0, next_radius=1.0, shrink_start=30.0, shrink_end=31.0)
        self.assertIsNone(self.search.find(obs, self.enemy))

    def test_enabled_bot_yields_cover_to_weapon_and_cube_pickups(self):
        with patch.dict(brain.TUNE, cover_peek=True):
            with patch.object(brain.geom, "MapModel", return_value=self.arena):
                bot = brain.Bot(copy.deepcopy(HELLO))
                cube_bot = brain.Bot(copy.deepcopy(HELLO))

            combat = observation(self.hold, enemies=(self.enemy,))
            bot.act(combat)
            self.assertEqual(bot.mode, "fight")
            self.assertIsNotNone(bot.cover_phase)

            weapon = observation(self.hold, t=30.1, enemies=(self.enemy,))
            nearby = self.point(self.hold[0], self.hold[1] + 2.0)
            weapon["pickups"].append({"id": 8001, "kind": "laser", "pos": list(nearby)})
            action = {"tick": weapon["tick"], **bot.act(weapon)}
            self.assertIsNone(bot.cover_phase)
            self.assertEqual(action.get("take"), 8001)
            self.assertEqual(self.schema.errors(action, "Action"), [])

            cube_bot.act(combat)
            self.assertIsNotNone(cube_bot.cover_phase)
            cube = observation(self.hold, t=30.1, enemies=(self.enemy,))
            cube["cubes"].append({"id": 5000, "pos": list(nearby)})
            action = {"tick": cube["tick"], **cube_bot.act(cube)}
            self.assertIsNone(cube_bot.cover_phase)
            self.assertGreater(action["move"][1], 0.0)
            self.assertEqual(self.schema.errors(action, "Action"), [])


class CoverFallbackTests(unittest.TestCase):
    def test_cover_peeking_is_disabled_by_default(self):
        self.assertIs(brain.TUNE["cover_peek"], False)

    def test_enabling_without_geometry_preserves_v6_action(self):
        obs = observation(enemies=(actor(5, (40.0, 0.0, 0.0)),))
        with patch.object(brain, "geom", None):
            with patch.dict(brain.TUNE, cover_peek=False):
                baseline = brain.Bot(copy.deepcopy(HELLO)).act(copy.deepcopy(obs))
            with patch.dict(brain.TUNE, cover_peek=True):
                opted_in = brain.Bot(copy.deepcopy(HELLO)).act(copy.deepcopy(obs))
        self.assertEqual(opted_in, baseline)

    def test_wire_action_uses_cover_only_when_v6_allows_it(self):
        class HoldRecorder:
            def __init__(self):
                self.eligibility = []

            def decide(self, obs, target, *, threats, eligible):
                self.eligibility.append(eligible)
                if eligible:
                    return CoverOrder((0.0, 0.0), "hold", reload=True, gun_clear=False)
                return None

        target = actor(5, (40.0, 0.0, 0.0))
        with patch.object(brain, "geom", None):
            bot = brain.Bot(copy.deepcopy(HELLO))
        recorder = HoldRecorder()
        bot.cover_engagement = recorder
        hold = observation(enemies=(target,), ammo=3, aim=[0.0, -0.0624])
        action = bot.act(hold)
        self.assertEqual(bot.cover_phase, "hold")
        self.assertEqual(action["move"], [0.0, 0.0])
        self.assertFalse(action["fire"][0])
        self.assertTrue(action["reload"])

        rotate = observation(t=31.0, enemies=(target,), in_zone=False)
        rotate["zone"].update(center=[-35.0, 0.0], radius=30.0,
                              next_center=[-35.0, 0.0], next_radius=25.0)
        action = bot.act(rotate)
        self.assertIsNone(bot.cover_phase)
        self.assertLess(action["move"][0], 0.0)

        grab = observation(t=32.0, enemies=(target,))
        grab["cubes"].append({"id": 5000, "pos": [-2.0, 0.0, 0.0]})
        bot.act(grab)
        self.assertIsNone(bot.cover_phase)

        bot.stuck_until = 34.0
        bot.stuck_dir = (0.0, -1.0)
        action = bot.act(observation(t=33.0, enemies=(target,)))
        self.assertIsNone(bot.cover_phase)
        self.assertTrue(action["jump"])
        self.assertEqual(recorder.eligibility, [True, False, False, False])


class FixedCover:
    """A deterministic corner with an occluded hold and one clear firing lane."""

    hold = (0.0, 0.0, 0.0)
    peek = (0.0, 8.0, 0.0)

    def __init__(self):
        self.find_calls = []
        self.lane_open = True
        self.lane_y = 7.0
        self.in_zone = True
        self.crossfire = set()

    def find(self, obs, target, *, threats=(), exclude_hold=None, **weapon):
        self.find_calls.append((target["id"], exclude_hold))
        if exclude_hold == self.hold:
            return None
        return CoverPair(self.hold, self.peek, target["id"], 1.0, (target["id"],))

    def hidden(self, point, threat, **dimensions):
        return point[1] <= 1.0 or (threat["id"] != 5 and threat["id"] not in self.crossfire)

    def can_fire(self, point, target, **weapon):
        return self.lane_open and point[1] >= self.lane_y

    def zone_safe(self, point, obs, *, seconds):
        return self.in_zone

    def path_clear(self, start, end, **dimensions):
        return True


class CoverEngagementTests(unittest.TestCase):
    def setUp(self):
        self.target = actor(5, (40.0, 8.0, 0.0))
        self.search = FixedCover()
        self.policy = CoverEngagement(self.search, CONFIG)

    def order(self, t, y, *, target=None, threats=(), eligible=True, damage=0, **options):
        target = self.target if target is None else target
        obs = observation((0.0, y, 0.0), t=t, enemies=(target, *threats), **options)
        if damage:
            obs["events"].append({"type": "damaged", "amount": damage, "weapon": "gun",
                                  "bearing": 0.0, "by": target["id"]})
        return self.policy.decide(obs, target, threats=threats, eligible=eligible)

    def test_retracts_and_reloads_from_hidden_hold(self):
        order = self.order(30.0, 0.0)
        self.assertEqual(order.phase, "hold")
        self.assertFalse(order.gun_clear)
        self.assertEqual(self.order(30.4, 0.0).phase, "peek")
        self.assertEqual(self.order(30.8, 8.0).phase, "fire")
        order = self.order(30.9, 8.0, damage=100, ammo=2)
        self.assertEqual(order.phase, "retract")
        self.assertLess(order.move[1], 0.0)
        self.assertFalse(order.reload)
        hidden = self.order(31.4, 0.0, ammo=2)
        self.assertEqual(hidden.phase, "hold")
        self.assertFalse(hidden.gun_clear)
        self.assertTrue(hidden.reload)
        loading = self.order(31.8, 0.0, ammo=2, reload=2.0)
        self.assertEqual(loading.phase, "hold")
        self.assertFalse(loading.reload)
        self.assertEqual(self.order(34.0, 0.0).phase, "peek")

    def test_peek_brakes_and_fires_only_after_a_clear_lane(self):
        self.assertEqual(self.order(30.0, 0.0).phase, "hold")
        self.assertEqual(self.order(30.2, 0.0).phase, "hold")
        self.assertEqual(self.order(30.4, 0.0).phase, "peek")
        close = self.order(30.6, 6.5)
        self.assertEqual(close.phase, "peek")
        self.assertFalse(close.gun_clear)
        self.assertGreater(close.move[1], 0.0)
        self.assertLess(close.move[1], 1.0)
        attack = self.order(30.7, 8.0)
        self.assertEqual(attack.phase, "fire")
        self.assertEqual(attack.move, (0.0, 0.0))
        self.assertTrue(attack.gun_clear)
        stop = self.order(30.8, 8.0, ammo=CONFIG["weapons"]["gun"]["mag"] - 2)
        self.assertEqual(stop.phase, "retract")
        self.assertLess(stop.move[1], 0.0)
        self.assertLess(self.order(31.0, 7.0).move[1], 0.0)
        self.assertEqual(len(self.search.find_calls), 1)

    def test_loaded_gun_keeps_a_clear_lane_to_a_nearby_peek(self):
        self.search.lane_y = 6.0
        loaded = self.order(30.0, 6.5)
        self.assertEqual(loaded.phase, "peek")
        self.assertTrue(loaded.gun_clear)
        self.assertGreater(loaded.move[1], 0.0)

        self.policy = CoverEngagement(self.search, CONFIG)
        nearly_empty = self.order(31.0, 6.5, ammo=2)
        self.assertEqual(nearly_empty.phase, "take_cover")
        self.assertLess(nearly_empty.move[1], 0.0)

    def test_cover_plan_yields_to_crossfire_and_zone(self):
        threat = actor(7, (35.0, -10.0, 0.0))
        self.assertEqual(self.order(30.0, 0.0, threats=(threat,)).phase, "hold")
        self.search.crossfire.add(threat["id"])
        self.assertIsNone(self.order(30.3, 0.0, threats=(threat,)))
        self.assertIsNone(self.order(30.4, 0.0, eligible=False))
        self.search.crossfire.clear()
        self.search.in_zone = False
        self.assertIsNone(self.order(35.0, 0.0, threats=(threat,)))

    def test_stalled_pair_is_not_retried_immediately(self):
        first = self.order(30.0, -6.0)
        self.assertEqual(first.phase, "take_cover")
        self.assertGreater(first.move[1], 0.0)
        self.assertEqual(self.order(30.5, -6.0).phase, "take_cover")
        self.assertIsNone(self.order(30.9, -6.0))
        self.assertIsNone(self.order(31.2, -6.0))
        self.assertEqual(len(self.search.find_calls), 1)
        self.assertIsNone(self.order(32.1, -6.0))
        self.assertEqual(self.search.find_calls[-1], (self.target["id"], self.search.hold))

    def test_target_change_does_not_flip_the_active_movement_on_next_turn(self):
        replacement = actor(6, (-40.0, 8.0, 0.0))
        self.assertEqual(self.order(30.0, -6.0).phase, "take_cover")
        self.assertIsNone(self.order(30.1, -6.0, target=replacement))
        self.assertIsNone(self.order(30.2, -6.0))
        self.assertEqual(len(self.search.find_calls), 1)
        self.assertEqual(self.order(30.6, -6.0, target=replacement).phase, "take_cover")
        self.assertEqual(self.search.find_calls[-1][0], replacement["id"])


if __name__ == "__main__":
    unittest.main()
