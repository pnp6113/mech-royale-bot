# Giddy reference benchmark

This branch uses the strongest public revision with a documented score that can be compared to its exact parent controller. Arm A is Last Mech Standing v8, `7a5737c64af3ca91756f3429a804cba9c0bc1986`, with the team's documented **4.29** practice score. B is Giddy `345c0de2f43f5f27a4ecf74c1c25c789d00fbc49`, the upstream revision identified by C. C is the **5.83** Kanyaraasi control, `24d68e50ecd1ecb272b614b8a38c509bc8eeee5f`, as identified by Kanyaraasi's later README. The official leaderboard corroborates Kanyaraasi's team score. The SHA-to-score link is the team's statement. B's exact revision has no verified official score. [Own practice log](https://github.com/pnp6113/mech-royale-bot/blob/f4f629d06552ab20894873adbe5e69d70cb7116d/docs/EXPERIMENTS.md#L24-L36) · [Kanyaraasi pin and score](https://github.com/devakchow/team-kanyaraasi-mech-royale/blob/def0f73cc925e10addacb284c709f3176eccea44/README.md#L1-L34) · [Official leaderboard](https://www.joinbuilders.org/buildathon/leaderboard)

## Source and builds

`reference/SOURCES.json` records the upstream commits, their Git tree IDs, and hashes for every tracked file. The 10 Giddy files and 11 Kanyaraasi files were materialized from the exact Git blobs. The checker also verifies all six runtime SHA-256 values in Kanyaraasi's README and that its third-party notice matches Giddy's MIT license byte-for-byte. Both modules build with standard-library Go and no third-party dependencies. The builder uses the upstream's digest-pinned Go 1.26.1 Debian image and runs Go compilation without a network. The production-style result is a scratch image with no runtime dependencies and UID/GID 65534. [Giddy runtime source](https://github.com/justnau/giddygiddyboy-mech-royale/tree/345c0de2f43f5f27a4ecf74c1c25c789d00fbc49) · [Kanyaraasi runtime source](https://github.com/devakchow/team-kanyaraasi-mech-royale/tree/24d68e50ecd1ecb272b614b8a38c509bc8eeee5f)

```sh
python tools/verify_reference_sources.py
docker build --platform linux/amd64 -t mech-reference-codex:v8 .
docker build --platform linux/amd64 -f Dockerfile.reference --build-arg CONTROLLER=giddy -t mech-reference-codex:giddy .
docker build --platform linux/amd64 -f Dockerfile.reference --build-arg CONTROLLER=kanyaraasi -t mech-reference-codex:kanyaraasi .
```

For the Windows simulator runs, export the Go binaries and their licenses. On a Linux host, `--target runtime` with `--output type=local,dest=...` exports the Linux `bot` at the destination root. Pass each host-native executable path with `--giddy` and `--kanyaraasi` if it differs from the runner's default.

```sh
docker build --platform linux/amd64 -f Dockerfile.reference --target windows --build-arg CONTROLLER=giddy --output type=local,dest=.reference-build/giddy .
docker build --platform linux/amd64 -f Dockerfile.reference --target windows --build-arg CONTROLLER=kanyaraasi --output type=local,dest=.reference-build/kanyaraasi .
```

## Conformance and resource use

Run `./tools/reference_sandbox.ps1` from PowerShell with Docker Desktop. The script preserves the official checker's code, then separately applies stricter action validation and measures monotonic wall time. The checker and candidate share the Linux/amd64 container's one CPU and 512 MB cap. The container has no network, a read-only root and arena mount, UID/GID 65534, and a 64 MB `/tmp`. Reference verification launches the same compiled binary as the corresponding scratch runtime image.

| Arm | Official replay timely | Official p99 / max | Strict p50 / p99 / max | Candidate peak RSS | Checker + bot cgroup peak |
| --- | ---: | ---: | ---: | ---: | ---: |
| A · v8 | 300/300 | 10.7 / 17.8 ms | 1.193 / 3.457 / 4.276 ms | 25,660 KiB | 46,768,128 B |
| B · Giddy | 300/300 | 1.7 / 20.6 ms | 0.499 / 1.281 / 21.523 ms | 39,184 KiB | 68,087,808 B |
| C · Kanyaraasi | 300/300 | 4.3 / 21.4 ms | 0.944 / 2.359 / 18.580 ms | 37,772 KiB | 68,165,632 B |

