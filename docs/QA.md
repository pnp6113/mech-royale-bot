# QA handoff: Last Mech Standing

Branch `qa/cursor` in `D:\Hackathon\mech-royale-cursor-qa`. This branch is not pushed. It contains public `main` at `f9a3669` plus the reliability fixes below. Do not merge it to `main` from this worktree. Claude integrates.

Functional commits:

- `94f99ad` — fixes and the new tests, based on `e0be091`
- merge of `origin/main` `f9a3669` (v3 smart targets, cover retreat, regen hold, weapon rush, experiment log)
- the commit that adds this file is the branch tip (`git rev-parse HEAD`)

No practice score is claimed. There is no official local match simulator. `sim/` is the team's approximate house-bot model. Only a Buildathon submission can score the bot.

## 1. Issues

### Critical, fixed

| Issue | What went wrong | Test |
| --- | --- | --- |
| Dodge sign | A bullet that would pass to the north produced `move [0, 1]`. The bot stepped into the impact point. `(cx, cy)` is the bullet's position relative to us at closest approach; the step has to go the other way. | `dodges_away_from_bullet_passing_north` |
| Crossfire cancel | One bullet north and one south summed to `move [0, 0]`. The bot stood in the middle, inside both hit radii (2.5 m). | `opposing_gunfire_does_not_cancel` |
| Close goal ignored geometry | Any goal inside 6 m was a straight line, including the centre of a blocked nav cell and the top of a cliff. Measured direction into a wall was `(0.667, 0)`. | `close_goal_does_not_walk_into_a_wall`, `straight_line_does_not_climb_a_cliff` |
| Weapon swap | With gun + mortar, a lightning pickup in range returned no `take`. Gun is the lowest `WEAPON_VALUE`, the old code selected that slot, then refused to drop the gun, and never considered the other slot. | `swaps_the_weaker_weapon_and_keeps_the_gun` |
| Lightning target | Lightning hits the nearest mech, NPC, or crate in the cone (protocol §7). The bot fired whenever any mech was in the cone, so a crate at 12 m stole a shot meant for a mech at 36 m. | `lightning_does_not_waste_the_shot_on_a_closer_crate` |

### High, fixed

| Issue | What went wrong | Test |
| --- | --- | --- |
| Impossible mortar arc | A high arc at ~40 m is about 1.49 rad. `max_pitch` is 1.3. The bot replaced the gun aim with that solution and clamped it, so the shell could not fly the solved arc. | `mortar_does_not_commit_to_an_unreachable_pitch` |
| Stale path | `Planner.start` left the previous polyline in place while searching a goal more than 12 m away. | `planner_drops_a_stale_path_when_the_goal_jumps` |
| Stuck recovery | The stuck heading replaced `avoid_walls`, so recovery could point into the building that stopped us. | `stuck_recovery_does_not_push_into_the_wall` |
| Slopes | `walk_clear` only tested the blocked bit. The map has 226 adjacent free nav cells with more than `max_rise` of uphill, up to 27.8 m over 4 m. A* already refused those edges. Direct steering did not. | `straight_line_does_not_climb_a_cliff` |
| Pickup memory | A pickup inside 30 m was forgotten as soon as it left the observation, including when a building blocked line of sight. Inside 45 m the server lists it whenever we can see it (protocol §8), so a hidden one is still there. | `hidden_pickup_stays_in_memory` |
| Laser behind cover | `charging: true` with aim on us set `boost` even when the beam could not reach us. | `charging_laser_behind_a_building_does_not_burn_boost` |
| Short velocity | A projectile `vel` of length 2 raised inside `dodge` and the wrapper coasted. | `short_projectile_vector_does_not_crash` |

### Medium, fixed

| Issue | What went wrong | Test |
| --- | --- | --- |
| Vertical lead | `intercept` applied the full flight time on x/y and `min(tt, 0.5)` on z. A target at 200 m rising at 10 m/s was led to z = 10 instead of about 13.4. | `intercept_leads_a_rising_target` |
| Early jet | A hop waypoint within 26 m set jump + boost immediately. A 3.5 m ledge at 22 m started a jet (`hop_req` height 7.41 m). Runway is now `min(24, climb * 1.25 + 3)` metres from the obstacle. Jet rise is capped at `max_rise_speed` 10 m/s in `hello.config.mech`. | `short_ledge_does_not_start_a_jet_from_across_the_street` |
| Planner spike | Giving A* 8 ms per decision, after the slope check started more searches, moved recorded-stream p99 from 1.25 ms to 9.57 ms (max 14.57 ms). The per-tick budget is now 2.5 ms and the clock is checked every 16 expansions. p99 went back to 2.73 ms. | `NOTRACE=1 python tools/replay.py` |

