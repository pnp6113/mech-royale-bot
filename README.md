# Mech Royale bot

Team **Last Mech Standing**: our entry for the **Builders Buildathon 2026, Mech Royale track** (Builders): the AI for one mech in a
16-mech 3D battle royale on the Ohio State campus. Last mech standing wins.

## Reference benchmark

This experiment branch compares our v8 bot with two public, third-party controllers. The root `Dockerfile` builds our v8 bot. `Dockerfile.reference` builds either attributed controller for benchmarking. Build commands, measured results, and organizer eligibility questions are in [the benchmark handoff](docs/GIDDY_REFERENCE.md).

| Arm | Source | Revision |
| --- | --- | --- |
| A | Last Mech Standing v8 | [`7a5737c`](https://github.com/pnp6113/mech-royale-bot/tree/7a5737c64af3ca91756f3429a804cba9c0bc1986) |
| B | giddygiddyboy, MIT | [`345c0de`](https://github.com/justnau/giddygiddyboy-mech-royale/tree/345c0de2f43f5f27a4ecf74c1c25c789d00fbc49) |
| C | Team Kanyaraasi, MIT with Giddy attribution | [`24d68e5`](https://github.com/devakchow/team-kanyaraasi-mech-royale/tree/24d68e50ecd1ecb272b614b8a38c509bc8eeee5f) |

The complete pinned source trees are in `reference/`. Their original notices are in [Giddy LICENSE](reference/giddy/LICENSE), [Kanyaraasi LICENSE](reference/kanyaraasi/LICENSE), and [Kanyaraasi third-party notices](reference/kanyaraasi/THIRD_PARTY_LICENSES). Reference images and binary exports retain the applicable notices and Go runtime license under `/licenses`. The branch adds benchmark tooling and packaging. The reference controllers are the upstream teams' work.

> Status: development environment only. The bot is still the official kit's example bot
> (`example_bot.py`). The competitive bot has not been written yet.

## Layout

| Path | What it is |
| --- | --- |
| `Dockerfile` | Submission image (must stay at the repo root). Official Python starter, base image pinned. |
| `example_bot.py` | Current bot entrypoint (official example bot, unchanged). |
| `mechfight.py` | Official `mechfight/1` Python wrapper (stdin/stdout loop, typed messages, debug drawing). |
| `kit/protocol.md` | Full rulebook and protocol. **Read first.** |
| `kit/mechfight.schema.json` | JSON Schema (draft-07) for every message. |
| `kit/check/check.py` | Official conformance check (replays 300 recorded observations). |
| `kit/arena/v2/` | Map: `world.json` (buildings, bridges, trees) and `terrain.i16` (heightmap). Mounted at `/arena` in matches. |
| `kit/ts/` | Official TypeScript starter, kept for reference. |
| `kit/KIT_README.md` | The kit's original README. |
| `scripts/check.ps1`, `scripts/check.sh` | Run the local and sandboxed-Docker checks in one command. |

`.dockerignore` keeps `kit/`, `scripts/` and docs out of the image, so only the bot code ships.

## Build and test

Windows (PowerShell):

```powershell
.\scripts\check.ps1            # local + Docker sandbox check
.\scripts\check.ps1 -Mode local
```

macOS / Linux / Git Bash:

```sh
scripts/check.sh               # local + Docker sandbox check
```

The same commands, by hand:

```sh
python3 kit/check/check.py -- python3 -u example_bot.py
docker build -t mech-bot .
python3 kit/check/check.py --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m \
  --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -v "$PWD/kit/arena:/arena:ro" mech-bot
```

The Docker command is the scorer's sandbox: no network, 1 CPU, 512 MB, read-only disk except `/tmp`
(64 MB), uid 65534. `check.py` only proves protocol compliance. It does not simulate a game.

## Submission rules (summary)

- Public GitHub repo, `Dockerfile` at the top, at most 20 MB at the submitted commit.
- Built on linux/amd64 with network on, within 10 minutes. Pin versions: finalists are rebuilt.
- 50 ms per decision, 10 decisions per match-second.
- Score = mean of (16 − place) over 48 practice matches. Baseline (this example bot) is 1.15.
- 4 submissions per hour. Keep every submitted commit public until results are published.

## Third-party code

- `mechfight.py`, `example_bot.py`, everything under `kit/`: the official mechfight bot kit from
  Builders (joinbuilders.org), distributed to Buildathon participants.
- No other third-party libraries yet. Add any you use here (Buildathon rule).

## License

MIT. See [LICENSE](LICENSE).