Every arm passed the official protocol check and all 300 strict actions. The strict runs separately exercised 12 queued batches per arm, reported zero stale or invalid frames and zero decisions over 50 ms, and observed clean shutdown. Both scratch images separately returned `ready` and a valid action under the same caps. Five fault controls passed on Windows and in the Linux sandbox, including detection of a child crash, stale responses, incorrect action shape, and non-finite JSON values. Run them with `python -m unittest discover -s tests -p test_reference_verify.py -v`. The intact C binary sends `giddygiddyboy` in its ready message, as its upstream source specifies.

## Matched local results

The repository simulator ran 48 practice-style squad seeds per arm and 16 independently targeting opponent seeds per arm, for 192 matches. Seed and focus slot matched across arms. Independent reconstruction also reproduced all 16 starting positions per seed. The Python controller ran in-process. The two Go controllers ran as separate Windows binaries, with actual protocol actions and no controller failures. [Per-match results](giddy-reference-results.json)

| Scenario | Metric | A · v8 | B · Giddy | C · Kanyaraasi |
| --- | --- | ---: | ---: | ---: |
| Practice | Mean place | 12.854 | 12.708 | 12.354 |
| | Mean placement points | 3.146 | 3.292 | 3.646 |
| | Kills / survival | 2.667 / 85.92 s | 2.646 / 85.84 s | 2.875 / 92.22 s |
| | Weapon / zone damage received | 1,982.7 / 29.2 HP | 2,022.4 / 58.3 HP | 2,029.8 / 58.3 HP |
| | Zone deaths | 1 | 2 | 2 |
| Modeled FFA | Mean place | 2.000 | 4.812 | 4.750 |
| | Mean placement points | 14.000 | 11.188 | 11.250 |
| | Kills / survival | 2.875 / 211.86 s | 1.375 / 170.04 s | 1.750 / 144.08 s |
| | Weapon / zone damage received | 1,213.6 / 3.1 HP | 1,487.2 / 263.5 HP | 1,295.8 / 87.5 HP |
| | Zone deaths | 0 | 2 | 1 |

The mean paired practice changes vs A were **+0.146** points for B (approximate 95% interval −0.756 to +1.048) and **+0.500** for C (−0.604 to +1.604). In the modeled FFA they were **−2.812** for B (−5.119 to −0.506) and **−2.750** for C (−4.689 to −0.811). The FFA result is specific to the modeled opponents and is sensitive to early third-party deaths and weapon pickups.

| Practice acquisition and fire | A · v8 | B · Giddy | C · Kanyaraasi |
| --- | ---: | ---: | ---: |
| Matches with a non-gun pickup | 40/48 | 35/48 | 34/48 |
| Median time to first non-gun pickup when acquired | 4.00 s | 5.20 s | 5.15 s |
| Laser acquisitions / median time when acquired | 13 / 3.90 s | 20 / 11.55 s | 22 / 15.25 s |
| Lightning acquisitions / median time when acquired | 35 / 7.50 s | 24 / 9.05 s | 25 / 9.10 s |
| Laser releases / full charges / mech hit events | 85 / 75 / 75 | 231 / 134 / 183 | 265 / 112 / 211 |
| Laser HP removed from other mechs | 29,926 | 85,258 | 90,551 |

C acquired lasers in more practice matches and produced more laser releases. Median time from pickup to first release among firing matches was 2.40 s for A, 13.85 s for B and 7.70 s for C. Each arm acquired a laser in 3 of 16 modeled FFA matches. A released three laser shots and each reference released zero. C survived longer in practice and took slightly more weapon damage per match, so the sample supplies no direct evidence of lower incoming damage. In the modeled FFA, A had lower incoming damage and fewer lethal zone hits than either reference.

```sh
python sim/reference_compare.py --scenario practice -n 48 -j 6 --output .reference-build/practice.json
python sim/reference_compare.py --scenario ffa -n 16 -j 4 --output .reference-build/ffa.json
```

This simulator's opponents, collision, visibility, and timing approximate the real game. It omits supply drops, so C's opportunistic drop rule did not participate in these results. A known projectile-order discrepancy can register an impact behind a wall within a step. Shared RNG draws and time-sliced pathfinding can diverge after the common start. In FFA there is one tested controller per match with 15 simplified individually targeting opponents. The model has neither the organizer's seeds nor a finalist field, and the point values above are local outcomes only.

