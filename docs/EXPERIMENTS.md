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
| v1 | 3bc58dc | Sat 02:36 | BB-00BC9ADE | pending | – | first utility bot: zone safety, dodging, gun lead, laser/lightning/mortar, loot, upgrades, A* nav | – |

## Local gates every submitted commit must clear

1. `python tests/test_scenarios.py`: all pass
2. `NOTRACE=1 python tools/replay.py`: 0 schema errors, act p99 well under 10 ms
3. `.\scripts\check.ps1`: local PASS **and** Docker sandbox PASS, with the stderr line `map loaded from /arena`
4. Commit pushed and visible on GitHub, then submit that exact SHA

## Notes

- On Windows, `check.py --map-dir /arena` becomes `D:\arena`. The bot falls back to `/arena`, so the Docker check
  really does load the map (confirmed by its stderr line).
