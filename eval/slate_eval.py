"""Does the slate contain the clips a human would post?

    python eval/slate_eval.py --run out [--vs out_v1]

For each reference set (human picks): how many are covered (IoU >= 0.5) by a slate clip, compared with the
plain top-N of the ranked list. Also prints slate facts: episodes covered, spoiler levels, joins, type variety.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eval.evaluate import iou  # noqa: E402
from promofinder.textutil import parse_ts  # noqa: E402


def covered(g, clips):
    g0, g1 = parse_ts(g["start"]), parse_ts(g["end"])
    for p in clips:
        for s in p["segments"]:
            if s["episode"] == g["episode"] and iou(g0, g1, s["start"], s["end"]) >= 0.5:
                return True
    return False


def load(run, name):
    return json.load(open(os.path.join(run, name), encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--vs", default=None, help="older run to compare plain top-N against")
    a = ap.parse_args()
    promos = load(a.run, "promos.json")
    slate = load(a.run, "slate.json")
    n = len(slate)
    byrank = sorted([p for p in promos if not p["blocked"]], key=lambda p: p["rank"])
    plain = byrank[:n]
    print(f"slate: {n} clips from {len({p['episode'] for p in slate})} episodes; "
          f"spoiler levels {[p.get('spoiler_level') for p in slate]}; joined {sum(1 for p in slate if p.get('joined_previous'))}; "
          f"hook types {len({p['hook_type'] for p in slate})}, cliff types {len({p['cliffhanger_type'] for p in slate})}")
    print(f"flagged clips in slate: {sum(1 for p in slate if p['flags'])} ; in plain top-{n}: {sum(1 for p in plain if p['flags'])}")
    print(f"clips from one episode more than once - slate: {n - len({p['episode'] for p in slate})}, plain top-{n}: {n - len({p['episode'] for p in plain})}")
    for fn in ("gold_user.json", "gold_builder.json"):
        gold = json.load(open(os.path.join("eval", fn), encoding="utf-8"))["promos"]
        print(f"{fn}: human picks covered by slate {sum(covered(g, slate) for g in gold)}/{len(gold)}"
              f" | by plain top-{n} {sum(covered(g, plain) for g in gold)}/{len(gold)}"
              f" | by top-{n} of all (incl. flagged) {sum(covered(g, sorted(promos, key=lambda p: p['rank'])[:n]) for g in gold)}/{len(gold)}")
    if a.vs:
        old = sorted([p for p in load(a.vs, "promos.json") if not p["blocked"]], key=lambda p: p["rank"])[:n]
        for fn in ("gold_user.json", "gold_builder.json"):
            gold = json.load(open(os.path.join("eval", fn), encoding="utf-8"))["promos"]
            print(f"{fn}: older run ({a.vs}) plain top-{n}: {sum(covered(g, old) for g in gold)}/{len(gold)}")


if __name__ == "__main__":
    main()
