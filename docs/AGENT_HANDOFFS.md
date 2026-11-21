# Agent handoffs: Last Mech Standing (Mech Royale)

Each section below is self-contained: paste it into the named tool as-is. Claude (lead) owns `main` and
integration. **Nobody but the lead pushes to `main` or submits on the Buildathon site.**

Shared facts for every worker:
- Repo: https://github.com/pnp6113/mech-royale-bot (public). Python 3.13, standard library only.
- Rules: `kit/protocol.md` (authoritative), schema `kit/mechfight.schema.json`, live numbers in `hello.config`.
- Runtime limits: **50 ms per decision**, 1 CPU, 512 MB, no network, read-only disk except `/tmp`, 20 s for `ready`.
- Scoring: mean(16 − place). Practice = 48 matches vs **15 house bots acting as one squad that hunts us**
  (they never fight each other, so in practice our score is roughly how many of them die before we do). The final is a
  16-mech free-for-all vs other teams' bots. Don't overfit to practice.
- Gates before any PR: `python tests/test_scenarios.py`, `NOTRACE=1 python tools/replay.py`,
  `.\scripts\check.ps1` (needs Docker Desktop). Never print to stdout: stdout is the protocol. Log with `mf.log`.
- Work in your **own clone or `git worktree`** on your branch. Open a PR to `main` with measured results.

## Code map and interfaces (keep these signatures stable)

| File | Owner | What it does |
| --- | --- | --- |
| `geom.py` `MapModel` | survival | `ground(x,y)`, `los(a,b)`, `is_blocked(x,y)`, `walk_clear(x0,y0,x1,y1)`, `ncell/ncenter`, 2 m solid grid (`top`,`base`,`wall2`), 4 m nav grid (`blocked`,`nground`) |
| `nav.py` `Planner` | survival | `start(sx,sy,gx,gy,extra_cost=None)`, `step(budget_s)->bool`, `lookahead(x,y)->(x,y)|None`, `path` |
| `brain.py` `Bot.act(obs)` | lead | builds the action: events → zone → threats/target → mode → movement → dodge → walls → stuck → aim/fire → pickups → upgrades |
| `Bot.fight_move(tgt, kinds, weapons, px, py, hp_frac, ec, er, t, t0)->(dx,dy)` | combat | combat positioning |
| `Bot.dodge(obs, me, mycenter, px, py, pz, vx, vy, scale)->(dx,dy,laser_threat)` | combat | projectile/laser evasion vector |
| `Bot.aim_and_fire(...)->(aim[yaw,pitch], fire[bool,bool])` | combat | targeting and triggers for both slots |
| `Bot.intercept(src, pt, vel, speed)`, `Bot.mortar_solution(muzzle, e, ms, high)` | combat | ballistics |
| `Bot.steer_to(goal, px, py, t0)`, `Bot.avoid_walls(mx,my,px,py)`, `Bot.choose_loot(...)` | survival | movement |
| `Bot.pick_upgrade(choices, kinds, me)->int`, `UPGRADE_VALUE`, `WEAPON_VALUE` | lead | economy |

If you need a new hook, add a **pure function in a new module** (e.g. `combat.py`, `survival.py`) and call it from
the matching `Bot` method. Don't restructure `brain.py`.

---

## 1. Codex account 1: Combat Specialist (branch `feat/combat-codex`)

Use the strongest Codex model with high reasoning.