`find_cover` (from `f9a3669`) skips a candidate that climbs more than `max_rise`. Those points are free in the nav grid and are not walkable.

### High, not changed

**Roofs are not standable in the nav grid.** A building cell is `blocked` from the ground up to the roof. A* may hop a wall under 22 m tall and under 20 m thick, and the landing is the first free cell on the far side. It cannot stop on the roof. Protocol §7 and §11: about a quarter of crates are on roofs, and a mech can jump and jet onto a roof. Fixing this means a walk-height per cell and a `walk_clear` that understands height. That is a new navigation model, not a local patch, and it collides with the survival work on `geom.py`. Left alone on purpose.

**Cliffs taller than 22 m are impassable to the planner.** `hop_max` is 22 m. Full energy climbs about 60 m (protocol §7). The sampled map has rises of about 28 m between free cells. The bot now stops at the lip instead of walking into it. It still cannot cross.

### Medium, not changed

- Gun intercept starts at the muzzle point on the mech's axis. Protocol §5: the shot leaves `radius + 0.5` = 3.0 m along the actual aim. The missing offset is a fraction of a target radius inside 100 m and grows as a time error at long range. `spread_deg` 0.6 is also not in the fire gate (about 2.1 m of sigma at 200 m). Both belong to the combat module.
- Mortar landing prediction samples gravity until terrain. It does not stop the shell on a building, so a dodge point can be past a wall the shell will hit.
- `los` samples every 2 m and ignores trees. A probe through the centre of 13 thin `wall2` cells leaked 0 times. Trees blocking vision and not shots matches protocol §7 for firing. Vision is already decided by the server before an actor appears in `obs.mechs`.
- Fight movement strafes in the open. Cover is used on retreat (`TUNE["cover"]`), not while fighting. That is a strategy gap, and the new retreat test passes.
- Realistic mortar self-splash was checked and did not fail. At 22.5 m with the target closing at 19.5 m/s the solved impact was 17.3 m out. Splash is 7 m; splash + 4 m is 11 m. The existing `dh > 22` gate held.

### Low

- `hello` has no practice or finals field. `Bot` has no such flag. Adding `match_mode` to an observation did not change move, aim, or fire (`protocol_has_no_practice_or_finals_flag`). `sim/house.py` labels itself a guess. Do not treat a detector as an official input.
- `gc.freeze()` after map load keeps the grid out of cyclic GC. Peak working set after load + 300 decisions was 29.6 MB. The sandbox allows 512 MB.
- Every action carries a debug label. The conformance check reported 300 drawings and 0 over the caps.

## 2. How to reproduce a failure

On `e0be091`, before `94f99ad`:

```powershell
cd D:\Hackathon\mech-royale-cursor-qa
git stash
git checkout e0be091
python tests/test_scenarios.py
```

The nine tests named in the critical and high tables failed there (the cliff assertion had to use a 3 m step along the unit heading; a shortened vector of length `dist/6` stopped on the cell boundary and hid the cliff). `git checkout qa/cursor` and the same command is 41/41 passed.

Individual cases, all through `Bot.act` or the named helper, with the map in `kit/arena`:

- Bullet dodge: gun projectile at `(OX+40, OY+1.5)` with velocity `(-252, 0, 0)`.
- Wall: `steer_to` from a free nav-cell centre into the blocked neighbour 4 m east.
- Cliff: adjacent free cells near `(-360, 97)` with ~28 m of rise. `steer_to` toward the high cell.
- Swap: weapons `[gun, mortar]`, lightning pickup 1.2 m away. Expect `take` and `drop: 1`.
- Lightning: crate at 12 m, mech at 36 m, same yaw, lightning in slot 1, aim already on them. Expect `fire[1]` false.
- Mortar: high arc at 40 m (`mortar_solution` pitch ~1.49 > 1.3) with line of sight forced false. Expect the aim to stay on the gun solution.

`OX, OY` is an open cell chosen by `tests/test_scenarios.py` (264, -152 on this map).

## 3. Measurements

Recorded stream `kit/check/stream.jsonl.gz`, 300 observations. In-process `Bot.act`, `NOTRACE=1 python tools/replay.py`, after the merge:

