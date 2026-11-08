"""Score a run against the gold set and print quality numbers for the solution doc.

    python eval/evaluate.py --run out --gold eval/gold.json [--baseline out_baseline]

Reports:
  - gold recall: for each gold promo, the best-overlapping promo the tool produced,
    its overlap (IoU), its rank, and whether its hook/cliffhanger text is grounded
  - precision-at-10: how many of the tool's top-10 overlap a gold promo (only meaningful
    if the gold set covers the whole show - otherwise read it as a lower bound)
  - validator / hallucination stats and stranger-test lift
"""
import argparse
import json
import os
import sys
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promofinder.textutil import parse_ts  # noqa: E402


def iou(a0, a1, b0, b1):
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = (a1 - a0) + (b1 - b0) - inter
    return inter / union if union else 0.0


def best_match(g, promos):
    g0, g1 = parse_ts(g["start"]), parse_ts(g["end"])
    best, bp = 0.0, None
    for p in promos:
        for s in p["segments"]:
            if s["episode"] == g["episode"]:
                v = iou(g0, g1, s["start"], s["end"])
                if v > best:
                    best, bp = v, p
    return best, bp


def load(run):
    return json.load(open(os.path.join(run, "promos.json"), encoding="utf-8"))


def report(run, gold, label):
    promos = load(run)
    print(f"\n=== {label}: {run} ({len(promos)} promos) ===")
    hits = top10_hits = 0
    for g in gold:
        v, p = best_match(g, promos)
        ok = v >= 0.5
        hits += ok
        if p and p["rank"] <= 10:
            top10_hits += ok
        where = f"rank {p['rank']}" if p else "-"
        print(f"  Ep{g['episode']:>2} gold {g['start']}-{g['end']}  IoU {v:.2f}  {where:<8}"
              f"{'HIT' if ok else 'MISS'}  {('| ' + p['hook_line'][:50]) if p else ''}")
    print(f"  gold recall (IoU>=0.5): {hits}/{len(gold)}   of those in tool's top-10: {top10_hits}")

    top = [p for p in promos if p["rank"] <= 10]
    gold_eps = {g["episode"] for g in gold}
    on_gold = sum(1 for p in top if any(
        best_match(g, [p])[0] >= 0.3 for g in gold if g["episode"] == p["episode"]))
    print(f"  top-10 overlapping a gold promo (IoU>=0.3): {on_gold}/10 "
          f"(gold only covers eps {sorted(gold_eps)} - lower bound)")

    ungr = sum(1 for p in promos if not (p.get("grounded_hook") and p.get("grounded_cliff")))
    print(f"  model quote not in the clip (corrected to the clip's real line): {ungr}/{len(promos)}")
    st = [(p["stranger_before"], p["stranger_after"]) for p in promos if "stranger_before" in p]
    if st:
        fixed = sum(1 for b, a in st if a > b)
        print(f"  stranger-tested: {len(st)}  mean {mean(b for b, _ in st):.2f} -> "
              f"{mean(a for _, a in st):.2f}   improved by repair: {fixed}")
    flagged = [p for p in promos if p["flags"]]
    print(f"  content-flagged: {len(flagged)}  (blocked from top list: "
          f"{sum(1 for p in promos if p['blocked'])})")
    return hits


def frozen_role(gpath):
    """'dev' / 'heldout' if the file was frozen with eval/freeze_gold.py (and still matches its hash); refuse if it changed."""
    import hashlib
    reg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "GOLD_FREEZE.json")
    key = os.path.relpath(os.path.abspath(gpath), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).replace("\\", "/")
    reg = json.load(open(reg_path, encoding="utf-8")) if os.path.isfile(reg_path) else {}
    if key not in reg:
        return "not frozen"
    if hashlib.sha256(open(gpath, "rb").read()).hexdigest() != reg[key]["sha256"]:
        raise SystemExit(f"{gpath} changed after it was frozen on {reg[key]['frozen_at']}. Refusing to score a moved target.")
    return f"{reg[key]['role']}, frozen {reg[key]['frozen_at']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--gold", nargs="+",
                    default=[os.path.join("eval", "gold_user.json"),
                             os.path.join("eval", "gold_builder.json")],
                    help="one or more gold files; each is scored separately")
    ap.add_argument("--baseline", help="second run folder to compare (e.g. LLM scoring only)")
    a = ap.parse_args()
    for gpath in a.gold:
        if not os.path.exists(gpath):
            continue
        gold = json.load(open(gpath, encoding="utf-8"))["promos"]
        tag = os.path.basename(gpath).replace(".json", "")
        role = frozen_role(gpath)
        report(a.run, gold, f"FULL PIPELINE vs {tag}  [{role}]")
        if a.baseline:
            report(a.baseline, gold, f"BASELINE vs {tag}")


if __name__ == "__main__":
    main()
