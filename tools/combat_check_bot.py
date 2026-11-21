"""Opt-in stdin/stdout adapter for conformance-testing the combat controller.

    python kit/check/check.py -- python -u tools/combat_check_bot.py
"""

from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from combat import CombatController
from geom import MapModel
import mechfight

PLANNED_MOVE = (0.6, 0.8)


class CombatCheckBot:
    name = "combat-codex-check"

    def __init__(self, hello):
        config = hello["config"]
        map_info = hello["map"]
        try:
            map_dir = Path(map_info["dir"])
            # A Windows checker turns --map-dir /arena into a drive-qualified path.
            if not map_dir.is_dir() and Path("/arena").is_dir():
                map_dir = Path("/arena")
            arena = MapModel(str(map_dir), map_info["files"], config["mech"])
            self.controller = CombatController(config, arena=arena)
        except Exception:
            # The live wrapper normally keeps serving fallback actions after exceptions.
            raise SystemExit(traceback.format_exc()) from None

    def act(self, obs):
        try:
            decision = self.controller.decide(obs, move=PLANNED_MOVE, engage=True)
            return decision.action_fields()
        except Exception:
            raise SystemExit(traceback.format_exc()) from None


if __name__ == "__main__":
    mechfight.run(CombatCheckBot)
