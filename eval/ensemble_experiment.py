"""Do two different models agree about which episode champions are best? And does averaging them help?

    python eval/ensemble_experiment.py --run out --scripts "<folder>" [--model-b gemini-3.1-flash-lite]

Runs the same Swiss tournament (30 champions, both orders) with a second model. Label-free reliability:
 - Spearman rank correlation between the two models' strengths, and how many of the top 10 they share;
 - each model's own split rate (pairs where its two orders disagree) and position bias.
Then, as a sanity check only (the reference clips are dev sets): where the human-picked clips sit under model A, model B
and their average. Does not modify the run folder."""
import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaml  # noqa: E402
from eval.slate_eval import covered  # noqa: E402
from promofinder import tournament  # noqa: E402
from promofinder.llm import LLM  # noqa: E402
from promofinder.parse import load_show  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--run", required=True)
ap.add_argument("--scripts", required=True)
ap.add_argument("--model-b", default="gemini-3.1-flash-lite")
ap.add_argument("--rounds", type=int, default=5)
a = ap.parse_args()

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
llm = LLM({**cfg["llm"], "model": a.model_b})
eps = {e.number: e for e in load_show(a.scripts)}
promos = json.load(open(os.path.join(a.run, "promos.json"), encoding="utf-8"))
key = lambda p: (p["episode"], p["segments"][0]["start"])
A = {key(p): p["tourney_strength"] for p in promos if p.get("tourney_strength") is not None}
pool = copy.deepcopy([p for p in promos if p.get("tourney_strength") is not None])
for p in pool:
    for k in ("tourney_points", "tourney_games", "tourney_strength"):
        p.pop(k, None)
stats = {}
tournament.swiss(llm, eps, pool, a.rounds, 5, lambda *_: None, None, stats)
B = {key(p): p["tourney_strength"] for p in pool}
ks = sorted(A)


def ranks(d):
    order = sorted(ks, key=lambda k: -d[k])
    avg = {}
    i = 0
    while i < len(order):                       # average ranks for ties
        j = i
        while j + 1 < len(order) and d[order[j + 1]] == d[order[i]]:
            j += 1
        for t in range(i, j + 1):
            avg[order[t]] = (i + j) / 2 + 1
        i = j + 1
    return avg


ra, rb = ranks(A), ranks(B)
n = len(ks)
mean_a, mean_b = sum(ra.values()) / n, sum(rb.values()) / n
cov = sum((ra[k] - mean_a) * (rb[k] - mean_b) for k in ks)
rho = cov / ((sum((ra[k] - mean_a) ** 2 for k in ks) * sum((rb[k] - mean_b) ** 2 for k in ks)) ** 0.5)
top = lambda d: set(sorted(ks, key=lambda k: -d[k])[:10])
print(f"model B = {a.model_b}: pairs {stats.get('pairs')}, split {stats.get('split', 0)}/{stats.get('pairs')} = {100 * stats.get('split', 0) / max(1, stats.get('pairs', 1)):.0f}%, "
      f"said 'A' {100 * stats.get('said_A', 0) / max(1, stats.get('answers', 1)):.0f}%")
print(f"agreement between the two models: Spearman rho = {rho:.2f}; top-10 shared = {len(top(A) & top(B))}/10; requests: new {llm.stats['calls']}, cached {llm.stats['cache_hits']}")
gold = json.load(open(os.path.join("eval", "gold_user.json"), encoding="utf-8"))["promos"]
by_key = {key(p): p for p in promos}
for name, d in (("model A (3.5)", A), (f"model B ({a.model_b})", B), ("average A+B", {k: (A[k] + B[k]) / 2 for k in ks})):
    order = sorted(ks, key=lambda k: -d[k])
    pos = sorted(i + 1 for i, k in enumerate(order) if any(covered(g, [by_key[k]]) for g in gold))
    print(f"  {name:<34} dev picks at positions {pos} of {n}  (in top 10: {sum(1 for x in pos if x <= 10)})")
json.dump({f"{k[0]}:{k[1]}": v for k, v in B.items()}, open(os.path.join(os.environ.get("TEMP", "."), "tourney_B.json"), "w"))