```
You are the combat engineer for a Mech Royale bot (Builders Buildathon 2026). Repo:
https://github.com/pnp6113/mech-royale-bot. Clone it, create branch feat/combat-codex. Read kit/protocol.md fully
(sections 5-7 and 10 are combat), then brain.py (Bot.aim_and_fire, Bot.dodge, Bot.intercept,
Bot.mortar_solution, Bot.fight_move). Python 3.13 stdlib only; each decision must take well under 10 ms.

Goals, in priority order. Put the math in a new module combat.py as pure functions with unit tests in
tests/test_combat.py, and call them from the existing Bot methods (keep those signatures):
1. Gun hit rate: intercept for target center (pos.z + 4.5*size scale) using target vel; shots leave 3.0 m along
   the actual torso aim from muzzle height; only fire when predicted miss distance < target radius
   (2.5*(1+0.05*cubes)) given self.aim (actual, slews at turn_rate/pitch_rate). Account for spread_deg.
2. Laser: hold to charge, release when the actual aim is on target (hitscan, aim at the position one decision
   ahead). Don't start charging if the target will leave LOS or we're being hit from another side.
   Cancel/hold rules per protocol (min_charge, hold_s auto-fire, cooldown). Track charge from self.weapons[i].charge.
3. Mortar: closed-form pitch for range R and height dz (v=speed, g=gravity), low and high arc; lead by flight
   time; reject solutions whose arc is blocked (use self.map.los on sampled arc points); never fire if the impact
   is within splash_radius + 4 m of ourselves.
4. Lightning: fire only if the nearest valid target in range and cone is a mech/NPC/crate we want to hit (it
   hits the NEAREST target in the cone); respect energy (keep ≥ 25 for escapes).
5. Target scoring: expected damage per second we can deal (weapon, range, LOS) vs threat to us; prefer
   low-HP targets we can finish, and attackers hitting our rear.
6. Projectile evasion: closest-approach on enemy gun bullets (enemy projectiles in obs.projectiles), mortar
   landing prediction with gravity and splash, laser aim lines from mechs with charging=true.
Deliver: PR to main with tests (tests/test_combat.py, plain asserts, runnable with python), a table of measured
hit/miss on synthetic cases, and act() p99 timing from `NOTRACE=1 python tools/replay.py`. All gates in
docs/AGENT_HANDOFFS.md must pass. Don't touch geom.py/nav.py. Don't print to stdout.
```

## 2. Codex account 2: Navigation and Survival Specialist (branch `feat/survival-codex`)

```
You are the navigation/survival engineer for a Mech Royale bot (Builders Buildathon 2026). Repo:
https://github.com/pnp6113/mech-royale-bot. Clone it, create branch feat/survival-codex. Read kit/protocol.md
sections 5, 7 (Movement), 8, 9, 11, then geom.py, nav.py and brain.py (steer_to, avoid_walls, choose_loot, the
zone block in act). Python 3.13 stdlib only; startup ≤ 3 s, act() p99 well under 10 ms.

Goals. Put new code in survival.py and/or extend geom.py/nav.py, keeping the public methods listed in
docs/AGENT_HANDOFFS.md:
1. Map fidelity: verify the 4 m walkability grid against the rules (step_up 1.2 m from config.mech, slopes >
   max_slope_deg, bridges/overhangs with 9 m head clearance, roof edges). Add a test that random A* paths
   never cross blocked cells, and a tool that renders the grid to PNG/ASCII for inspection.
2. Zone forecasting: be inside the next circle with margin before shrink_end; estimate travel time along the
   A* path (not straight-line); start rotating early enough that we're never outside. Late phases (radius ≤ 70):
   choose positions that minimise distance to every plausible future centre.
3. Danger field for A* (Planner.start extra_cost): cost for cells near known enemies, open ground with no cover,
   and outside the forecast zone.
4. Cover: find nearby cells where a building blocks LOS to the main threat (use MapModel.los); use for
   retreat/regen (regen needs 5 s without dealing or taking damage).
5. Stuck detection and recovery that doesn't oscillate: jump + jet (energy) over ledges, back off and replan.
6. Resource routing: risk-adjusted value of pickups, cubes, crates, NPC drones, supply drops (capture alone for 4 s).
Deliver: PR to main with tests (tests/test_survival.py), measured timings, and a short note of what changed in
behaviour. All gates in docs/AGENT_HANDOFFS.md must pass. Don't change combat code. Don't print to stdout.
```

## 3. Grok: Independent Strategy Researcher (no code changes to main)

```
You are a strategy researcher for a team competing in "Mech Royale" (Builders Buildathon 2026): a 16-mech
battle royale where each bot gets JSON observations 10x/second and replies with move/aim/fire. Read the full
rulebook at https://github.com/pnp6113/mech-royale-bot/blob/main/kit/protocol.md and the track rules below.

Track rules: score = mean of (16 - place) across matches. Practice = 48 matches vs 15 house bots (4 easy,
4 medium, 4 hard, 3 expert) acting as ONE squad: they share vision, never fight each other, hunt you weakest
first (one early, more as you thin them out, all late). Final = top 32 play 16 free-for-all matches vs other
teams' bots; top 16 play 64 more. Placement counts, not kills. Current practice board top ≈ 5.6, baseline 1.15.

Deliver a ranked report (markdown):
1. What actually decides the score in practice vs in the final (e.g. since house bots never fight each other,
   practice score ≈ kills before death + timeout effects). Check this reasoning against the rulebook.
2. The 10 highest-impact tactics, each with expected effect, risk, and how to verify it from a 48-match
   practice score (4 submissions/hour).
3. Weapon loadout analysis (gun/mortar/laser/lightning, triangle, cube scaling, upgrades): which loadout and
   upgrade order maximise survival + kills vs a hunting squad, and vs free-for-all bots.
4. Zone and supply-drop timing: when to rotate, when a drop is worth contesting.
5. Dangerous assumptions in a "survival-first" plan and how they could fail.
6. 10 concrete tactical test scenarios (exact positions/velocities/weapons) we can encode as unit tests.
Don't write production code. Cite protocol sections for every rule you rely on.
```

