"""Validate mode.ModeDetector on APPROXIMATE squad (practice-like) and FFA (finals-like) matches.

    python sim/validate_mode.py -n 48
Requirement before the detector may drive behaviour: 0 false FFA calls in squad matches.
"""
import argparse
import multiprocessing as mp
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)


def one(args):
    seed, ffa = args
    import contextlib
    import io
    import brain
    import house
    import mode
    import sim
    det = mode.ModeDetector()

    def factory(hello):
        b = brain.Bot(hello)
        orig = b.act

        def act(obs):
            det.update(obs)
            return orig(obs)

        b.act = act
        return b

    with contextlib.redirect_stderr(io.StringIO()):
        m = sim.Match(seed, factory, house.make_factory(seed))
        m.ffa = ffa
        r = m.run()
    others_dead_by_us = r["kills"]
    return {"seed": seed, "ffa": ffa, "called": det.mode, "t": det.decided_t, "reason": det.reason,
            "end_t": r["t"], "kills": others_dead_by_us, "score": r["score"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=48)
    ap.add_argument("-j", type=int, default=16)
    a = ap.parse_args()
    jobs = [(1000 + i, False) for i in range(a.n)] + [(2000 + i, True) for i in range(a.n)]
    with mp.Pool(a.j) as pool:
        res = pool.map(one, jobs)
    sq = [r for r in res if not r["ffa"]]
    ff = [r for r in res if r["ffa"]]
    fp = [r for r in sq if r["called"] == "ffa"]
    tp = [r for r in ff if r["called"] == "ffa"]
    print(f"squad matches: {len(sq)}  false FFA calls: {len(fp)}")
    for r in fp:
        print("  FALSE POSITIVE", r)
    ts = sorted(r["t"] for r in tp)
    print(f"FFA matches:   {len(ff)}  detected: {len(tp)}  median detect t: {ts[len(ts)//2] if ts else '-'}  "
          f"(our median end t {sorted(r['end_t'] for r in ff)[len(ff)//2]})")
    missed = [r for r in ff if r["called"] != "ffa"]
    print(f"missed in FFA: {len(missed)} (of which we died before 60 s: {sum(1 for r in missed if r['end_t'] < 60)})")


if __name__ == "__main__":
    main()
