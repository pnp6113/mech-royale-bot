# Combat controller integration

`combat.py`, `combat_targeting.py`, and `combat_defense.py` form the opt-in controller. They use the standard library and the existing `MapModel`. The public interface takes the decoded `mechfight/1` observation and returns ordinary action fields.

## Wire into `brain.Bot`

From Claude's feature branch, bring in the combat commit. The remote command assumes the feature branch is available on `origin`. From the same machine, use `git fetch D:/Hackathon/mech-royale-combat-codex feat/combat-codex` as the local source.

```sh
git fetch origin feat/combat-codex
git cherry-pick FETCH_HEAD
```

Import the controller and construct it after map initialization in `Bot.__init__`. Its state tracks laser charge, the lightning short interval, and recent damage direction for one match.

```python
from combat import CombatController

# In Bot.__init__, after self.map has been initialized:
self.combat = CombatController(self.cfg, arena=self.map)
```

In `Bot.act`, calculate movement, boost, jump, and navigation mode as usual. Route projectile evasion through a single layer by removing the call to the previous `self.dodge` and its movement and boost adjustments when enabling this controller. Supply the final navigation intent after wall avoidance and stuck handling. Replace the old `aim_and_fire` and reload block with this call. In this example, rotation or retreat takes priority over returning fire, and loot fire requires an explicit observed target.

```python
scan_yaw = math.atan2(move[1], move[0]) if any(move) else me["aim"][0]
loot_ids = (loot_target[1]["id"],) if mode == "loot" and loot_target else None
decision = self.combat.decide(
    obs,
    move=move,
    boost=boost,
    jump=jump,
    engage=mode == "fight" or loot_ids is not None,
    target_ids=loot_ids,
    default_aim=(scan_yaw, 0.0),
    allow_dodge=not jump and t >= self.stuck_until,
)
act.update(decision.action_fields())
act["jump"] = jump
```

Keep the existing pickup, swap, upgrade, debug, and wrapper logic. The wrapper echoes `tick`. If navigation has a local clearance query, supply `path_safe(start_xyz, end_xyz) -> bool` to veto unsafe dodge candidates. The built-in ground check uses the repository's walkability grid. A caller that owns an airborne maneuver can pass `allow_dodge=False`; defensive aim and fire selection still run.

`target_ids=None` permits engagement of any observed enemy mech. An explicit iterable restricts fire to the matching observed mechs, NPCs, or crates. `engage=False` gives control of torso aim to defense and suppresses new shots. An already charging laser can release when its trigger is dropped, per the game protocol. `default_aim` is used when defense has no directional threat and offense has no selected aim. The decision's `target_id`, `weapon`, `risk`, and `dodge` fields can feed debug output or a higher-level policy. Use `action_fields()` for the wire, which contains only `move`, `boost`, `aim`, `fire`, and `reload`.

## Decision model

- The gun solves a 3D constant-velocity intercept from the forward-offset muzzle. Trigger gating checks the reachable torso aim and target cylinder at the first eligible simulation tick. Static cover checks use terrain and building prisms.
- The laser separates charging from release and estimates damage at the proposed release time, including cook-off against mortar carriers. It tracks the observed short effect and favors maintaining movement during evasive or zone-critical decisions.
- The mortar solves low and high intercept arcs with the live shell speed, gravity, and lifetime, samples the arc for cover, and rejects close impacts that could hurt the shooter. It plans direct contacts, with confidence falling as the target's projected movement becomes less reliable.
- Lightning checks the reachable horizontal cone, range, energy, and line of sight. It evaluates the nearest eligible primary across observed enemies, NPCs, and crates, then follows eligible chain jumps and their damage falloff.
- Defense checks projected paths against incoming projectiles and charging lasers. It tests alternatives under the movement acceleration limits and spends boost when it materially reduces exposure. Facing uses incoming threat directions and the configured front arc. Weapon selection discounts options that expose the rear or slow movement during a dangerous rotation.

The official protocol specifies the simulation tick and action window. The controller models the torso at the first eligible tick of that window and uses the arena's rasterized line and walkability queries. The server resolves exact shot order, projectiles, contacts, and geometry. Enemy movement prediction, hit confidence, and estimated damage are heuristics. To bound decision work, the controller evaluates up to eight intended targets and simulates up to 32 incoming bullets and eight shells per observation. Obstructions and opponents are limited to the provided map and observations. Pass `arena=None` for geometry-only tests.

## Local verification

```sh
python -m unittest discover -s tests -p "test_combat*.py" -v
python tools/combat_benchmark.py
python kit/check/check.py -- python -u tools/combat_check_bot.py
```

Run the adapter with the match resource limits from the repository root. The Dockerfile excludes local tooling, so mount the adapter and arena read-only.

```sh
docker build --platform linux/amd64 -t mech-combat-codex .
python kit/check/check.py --map-dir /arena -- \
  docker run --rm -i --platform linux/amd64 --network none --cpus 1 \
    --memory 512m --memory-swap 512m --pids-limit 128 --cap-drop ALL \
    --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 \
    -v "$PWD/kit/arena:/arena:ro" -v "$PWD/tools:/bot/tools:ro" \
    --entrypoint python3 mech-combat-codex -u /bot/tools/combat_check_bot.py
```

The benchmark reports controller-only latency for the recorded kit stream and deterministic dense observations. The separate conformance adapter checks process-level protocol and response time. Both use fixed input observations. Placement impact needs live matches with the integrated bot.

The wire contract is in `kit/mechfight.schema.json`. [Official mechfight/1 protocol](https://www.joinbuilders.org/buildathon/tracks/builders/files/protocol.md?inline=1)
