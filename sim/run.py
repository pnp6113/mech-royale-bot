"""Run N APPROXIMATE matches of our bot vs the modelled house squad, in parallel.

    python sim/run.py -n 40 -j 8                  # current brain.TUNE
    python sim/run.py -n 40 --tune '{"retreat_hp": 0.32}'
"""
import argparse
import json
import multiprocessing as mp
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)


def one(args):
    seed, tune, ffa = args
    import io
    import contextlib
    import brain
    brain.TUNE.update(tune or {})
    import sim
    import house
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        m = sim.Match(seed, lambda hello: brain.Bot(hello), house.make_factory(seed))
        m.ffa = ffa
        t0 = time.perf_counter()
        r = m.run()
    r["wall_s"] = round(time.perf_counter() - t0, 1)
    r["seed"] = seed
    tb = [l for l in err.getvalue().splitlines() if "Error" in l or "Traceback" in l]
    r["errors"] = len(tb)
    if tb:
        r["first_error"] = err.getvalue()[-800:]
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=24)
    ap.add_argument("-j", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--seed0", type=int, default=1)
    ap.add_argument("--tune", default="{}")
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--ffa", action="store_true", help="house bots fight everyone (finals-like)")
    a = ap.parse_args()
    tune = json.loads(a.tune)
    jobs = [(a.seed0 + i, tune, a.ffa) for i in range(a.n)]
    t0 = time.time()
    with mp.Pool(a.j) as pool:
        res = pool.map(one, jobs)
    sc = [r["score"] for r in res]
    print(f"APPROX SIM {'FFA ' if a.ffa else ''} tune={tune}  n={len(res)}  wall={time.time() - t0:.0f}s")
    print(f"score mean {statistics.mean(sc):.2f}  sd {statistics.pstdev(sc):.2f}  "
          f"kills {statistics.mean(r['kills'] for r in res):.2f}  survive_t {statistics.mean(r['t'] for r in res):.0f}s  "
          f"cubes {statistics.mean(r['cubes'] for r in res):.1f}  zone_dmg {statistics.mean(r['zone_dmg'] for r in res):.0f}  "
          f"errors {sum(r['errors'] for r in res)}")
    import collections
    tw = [r["t_weapon"] for r in res if r.get("t_weapon") is not None]
    cf = [r["cubes_first_hit"] for r in res if r.get("cubes_first_hit") is not None]
    print(f"weapon by {len(tw)}/{len(res)} (median t {sorted(tw)[len(tw)//2] if tw else '-'}s)  "
          f"cubes@first_hit {statistics.mean(cf) if cf else 0:.1f}  laser_dmg {statistics.mean(r['laser_dmg'] for r in res):.0f}  "
          f"causes {dict(collections.Counter(r['cause'] for r in res))}")
    if a.v:
        for r in res:
            print(json.dumps({k: v for k, v in r.items() if k != "first_error"}))
    errs = [r for r in res if r.get("first_error")]
    if errs:
        print("FIRST ERROR:\n", errs[0]["first_error"])


if __name__ == "__main__":
    main()
