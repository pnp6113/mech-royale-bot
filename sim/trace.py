"""Trace one APPROXIMATE match: our mode/position/zone every N seconds.  python sim/trace.py SEED [every_s]"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import brain  # noqa: E402
import house  # noqa: E402
import sim  # noqa: E402


def main():
    seed = int(sys.argv[1])
    every = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    holder = {}

    def factory(hello):
        b = brain.Bot(hello)
        holder["b"] = b
        return b

    m = sim.Match(seed, factory, house.make_factory(seed))
    orig = m.decide
    last = [-99.0]

    def decide():
        orig()
        us = m.mechs[m.our_slot]
        b = holder["b"]
        if m.t - last[0] >= every and us.alive:
            last[0] = m.t
            zc, zr = m.zone_now()
            dz = math.hypot(us.pos[0] - zc[0], us.pos[1] - zc[1])
            dn = math.hypot(us.pos[0] - m.nc[0], us.pos[1] - m.nc[1])
            g = b.planner.path_goal if b.planner else None
            print(f"t={m.t:6.1f} mode={b.mode:7s} pos=({us.pos[0]:7.1f},{us.pos[1]:7.1f}) hp={us.hp:6.0f} "
                  f"zone r={zr:5.0f} d={dz:5.0f} next r={m.nr:4.0f} d={dn:5.0f} ss={m.ss:5.0f} se={m.se:5.0f} "
                  f"cmd={us.cmd['move'][0]:+.2f},{us.cmd['move'][1]:+.2f} v={math.hypot(us.vel[0], us.vel[1]):4.1f} "
                  f"goal={g and (round(g[0]), round(g[1]))} path={len(b.planner.path) if b.planner else 0} "
                  f"blocked={m.m.is_blocked(us.pos[0], us.pos[1])}")

    m.decide = decide
    r = m.run()
    print(r)


if __name__ == "__main__":
    main()