| | |
| --- | --- |
| Map load / init | 1.49 s (other quiet runs 0.6–2.1 s; one busy run 3.6 s) |
| Schema errors | 0 |
| p50 / p95 / p99 / max | 0.24 / 0.41 / 2.73 / 2.91 ms |
| Mean | 0.30 ms |
| Modes on that stream | loot 131, fight 97, retreat 72 (same counts as before the fixes) |

Same bot before the fixes, same command, on `e0be091`: p50 0.07, p95 0.22, p99 1.25, max 2.65 ms, init 0.55 s. The extra time is `walk_clear` and short A* slices. p99 is under the 15 ms target. The hard limit is 50 ms.

Profile of those 300 calls (cumulative): `steer_to` 0.144 s, `lookahead` 0.089 s, `walk_clear` 0.077 s, `Planner.step` 0.051 s across 24 searches, `los` 0.034 s, `aim_and_fire` 0.020 s, `choose_loot` 0.018 s. Total `act` 0.339 s. Nothing else is worth optimising on this stream.

Fuzz, 40 mutated real observations plus one frame with 48 projectiles and 12 mechs (`python tests/test_fuzz.py`), quiet machine: p50 0.80, p95 2.64, max 2.71 ms. 41/41 schema-valid, move length ≤ 1, no exceptions. A later run overlapped the host conformance check and peaked at 6.04 ms. Still under 20 ms.

Peak working set after map load and 300 decisions: **29.6 MB** (Windows `GetProcessMemoryInfo`). Limit 512 MB.

Navigation audit (`python tools/nav_audit.py --n 500`), planner resumed at the bot's 2.5 ms tick budget, goals within 120 m:

| | |
| --- | --- |
| Paths checked | 482 |
| Unfinished after 16 ticks | 18 |
| Waypoints on a blocked cell | 0 |
| Non-hop steps steeper than `max_rise * 1.5` | 0 |
| Search time p50 / p95 / max | 1.63 / 30.75 / 78.27 ms total, not per tick |

Official conformance, host process (`python kit/check/check.py -- python -u main.py`): **PASS**. ready 2136 ms, 300/300 in time, 0 late, 0 wrong tick, 0 bad lines, 300 debug drawings, 0 over the caps. That run shared the CPU with the fuzzer, so its p99 of 11.4 ms and max of 25.1 ms are a loaded-machine figure. The checker prints that those times are wall time on this machine.

Docker sandbox, image `mech-royale-qa` (not the `mech-bot` tag, so a local tag used by another clone was left alone):

```text
docker run --rm -i --network none --cpus 1 --memory 512m --read-only
  --tmpfs /tmp:rw,size=64m --user 65534:65534 -v kit/arena:/arena:ro
```

**PASS.** Stderr: `map loaded from /arena in 1.01 s`. ready 2266 ms (container start plus map; hello budget is 20 s). 300/300 in time, 0 late, p50 3.5, p90 4.3, p99 6.8, max 8.5 ms. One answer to an older queued observation was dropped, which is the newest-observation rule, and the check still passed.

`tests/test_scenarios.py`: **41/41 passed** after the merge, including `hurt_and_alone_holds_fire_to_regen` and `retreat_goal_breaks_line_of_fire` from `f9a3669`.

## 4. What to take

Take the whole `qa/cursor` diff. The behaviour changes that matter in a match:

1. Do not walk a straight line into a blocked cell or up a slope steeper than `max_slope_deg`. If the planner has no path yet, move only when the next 4 m is `walk_clear`, otherwise stop.
2. Step away from a bullet's closest approach. If two dodges cancel, commit to the stronger one.
3. When both slots are full, swap the worse non-gun weapon if the pickup is better by more than 0.3. Keep the gun when the other slot can be dropped. Never drop a charging laser.
4. Fire lightning only when the nearest in-cone, in-range, visible target is a mech or NPC, or a crate when there is no fight target and energy is above 40.
5. Do not aim a mortar whose solved pitch lies outside `[min_pitch, max_pitch]`.
6. Lead vertical velocity for the same flight time as horizontal velocity.
7. Start a hop only when the obstacle is inside a runway scaled to the climb.
8. Drop a path when the goal jumps by more than 12 m. Forget a nearby pickup only when line of sight says we should have seen it, or after 90 s.
9. Run `avoid_walls` on the stuck heading, and reject a heading that climbs more than `max_rise`.
10. Do not boost to dodge a charging laser with no line of sight.
11. Keep A* under 2.5 ms per decision.

Reload of an empty gun now happens even if a target is visible. A gun with 1–3 rounds still finishes the burst inside 150 m. That matches "an empty slot does not fire" (protocol §7).

## 5. Tests added

