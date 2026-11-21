# Experiment log: Last Mech Standing

Practice score = mean(16 − place) over 48 matches vs 15 squad house bots (fixed private seeds).
Only rows with a receipt are real Buildathon results. Everything else is labelled local.

## Reference points (not ours)

| What | Score | Source |
| --- | --- | --- |
| Starter example bot (baseline) | 1.15 | track page |
| Practice board top (Sat 02:30 ET) | 5.60 | track page, 180 teams |

## Submissions

| Ver | Commit | Submitted (ET) | Receipt | Score | Rank | Change / hypothesis | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v0 | edc5127 | not submitted | – | (1.15 = baseline) | – | official starter, rollback tag `v0-starter` | – |
| v1 | 3bc58dc | Sat 02:36 | BB-00BC9ADE | **3.42** | 113/183 | first utility bot: zone safety, dodging, gun lead, laser/lightning/mortar, loot, upgrades, A* nav | +2.27 over baseline |
| v1b | b650ef4 | Sat 02:40 | BB-AE58C7E7 | 3.33 | – | v1 with low-HP retreat disabled (`retreat_hp` 0.32 → 0) | inconclusive (−0.09): retreat isn't the lever; restored 0.32 |
| v2 | e0be091 | Sat 02:53 | BB-3E2C5FC8 | **3.77** | – | A* hop edges + jump/jet over walls (sim showed zone deaths trapped in the stadium pocket), cube grabbing, loot standoff fix | **better (+0.35)**: new best, entered in final |

## Local gates every submitted commit must clear

1. `python tests/test_scenarios.py`: all pass
2. `NOTRACE=1 python tools/replay.py`: 0 schema errors, act p99 well under 10 ms
3. `.\scripts\check.ps1`: local PASS **and** Docker sandbox PASS, with the stderr line `map loaded from /arena`
4. Commit pushed and visible on GitHub, then submit that exact SHA

## Notes

- On Windows, `check.py --map-dir /arena` becomes `D:\arena`. The bot falls back to `/arena`, so the Docker check
  really does load the map (confirmed by its stderr line).

## Approximate simulator results (sim/, NOT official; same seeds within each comparison)

| Comparison | n | Mean A | Mean B | Read |
| --- | --- | --- | --- | --- |
| v1b config, before hop fix | 24 | 1.92 | – | zone deaths in 4/24 (bot trapped in stadium pocket) |
| v2 code (hops, cubes, loot fix) | 24 | 2.00 | – | zone damage 0 |
| v3 features on vs off (smart targets, cover retreat, regen hold) | 48 | 2.15 | 2.44 | no evidence of gain (SE ≈ 0.3); not shipped alone |
