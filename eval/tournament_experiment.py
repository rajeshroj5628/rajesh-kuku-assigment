"""Compare ways of asking the head-to-head question, on the 30 episode champions in a finished run.

    python eval/tournament_experiment.py --run out --scripts "<folder>" --variant baseline|reason|reason_examples

Reports (a) how decisive and un-biased the judge is: split = the two orders disagreed (a draw), said_A = share of
answers that were 'A' (0.50 = no position bias), and (b) where the clips a human picked land among the 30 (lower
is better). Only the user's 8 picks are used for (b) because the examples come from episodes the user's set does
not contain. Does not modify the run folder."""
import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaml  # noqa: E402
from eval.slate_eval import covered  # noqa: E402
from promofinder import examples as ex_mod, tournament  # noqa: E402
from promofinder.llm import LLM  # noqa: E402
from promofinder.parse import load_show  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--run", required=True)
ap.add_argument("--scripts", required=True)
ap.add_argument("--variant", required=True, choices=["baseline", "reason", "reason_examples"])
ap.add_argument("--rounds", type=int, default=5)
a = ap.parse_args()

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
llm = LLM(cfg["llm"])
eps = {e.number: e for e in load_show(a.scripts)}
promos = json.load(open(os.path.join(a.run, "promos.json"), encoding="utf-8"))
pool = copy.deepcopy([p for p in promos if p.get("tourney_strength") is not None])
for p in pool:
    for k in ("tourney_points", "tourney_games", "tourney_strength"):
        p.pop(k, None)
examples = ex_mod.load(os.path.join("examples", "editor_picks.json"), eps) if a.variant == "reason_examples" else ""
if a.variant == "reason_examples" and not examples:
    sys.exit("examples were not loaded (wrong show?)")
ask = None
if a.variant != "baseline":
    ask = lambda x, y: tournament.ask_pair(llm, eps, x, y, examples, reason_first=True)
stats = {}
order = tournament.swiss(llm, eps, pool, a.rounds, 5, lambda *_: None, ask, stats)
gold = json.load(open(os.path.join("eval", "gold_user.json"), encoding="utf-8"))["promos"]
pos = sorted(i + 1 for i, p in enumerate(order) if any(covered(g, [p]) for g in gold))
n = max(1, stats.get("pairs", 1))
print(f"variant={a.variant}  pairs={stats.get('pairs')}  split(draw) {stats.get('split', 0)}/{n} = {100 * stats.get('split', 0) / n:.0f}%  "
      f"unknown {stats.get('unknown', 0)}  said_A {100 * stats.get('said_A', 0) / max(1, stats.get('answers', 1)):.0f}%")
print(f"  user's picks present in the pool: {len(pos)}; positions among {len(order)}: {pos}; mean {sum(pos) / max(1, len(pos)):.1f}")
print(f"  model requests this variant: new {llm.stats['calls']}, cached {llm.stats['cache_hits']}")
json.dump([{"episode": p["episode"], "rank": p["rank"], "strength": p["tourney_strength"]} for p in order],
          open(os.path.join(os.environ.get("TEMP", "."), f"tourney_{a.variant}.json"), "w"))
