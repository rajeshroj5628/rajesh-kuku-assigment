"""Offline ablation of the slate (no model calls: spoiler levels and tournament records are stored in promos.json).

    python eval/slate_ablation.py --run out

Varies the tournament weight and the spoiler penalty; shows how many human-picked clips each slate covers.
NOTE: the reference sets are small (15 clips) and were also used to look at these numbers, so treat any gain as optimistic."""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eval.slate_eval import covered  # noqa: E402
from promofinder.slate import select  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--run", required=True)
a = ap.parse_args()
promos = json.load(open(os.path.join(a.run, "promos.json"), encoding="utf-8"))
golds = {fn: json.load(open(os.path.join("eval", fn), encoding="utf-8"))["promos"] for fn in ("gold_user.json", "gold_builder.json")}
pool = [p for p in promos if p.get("tourney_strength") is not None]
print(f"pool: {len(pool)} episode champions that played the tournament")
print(f"{'tourney weight':>14} {'spoiler penalty':>16} | covers user 8 / builder 7 | major spoilers in slate | mean overall rank")
for tw in (0.0, 0.5, 0.8, 1.0):
    for pen in (0.0, 0.4):
        s = select(pool, 12, pen, tw)
        cov = [sum(covered(g, s) for g in gs) for gs in golds.values()]
        print(f"{tw:>14} {pen:>16} | {cov[0]}/8  {cov[1]}/7 | {sum(1 for p in s if p.get('spoiler_level') == 2):>3} | {sum(p['rank'] for p in s) / len(s):6.1f}")