| File | What it covers |
| --- | --- |
| `tests/test_scenarios.py` | The 16 situations in the brief: no enemies, several attackers, charging laser, gunfire, mortar splash, low HP, low energy, zone exit, blocked nav, stuck-against-a-wall, pickup and swap, upgrades including an unknown id, 400-tick memory bound, low ammo, missing optional fields, final circle at radius 0, and a fake `match_mode` field. Plus the combat-math and planner cases above. |
| `tests/test_fuzz.py` | 40 deterministic mutations of a real observation (optional keys dropped, null weapon slot, empty lists, null zone bearing, unknown upgrade id, map-edge position, unknown field) and one heavy projectile frame. |
| `tools/nav_audit.py` | 500 nearby start/goal pairs on `kit/arena`. |

Existing tests from `main` still run in the same file.

## 6. Practice and finals

Runtime mode detection is not implemented, and it should not be added as if `hello` contained it. Protocol §6 lists `hello`, `obs`, and `end`. None of them name the opponent set. The same observation with an extra `match_mode: "finals"` produced the same move, aim, and fire from two bots with the same seed.

`docs/EXPERIMENTS.md` (now on this branch) records practice as 48 matches against a squad that hunts us, and records local `sim/` comparisons. Those sim numbers are the team's model. They are not a Buildathon score. v2 on the public log is 3.77 (receipt `BB-3E2C5FC8`). v3/v4 in that log are local sim rows, not submissions.

The fixes in section 4 help a squad hunt and a free-for-all for the same reason: an illegal or self-destructive action wastes the tick in both. Nothing here turns the bot passive in practice or reckless in a final. Weapon rush, regen hold, smart target scores, and retreat-to-cover came from `f9a3669` and still pass their tests with these fixes applied.

## 7. Integration

From the lead clone, with no uncommitted edits to `brain.py`:

```powershell
git remote add qa D:\Hackathon\mech-royale-cursor-qa
git fetch qa qa/cursor
git checkout -b integrate/qa-cursor origin/main
git merge qa/qa/cursor
python tests/test_scenarios.py
$env:NOTRACE='1'; python tools/replay.py
.\scripts\check.ps1
```

If `origin/main` has moved past `f9a3669`, merge `qa/cursor` and keep both sides. The conflict sites are `Bot.steer_to` / `Bot.find_cover` and the bottom of `tests/test_scenarios.py`. This branch already resolved those against `f9a3669`.

Gates this branch cleared on 21 Nov 2026:

- `python tests/test_scenarios.py` — 41/41
- `python tests/test_fuzz.py` — 41/41
- `python tools/nav_audit.py --n 500` — 0 blocked, 0 illegal slopes
- `NOTRACE=1 python tools/replay.py` — 0 schema errors, p99 2.73 ms
- local `check.py` — PASS
- Docker sandbox `mech-royale-qa` — PASS, `map loaded from /arena`

Do not submit this SHA until the lead has merged it and the image tag in `scripts/check.ps1` (`mech-bot`) has been built from that merge. This QA run used `mech-royale-qa` so it would not replace another clone's image.

## 8. Codex combat overlap

There is no `combat.py` on `origin/main` at `f9a3669`. `docs/EXPERIMENTS.md` says a Codex combat drop was slower in `sim/` and did not raise the local score. If that module still lands, it will collide with these edits:

| Function | QA change | What to keep if both touch it |
| --- | --- | --- |
| `Bot.dodge` | Away-from-impact sign, cancel fallback, ignore a charging laser with no LOS, ignore short vectors | The sign and the LOS check. A new predictor can replace the ballistics if it still steps off the closest point. |
| `Bot.intercept` | z uses the same `tt` as x/y | Any new solver should lead z for the full time of flight. |
| `Bot.aim_and_fire` | Mortar pitch must survive clamping. Lightning uses the nearest cone target. | Do not assign `aim = mortar_solution` when the pitch is outside `min_pitch`/`max_pitch`. Do not fire lightning because a farther mech is in the cone. |
| `Bot.pick_up` | Swap the non-gun slot | A combat loadout table can change `WEAPON_VALUE`. It should still look past the gun. |
| `Bot.steer_to`, `geom.walk_clear`, `nav.Planner.start`, `Bot.avoid_walls` | Geometry | Combat code should not reintroduce a straight line into a blocked or too-steep cell. |

`Bot.fight_move`, `Bot.mortar_solution`, and the laser charge state machine were not rewritten. `mortar_solution` is unchanged; the caller rejects pitches the torso cannot hold.