## 4. Cursor Pro: QA and Performance Engineer

```
You are QA/performance for a Mech Royale bot. Repo: https://github.com/pnp6113/mech-royale-bot. Clone to a
separate folder (not the lead's working tree), branch qa/cursor. Windows + Docker Desktop available.
Tasks:
1. Run all gates: python tests/test_scenarios.py; set NOTRACE=1 then python tools/replay.py; .\scripts\check.ps1.
   Confirm the Docker check prints "map loaded from /arena".
2. Profile act() under the sandbox (docker --cpus 1): report p50/p95/p99/max decision time and peak RSS.
   Find the slowest code paths (cProfile over tools/replay.py) and propose concrete optimisations.
3. Fuzz robustness: write tests/test_fuzz.py that mutates real observations from kit/check/stream.jsonl.gz
   (drop optional keys, nulls where the schema allows, empty lists, extreme positions at map edges, huge lists
   of projectiles/mechs, offers with unknown upgrade ids, weapons null) and asserts act() never raises, never
   exceeds 20 ms, and always returns a schema-valid action (use check.Schema from kit/check/check.py).
4. Navigation audit: script that samples 500 random start/goal pairs, runs nav.Planner, and reports failures,
   paths through blocked cells, and worst-case planning time.
5. Review every open PR (feat/combat-codex, feat/survival-codex) for protocol violations, stdout writes,
   unbounded memory, and timing risk. Comment with findings; don't merge.
Deliver: PR with tests/test_fuzz.py + tools/nav_audit.py and a QA report in docs/QA.md.
```

---

## Integration protocol (lead)

1. Workers open PRs; the lead pulls each branch into a separate worktree, runs every gate, and merges one at a time.
2. After each merge: full gates, push, then submit only if the change is expected to move the score.
3. Every submitted SHA is tagged (`v1`, `v2`, …) and logged in `docs/EXPERIMENTS.md`. Tags are never deleted.
4. The best-scoring tag is the fallback. A new version replaces it only after a real practice score beats it.

---

## Integration status (lead, Sat 03:10 ET)

**Codex combat (`feat/combat-codex` @ ed12f8a): integrated on `integ/combat`, behind `TUNE["codex_combat"]`, not yet submitted.**
- Wiring follows COMBAT_INTEGRATION.md: one dodge layer (our `dodge()` is skipped), the controller gets the final nav move
  after walls/stuck/hops, its dodge is off during jumps/hops/stuck recovery and when the tick already used 20 ms;
  exceptions fall back to our aim/fire.
- Gates: 17 scenario tests run (3 encode our old controller's choices, so they "fail" by design under Codex: head-on
  bullet at 0.16 s, mortar dodge side, laser not charging at 100 m), the replay is schema-clean, and check.py passes.
- APPROXIMATE sim, 48 seeds: on v3 base 2.62 vs 2.46 (noise); on v4 base **2.62 vs 2.73 (no gain)**, zone damage
  28 vs 0. Uncontended latency p99 7 ms, max 11 ms (ours alone: about 3x lower).
- **Next:** one real practice A/B (v4 + Codex vs v4). Ship only if the real score beats v4.

**Requests back to Codex (combat):**
1. `choose_dodge` costs most of the time (`_simulate`, LOS). Add an explicit time budget argument and early-out.
2. In the sim, zone damage appeared only with the controller on (28 vs 0, cause not confirmed; dodge moves are the likely suspect). Consider accepting a `zone=(center, radius)` constraint in candidate scoring.
3. `tests/test_combat_*.py` need `sys.path` set up for `python -m unittest discover -s tests` from the repo root.
