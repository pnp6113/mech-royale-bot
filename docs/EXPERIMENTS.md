# Experiment log: Last Mech Standing

Practice score = mean(16 − place) over 48 matches vs 15 squad house bots (fixed private seeds).
Only rows with a receipt are real Buildathon results. Everything else is labelled local.

## Reference points (not ours)

| What | Score | Source |
| --- | --- | --- |
| Starter example bot (baseline) | 1.15 | track page |
| Practice board top (Sat 02:30 ET) | 5.60 | track page, 180 teams |
| Top-32 cutoff / top-16 / top (Sat 03:00 ET) | 4.98 / 5.21 / 5.75 | track page, 184 teams; we were 95th at 3.77 |
| Top-32 cutoff / top-16 / top (Sat 03:41 ET) | 5.04 / 5.27 / 5.96 | 193 teams; we are 86th at 4.04 |

## Submissions

| Ver | Commit | Submitted (ET) | Receipt | Score | Rank | Change / hypothesis | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v0 | edc5127 | not submitted | – | (1.15 = baseline) | – | official starter, rollback tag `v0-starter` | – |
| v1 | 3bc58dc | Sat 02:36 | BB-00BC9ADE | **3.42** | 113/183 | first utility bot: zone safety, dodging, gun lead, laser/lightning/mortar, loot, upgrades, A* nav | +2.27 over baseline |
| v1b | b650ef4 | Sat 02:40 | BB-AE58C7E7 | 3.33 | – | v1 with low-HP retreat disabled (`retreat_hp` 0.32 → 0) | inconclusive (−0.09): retreat isn't the lever; restored 0.32 |
| v2 | e0be091 | Sat 02:53 | BB-3E2C5FC8 | **3.77** | 95/184 | A* hop edges + jump/jet over walls (sim showed zone deaths trapped in the stadium pocket), cube grabbing, loot standoff fix | **better (+0.35)** |
| v3 | 26b1d5a | Sat 02:59 | BB-5344E3D3 | **3.79** | – | v2 + smart targets, cover retreat, regen hold | neutral (+0.02), matches sim |
| v4 | 1735122 | Sat 03:37 | BB-2D26F728 | **4.04** | 86/193 | v3 + weapon rush (with slot 2 empty, grab seen lightning/laser ≤80 m even mid-fight) | **better (+0.25)**: new best |
| v4c | fb13d48 | Sat 03:41 | BB-157F165B | pending | – | v4 + Codex CombatController (integ/combat) | – |

## Local gates every submitted commit must clear

1. `python tests/test_scenarios.py`: all pass
2. `NOTRACE=1 python tools/replay.py`: 0 schema errors, act p99 well under 10 ms
3. `.\scripts\check.ps1`: local PASS **and** Docker sandbox PASS, with the stderr line `map loaded from /arena`
4. Commit pushed and visible on GitHub, then submit that exact SHA

## Notes

- On Windows, `check.py --map-dir /arena` becomes `D:\arena`. The bot falls back to `/arena`, so the Docker check
  really does load the map (confirmed by its stderr line).

## Approximate simulator results (sim/, NOT official; same seeds within each comparison)

| Comparison | n | A | B | Read |
| --- | --- | --- | --- | --- |
| v1b config, before hop fix | 24 | 1.92 | – | zone deaths in 4/24 (trapped in stadium pocket) |
| v3 features on vs off | 48 | 2.15 | 2.44 | no evidence of gain (SE ≈ 0.3); real: neutral |
| Codex combat on vs off (v3 base) | 48 | 2.62 | 2.46 | noise; act() ~3.5x slower |
| weapon rush on vs off (v3 base) | 48 | 2.65 | 2.27 | promising (+0.38) → v4 |
| Codex combat on vs off (v4 base) | 48 | 2.62 | 2.73 | no gain; zone dmg 28 vs 0 |
| Ethan F1-F5 each vs base (v4 code, house lead 1.45x) | 96 | base 2.73 | jink 2.50, gun≤120 2.59, no_mortar 2.73, face 2.78, laser_cover 2.70, all 2.36 | none above noise (SE≈0.26) |
| retreat_hp 0.5 / 0.65 (v4 code) | 96 | 2.73 / 2.88 | base 2.73 | 0.65: longer survival (82 s vs 70 s) |
