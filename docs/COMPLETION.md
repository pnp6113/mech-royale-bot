# Mech Royale: completion report (Team Last Mech Standing)

Status as of Sat Nov 21, 09:06 ET. Submissions close Sun Nov 22, 11:00 AM ET.

## Final entry

| | |
| --- | --- |
| Selected entry | **v11**, receipt **BB-8B52F20D**, commit `933713c12466f34adced87323fc17a872543dc66` (tag `v11`, branch `rel/v11`, merged into `main`) |
| Practice score | **4.48** (best of 14 scored runs; the site marks it "Entered in the final (your best score)") |
| Leaderboard | 85th of 243 teams; top-32 qualifying cutoff 5.27; leader 6.00 |
| Repo | https://github.com/pnp6113/mech-royale-bot (public; every submitted commit is tagged and kept) |

v11 is our original code: v8's navigation, retreat/heal, zone and defensive logic, plus a laser-first package (laser pickup
priority, engage to 220 m while holding a laser, aim at the predicted release-time position, release a partial charge when
it is already lethal). In the approximate free-for-all model it holds up as well as v8 (13.16 vs 12.75), so it is a
reasonable finals controller too.

## Score history (real practice grader)

| Ver | Score | Change |
| --- | --- | --- |
| baseline | 1.15 | official example bot |
| v1 | 3.42 | first bot: zone, dodging, gun lead, laser/lightning/mortar, A* |
| v2 | 3.77 | jump/jet over walls (fixed zone deaths), cube pickup |
| v3 | 3.79 | target priority, cover retreat, regen hold |
| v4 | 4.04 | weapon rush (grab lightning/laser early) |
| v4c | 4.00 | + Codex combat controller: no gain, not used |
| v5 | 4.19 | Cursor QA bug fixes |
| v6 | 4.21 | retreat hysteresis (65% → 90%) |
| v7 | 4.25 | start fights only inside 100 m |
| v8 | 4.29 | face threats, never carry a mortar |
| v9 | 4.06 | damage-first upgrades: worse, reverted |
| v9r | 4.23 | earlier retreat (75% → 95%): no gain |
| **v11** | **4.48** | **laser-first package (original)** |

## What worked, what didn't

- Worked: navigation correctness (hops), weapon power (weapon rush, laser-first), bug fixes (Cursor QA).
- Didn't: Codex combat controller, damage-first upgrades, finer retreat tuning, early caution, practice/FFA classifier
  (1/96 false calls in sim, never wired in).
- The approximate simulator overestimates gains (v7: +0.45 sim, +0.04 real) but has ranked the big changes correctly.

## Not done / left open

- Competitor controllers (Giddy, Kanyaraasi) were benchmarked by Codex on `exp/giddy-reference` only. **Never submitted.**
- Untested ideas on branches, all switched off: multi-threat cover, early caution (`mlb-qa` working tree, uncommitted),
  Codex cover peeking (`feat/cover-combat`).
- Gap to qualification is ~0.8. Further gains likely need bigger structural work (crossfire avoidance, roof navigation),
  not tuning.
- If nothing else is submitted, v11 stays the final entry. The site auto-selects the best score; a later run only replaces
  it if it scores higher.
