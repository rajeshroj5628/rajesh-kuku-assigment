"""Check a reference ("gold") file against the real screenplay boundaries BEFORE it is used.

    python eval/check_gold.py eval/gold_user.json --scripts "<folder>"

For every pick: is the start on a beat start and the end on a beat end (within TOL seconds)? If not, shows the nearest
real boundaries so a human can decide whether to correct the pick. Never edits the file: gold must stay as the person wrote it."""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promofinder.parse import fmt, load_show  # noqa: E402
from promofinder.textutil import content_beats, parse_ts  # noqa: E402

TOL = 0.6
ap = argparse.ArgumentParser()
ap.add_argument("gold")
ap.add_argument("--scripts", required=True)
a = ap.parse_args()
eps = {e.number: e for e in load_show(a.scripts)}
gold = json.load(open(a.gold, encoding="utf-8"))["promos"]
bad = 0
for g in gold:
    ep = eps.get(g["episode"])
    s, e = parse_ts(g["start"]), parse_ts(g["end"])
    if ep is None or s is None or e is None:
        print(f"Ep{g['episode']}: UNREADABLE pick {g}")
        bad += 1
        continue
    cb = content_beats(ep)
    bs = min(cb, key=lambda b: abs(b.start - s))
    be = min(cb, key=lambda b: abs(b.end - e))
    ds, de = abs(bs.start - s), abs(be.end - e)
    inside = 0 <= s < e <= ep.duration + 0.5
    ok = ds <= TOL and de <= TOL and inside
    bad += not ok
    print(f"Ep{g['episode']:>2} {g['start']:>8}-{g['end']:<8} ({e - s:4.0f}s) "
          f"start->{fmt(bs.start)} (off {ds:.1f}s) end->{fmt(be.end)} (off {de:.1f}s) "
          f"{'OK' if ok else 'CHECK'}{'' if inside else '  OUTSIDE EPISODE'}")
print(f"\n{len(gold) - bad}/{len(gold)} picks sit on real beat boundaries (tolerance {TOL}s)")