## Architecture comparison

- **A** uses one Python controller for both formats. It has v8's weapon rush, survival upgrade preferences, leading gun, laser and lightning policy, retreat hysteresis, retreat cover search, and a time-sliced A* navigation grid with hops. It uses strafe and preferred range for fight movement. [A movement, combat, and loot](https://github.com/pnp6113/mech-royale-bot/blob/7a5737c64af3ca91756f3429a804cba9c0bc1986/brain.py)
- **B** tracks enemies and loot over time, favors connected arena paths, and scores candidate fight positions by range, fire and exposure, clearance, zone, and cube pull. Its squad profile sets a 140 m laser preference. It can switch from a provisional squad profile to its base FFA settings as unexplained deaths accumulate. [B brain](https://github.com/justnau/giddygiddyboy-mech-royale/blob/345c0de2f43f5f27a4ecf74c1c25c789d00fbc49/mech/brain.go) · [140 m change](https://github.com/justnau/giddygiddyboy-mech-royale/commit/345c0de2f43f5f27a4ecf74c1c25c789d00fbc49)
- **C** shares B's world geometry and protocol types. Its changes include conservative opportunistic drop pursuit, early aligned laser release for a conservatively lethal shot, and temporary squad-only closing range when a weak enemy is finishable under its health and crowd guards. It restores normal range after each scoring pass. [C delta and inheritance](https://github.com/devakchow/team-kanyaraasi-mech-royale/blob/24d68e50ecd1ecb272b614b8a38c509bc8eeee5f/README.md#L3-L34) · [C brain](https://github.com/devakchow/team-kanyaraasi-mech-royale/blob/24d68e50ecd1ecb272b614b8a38c509bc8eeee5f/mech/brain.go)

B and C use a laser-first opening. After 20 seconds they can enable squad behavior, including more permissive engagement and farming, longer laser spacing, and stronger target persistence. One persistent uncredited death across five observations is enough to latch their FFA policy. That policy narrows voluntary fights and favors lower-exposure positions, cover, and healing. Zone or drone deaths can satisfy the same signal, and a quiet FFA can temporarily run squad behavior. C's early laser release remains enabled after the switch. Its finish and opportunistic drop changes are squad-only. [Shared detector](https://github.com/justnau/giddygiddyboy-mech-royale/blob/345c0de2f43f5f27a4ecf74c1c25c789d00fbc49/mech/brain.go#L312-L339) · [C squad parameters](https://github.com/devakchow/team-kanyaraasi-mech-royale/blob/24d68e50ecd1ecb272b614b8a38c509bc8eeee5f/mech/brain.go#L1957-L1977)

## Eligibility and handoff

The event rules require MIT licensing for event-built work and allow credited open-source libraries and your own pre-event code. The Mech Royale rules allow any language, libraries, and AI, and specify the public repo, root Dockerfile, build, and runtime requirements. The published rules leave eligibility for submitting another team's mostly unchanged controller unresolved. **Keep the slot for an original candidate.** If the organizer explicitly approves an attributed reference-only run and clarifies how its result would affect leaderboard and qualification selection, C is the useful single run for practice calibration. Keep the branch's separate reference Dockerfile until that decision. [Event rules and contact](https://www.joinbuilders.org/buildathon#rules) · [Mech Royale track rules](https://www.joinbuilders.org/buildathon/tracks/builders)

For an original finals candidate, the strongest next experiments are:

1. **Exposure-aware rotation.** Score a bounded set of reachable moves along v8's existing route, before its dodge override. Penalize crossfire and arrival-time zone risk. Favor low-exposure healing positions inside the next circle when multiple enemies have lines or when the bot is outgunned. Drive this policy with visible encounter evidence so it stays useful even when the match format is unclear.
2. **Safe conversions.** Require an exit route, an actual firing lane, and enough projected HP after return fire before closing on a weak mech. Use weapon readiness and the prospect of a third party. Test this separately from an early aligned laser release that uses a damage lower bound and preserves the normal full-charge behavior for other shots.
3. **Reference head-to-head holdout.** Put A, B, C and original variants in the same modeled FFA, rotate slots across matched seeds, and vary the other bot policies. Track third-party losses, time outside the zone, damage taken while charging, and kills converted. Require the single-CPU protocol check for each candidate.
