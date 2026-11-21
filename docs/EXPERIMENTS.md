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
| Top-32 cutoff / top-16 / top (Sat 04:00 ET) | 5.06 / 5.31 / 5.96 | 198 teams; we are 78th at 4.19 |

## Submissions

| Ver | Commit | Submitted (ET) | Receipt | Score | Rank | Change / hypothesis | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v0 | edc5127 | not submitted | – | (1.15 = baseline) | – | official starter, rollback tag `v0-starter` | – |
| v1 | 3bc58dc | Sat 02:36 | BB-00BC9ADE | **3.42** | 113/183 | first utility bot: zone safety, dodging, gun lead, laser/lightning/mortar, loot, upgrades, A* nav | +2.27 over baseline |
| v1b | b650ef4 | Sat 02:40 | BB-AE58C7E7 | 3.33 | – | v1 with low-HP retreat disabled (`retreat_hp` 0.32 → 0) | inconclusive (−0.09): retreat isn't the lever; restored 0.32 |
| v2 | e0be091 | Sat 02:53 | BB-3E2C5FC8 | **3.77** | 95/184 | A* hop edges + jump/jet over walls (sim showed zone deaths trapped in the stadium pocket), cube grabbing, loot standoff fix | **better (+0.35)** |
| v3 | 26b1d5a | Sat 02:59 | BB-5344E3D3 | **3.79** | – | v2 + smart targets, cover retreat, regen hold | neutral (+0.02), matches sim |
| v4 | 1735122 | Sat 03:37 | BB-2D26F728 | **4.04** | 86/193 | v3 + weapon rush (with slot 2 empty, grab seen lightning/laser ≤80 m even mid-fight) | **better (+0.25)**: new best |
| v4c | fb13d48 | Sat 03:41 | BB-157F165B | 4.00 | – | v4 + Codex CombatController (integ/combat) | neutral (−0.04), matches sim: not merged |
| v5 | 1f3ff13 | Sat 03:54 | BB-49370AB7 | **4.19** | 78/198 | v4 + Cursor QA fixes (dodge sign, crossfire, swap, lightning nearest, mortar pitch, slopes, planner 2.5 ms) | **better (+0.15)**: new best |
| v6 | 417f6eb | Sat 04:00 | BB-B869F61E | **4.21** | – | v5 + retreat hysteresis: retreat <65% HP, stay back until 90% (sim 3.03 vs 2.74) | neutral (+0.02) |
| v7 | 5cdab49 | Sat 04:39 | BB-0B89A341 | **4.25** | – | v6 + engage_r 100: start fights only inside 100 m, always answer attackers (sim 3.20/3.22 vs 2.77) | +0.04: new best (sim overestimated) |
| v8 | 7a5737c | Sat 04:42 | BB-7EEB6537 | **4.29** | – | v7 + face_threats + no_mortar (sim 3.04 vs 2.76 on v7 base) | +0.04: new best |
| v9 | 544d924 | Sat 04:55 | BB-6744393F | 4.06 | – | v8 + upgrade_damage 4 (sim 3.17 vs 3.04) | **worse (−0.23)**: rejected; keep plating/armor order |
| v9r | 45ce606 | Sat 05:01 | BB-885F8DDA | pending | – | v8 + retreat 75%/95% (sim 3.16 vs 3.04) | – |

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
| retreat hysteresis (v4f code, F3/F4 off) | 96 | 65%/90%: **3.03**; 50%/85%: 2.74 | base 2.74 | strongest sim signal so far → v6 |

## Practice/FFA classifier validation (sim, mode.py) — NOT wired into the bot

| Rule | Squad matches: false FFA calls | FFA matches: detected | Verdict |
| --- | --- | --- | --- |
| >=3 unexplained deaths before 200 s | 1/48 | 40/48 (median 17 s) | fails 0-false-positive gate |
| >=3 before 60 s or >=6 before 150 s | 1/96 | 87/96 (median 17 s) | fails 0-false-positive gate |
Unexplained deaths also happen in squad play (NPC drones, zone). Not used; v6's retreat/heal policy is the finals policy.

## Reviews of other agents' branches

| Branch | What | Evidence | Decision |
| --- | --- | --- | --- |
| feat/combat-codex (ed12f8a) | full combat controller | real v4c 4.00 vs v4 4.04; sim no gain; 3x latency | not merged |
| qa/cursor (674685d) | correctness fixes | real v5 4.19 vs v4 4.04 | merged (v5) |
| feat/cover-combat (d9b1731) | opt-in cover peeking for gun fights, based on v6 | its own sim: 2.73 vs 2.77, active on 215/32,677 decisions | deferred: low activity, unproven; noted sim bug (bullet can hit a mech before the wall check on the same step) |

Watch page (`watch.joinbuilders.org/show`) airs exhibitions/lobby/final, not practice runs: no per-match diagnostics for us.
| drop_gun (laser + lightning loadout) on v9 base, corrected sim | 96 | **3.32** (kills 2.84, survive 91 s) | 2.92 (kills 2.50, 80 s) | promising; confirming on fresh seeds |
