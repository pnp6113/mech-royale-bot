# v6 cover combat experiment

This branch starts at the `v6` tag, `417f6eb108542577ceedf749f34290c9cb4cdd7c`. The experiment adds ground-level positioning around building cover in gun fights. V6 still chooses targets, leads shots, dodges projectiles, picks up weapons and cubes, navigates, and decides when to retreat for health recovery.

## Enable for a candidate

Cherry-pick the feature commit onto Claude's branch. Keep `cover_geometry.py`, `cover_engagement.py`, and the `brain.py` hooks together. Set `TUNE["cover_peek"] = True` before constructing `brain.Bot`. The feature is off by default. One `CoverEngagement` instance holds the state for each match. The branch uses the existing map and wrapper, with no additional dependencies.

The hook runs in `Bot.act` after fight mode and pickup priorities are known. `CoverEngagement.decide()` returns a short movement vector, a requested reload, and a clear-lane flag. The separation between planned cover movement and v6 projectile dodging lets v6 override the move on an incoming shot. The existing gun intercept stays in `aim_and_fire`. A gun trigger during an active cover plan also requires a clear ray to that same lead point. `bot.cover_phase` exposes the active phase for replay or simulator instrumentation.

The hook yields to v6 when the arena is absent, when zone, retreat, recovery, weapon rush, or cube pickup takes priority, and during stuck recovery, hopping, or airborne movement. Laser builds and close-range lightning take their usual engagements. V6 handles fights that lack a usable cover pair.

## Positioning and safety checks

- Find a reachable hold near an obstacle and a firing point with a walkable return route. Geometry uses the live ground height and mech hull, blocked navigation cells, and lines through the arena's solid raster. Tree trunks and canopy affect sight in the game and do not stop bullets.
- Keep both points in the current and projected zone. At the hold, require that the primary opponent's line to the projected body is blocked. At the peek, require clear center and expected gun-lead rays, plus cover from other nearby shooting angles.
- Move into cover, wait briefly for the gun, expose for a short burst, and return. Start a low-magazine reload while protected. A stalled route, moved target, new crossfire angle, or shrinking zone invalidates the pair. A failed hold is temporarily excluded from the next search.
- The burst and dwell timers limit repeated corner crossings. Revalidate a cached pair periodically, and bound the number of candidate points and lines searched on acquisition. Map geometry and target movement are estimates, so a valid pair can become unsafe before the next observation.

## Verify and compare

```sh
python -m unittest discover -s tests -p 'test_cover_combat.py' -v
python tests/test_scenarios.py
python kit/check/check.py -- python -u -c "import brain, mechfight; brain.TUNE['cover_peek'] = True; mechfight.run(brain.Bot)"
python sim/cover_ab.py -n 48 -j 6 --seed0 1 --output cover-comparison.json
```

The runner also accepts `--arm cover --baseline-input baseline.json` to reuse a baseline saved on the pinned v6 checkout. The official checker uses a fixed observation stream and measures valid, timely actions.

## Local results

The approximate 16-mech comparison used seeds 1–48, six workers, the pinned v6 baseline, and the feature switched on in the cover arm. Kills, damage, placement, and survival are per-match means. A zone death is a lethal zone hit.

| Metric | v6 | Cover |
| --- | ---: | ---: |
| Placement points | 2.771 | 2.729 |
| Place | 13.229 | 13.271 |
| Kills | 2.354 | 2.458 |
| Damage received | 1,927.1 HP | 1,921.4 HP |
| Survival | 70.20 s | 67.99 s |
| Zone deaths | 0 | 0 |
| Parallel simulator decision p99 | 3.20 ms | 5.81 ms |

The paired placement-points change was −0.042, with an approximate 95% interval of [−0.722, +0.639]. The 40 held-out seeds (9–48) averaged 2.925 → 3.025 placement points, 2.575 → 2.725 kills, 1,946.3 → 1,972.2 HP received, and 67.42 → 68.97 s survival. The held-out paired placement-points interval was [−0.687, +0.887], with zero zone deaths in both arms.

The policy issued movement orders on 215 of 32,677 treatment decisions in 13 matches. Those orders included 144 moving into cover, 39 holding, 22 approaching a peek, one at the firing point, and nine retracting. The active decisions measured 15.39 ms p99 and 39.05 ms maximum. Over all parallel decisions, the baseline had 13 readings above 50 ms and the cover arm had 25. Parallel timings include competition for CPU time across simulated matches.

A separate single-process arena fixture measured 10.92 ms p99 and 12.36 ms maximum for 120 full decisions that acquired a corner pair. For 120 full decisions holding a pair, p99 was 0.80 ms and maximum was 1.69 ms. The cover-disabled bot produced the same actions as pinned v6 on all 300 recorded observations when the existing navigation time budget used the same deterministic clock.

In a separate two-seed, 10-second corner fixture with a stationary gun opponent, mean damage received fell from 675 to 292.5 HP, and mean damage dealt fell from 832.5 to 315 HP. All four duels ended at timeout with both mechs alive. The fixture isolates exposure at one corner. The full matches provide inconclusive evidence of a placement gain, so the experiment remains opt-in.

## Simulator limits

The repository's simulator runs matches against a modeled house squad. Matched seeds reproduce the initial setup. Later random draws can diverge as the bots take different actions, and pathfinding uses elapsed-time budgets. The simulator uses rasterized line of sight and approximate movement. It can apply a cylinder hit before checking a wall on the same projectile step, a defect verified with a single-projectile fixture. Gun muzzle offset also lacks the documented wall clamp. Those discrepancies affect this experiment directly. The local sample measures simulator outcomes, with Buildathon placement still unmeasured.

The checked-in protocol describes the map, visibility, movement, weapons, and 50 ms decision budget. [Official mechfight/1 protocol](https://www.joinbuilders.org/buildathon/tracks/builders/files/protocol.md?inline=1)
