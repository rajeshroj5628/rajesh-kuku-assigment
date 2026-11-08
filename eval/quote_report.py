"""Quote / timestamp accuracy of a run: how many displayed hook and cliffhanger lines are real lines of their clip,
how many had to be extended / trimmed / corrected, and where the lines sit (hook near the start, cliffhanger at the end).

    python eval/quote_report.py --run out --scripts "<folder>" [--before out_v1]
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promofinder import strict  # noqa: E402
from promofinder.parse import load_show  # noqa: E402
from promofinder.textutil import quote_in  # noqa: E402


def stats(run, eps):
    promos = json.load(open(os.path.join(run, "promos.json"), encoding="utf-8"))
    n = len(promos)
    # a clip may start with a short setup segment (stranger-test repair), so a line counts if it is in ANY segment of the clip
    def anyseg(p, line):
        return any(quote_in(eps[s["episode"]], [s], line) for s in p["segments"])
    in_clip = sum(1 for p in promos if anyseg(p, p["hook_line"]) and anyseg(p, p["cliffhanger_line"]))
    st = collections.Counter()
    for p in promos:
        for w, s in (p.get("quote_status") or {}).items():
            st[s] += 1
    pos = [strict.positions(eps, p) for p in promos]
    hooks = [x["hook_offset"] for x in pos if x["hook_offset"] is not None]
    cliffs = [x["cliff_gap"] for x in pos if x["cliff_gap"] is not None]
    return n, in_clip, st, hooks, cliffs, promos


ap = argparse.ArgumentParser()
ap.add_argument("--run", required=True)
ap.add_argument("--scripts", required=True)
ap.add_argument("--before", default=None)
a = ap.parse_args()
eps = {e.number: e for e in load_show(a.scripts)}
for label, run in ((("BEFORE (" + a.before + ")") if a.before else None, a.before), ("AFTER (" + a.run + ")", a.run)):
    if not run:
        continue
    n, ok, st, hooks, cliffs, promos = stats(run, eps)
    print(f"\n== {label}: {n} clips")
    print(f"  displayed hook AND cliffhanger line both inside the clip: {ok}/{n}  (not inside: {n - ok})")
    if st:
        print(f"  how the model's original quotes fared: {dict(st)}   (fields, 2 per clip)")
    print(f"  hook line starts within 3 s of the clip start: {sum(1 for h in hooks if h <= 3.0)}/{len(hooks)}  "
          f"| cliffhanger line ends within 3 s of the clip end: {sum(1 for c in cliffs if c <= 3.0)}/{len(cliffs)}")
sl = os.path.join(a.run, "slate.json")
if os.path.isfile(sl):
    slate = json.load(open(sl, encoding="utf-8"))
    pos = [strict.positions(eps, p) for p in slate]
    print(f"\n== slate ({len(slate)} clips): hook within 3 s: {sum(1 for x in pos if x['hook_offset'] is not None and x['hook_offset'] <= 3.0)}/{len(slate)}"
          f" | ends on the cliffhanger (<=3 s): {sum(1 for x in pos if x['cliff_gap'] is not None and x['cliff_gap'] <= 3.0)}/{len(slate)}")
