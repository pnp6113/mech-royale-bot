"""Sample random walks on the official map and check the planner.

Reports paths that enter a blocked nav cell, uphill steps steeper than the
mech can walk (hops are allowed), searches that do not finish in budget,
and the slowest search.

    python tools/nav_audit.py
    python tools/nav_audit.py --n 500 --budget 0.02
"""
import argparse
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import geom  # noqa: E402
import nav  # noqa: E402


def load_map():
    arena = os.path.join(ROOT, "kit", "arena")
    t = time.perf_counter()
    m = geom.MapModel(arena, ["v2/world.json", "v2/terrain.i16"],
                      {"step_up": 1.2, "height": 9.0, "max_slope_deg": 50})
    return m, (time.perf_counter() - t) * 1000.0


def audit(m, n=500, budget=0.02, seed=6113, max_span=120.0):
    rng = random.Random(seed)
    free = [k for k, blocked in enumerate(m.blocked) if not blocked]
    pl = nav.Planner(m)
    blocked_hits = []
    steep = []
    unfinished = 0
    times = []
    checked = 0
    span_cells = max(2, int(max_span / m.ncs))
    for _ in range(n):
        a = rng.choice(free)
        j0, i0 = divmod(a, m.nnx)
        b = None
        for _try in range(16):
            i2 = min(m.nnx - 2, max(1, i0 + rng.randint(-span_cells, span_cells)))
            j2 = min(m.nny - 2, max(1, j0 + rng.randint(-span_cells, span_cells)))
            k = j2 * m.nnx + i2
            if not m.blocked[k] and k != a:
                b = k
                break
        if b is None:
            continue
        ax, ay = m.ncenter(a)
        bx, by = m.ncenter(b)
        pl.start(ax, ay, bx, by)
        spent = 0.0
        done = False
        # Same per-tick budget the bot uses, resumed across decisions.
        for _tick in range(16):
            t = time.perf_counter()
            done = pl.step(budget)
            spent += time.perf_counter() - t
            if done and not pl.active:
                break
        times.append(spent * 1000.0)
        if not done or pl.active:
            unfinished += 1
            continue
        if not pl.path:
            continue
        checked += 1
        prev = None
        for x, y, hop in pl.path:
            k = m.ncell(x, y)
            if m.blocked[k]:
                blocked_hits.append((round(x, 1), round(y, 1)))
                break
            if prev is not None and hop is None and prev[2] is None:
                rise = m.nground[k] - m.nground[m.ncell(prev[0], prev[1])]
                if rise > m.max_rise * 1.5:
                    steep.append((round(rise, 2), round(x, 1), round(y, 1)))
                    break
            prev = (x, y, hop)
    times.sort()
    return {
        "pairs": n,
        "checked": checked,
        "unfinished": unfinished,
        "blocked": blocked_hits[:8],
        "blocked_n": len(blocked_hits),
        "steep": steep[:8],
        "steep_n": len(steep),
        "times": times,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--budget", type=float, default=0.0025, help="per-tick seconds, same cap as Bot.steer_to")
    args = ap.parse_args()
    m, init_ms = load_map()
    out = audit(m, n=args.n, budget=args.budget)
    times = out["times"] or [0.0]

    def q(p):
        return times[min(len(times) - 1, int(p * len(times)))]

    print(f"map init {init_ms:.0f} ms  free-cells sampled {out['pairs']}  "
          f"paths checked {out['checked']}  unfinished {out['unfinished']}")
    print(f"search ms p50 {q(0.5):.2f} p95 {q(0.95):.2f} max {times[-1]:.2f}  "
          f"per-tick budget {args.budget * 1000:.1f} ms")
    print(f"blocked waypoints {out['blocked_n']} {out['blocked']}")
    print(f"steep non-hop steps {out['steep_n']} {out['steep']}")
    return 1 if out["blocked_n"] or out["steep_n"] else 0


if __name__ == "__main__":
    sys.exit(main())
