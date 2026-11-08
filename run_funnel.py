"""Run steps 1-2 (parse + rules funnel) and report recall against the gold set.

    python run_funnel.py --input "<folder with episode .txt files>"
"""
import argparse
import csv
import json
import os

from promofinder.parse import load_show, fmt
from promofinder.candidates import generate


def mmss(s: str) -> float:
    m, sec = s.split(":")
    return int(m) * 60 + float(sec)


def iou(a0, a1, b0, b1):
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = (a1 - a0) + (b1 - b0) - inter
    return inter / union if union else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--top-k", type=int, default=12)
    ap.add_argument("--min", type=float, default=20)
    ap.add_argument("--max", type=float, default=60)
    ap.add_argument("--gold", default=os.path.join("eval", "gold.json"))
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    eps = load_show(a.input)
    n_beats = sum(len(e.beats) for e in eps)
    print(f"Parsed {len(eps)} episodes, {n_beats} beats "
          f"(durations {min(e.duration for e in eps):.0f}-{max(e.duration for e in eps):.0f}s)")

    rows, per_ep = [], {}
    for e in eps:
        cands = generate(e, a.min, a.max, a.top_k)
        per_ep[e.number] = cands
        for rank, c in enumerate(cands, 1):
            r = c.as_row(); r["rank_in_episode"] = rank
            r["why"] = "; ".join(c.parts.get("why", []))
            rows.append(r)

    path = os.path.join(a.out, "candidates.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"Wrote {len(rows)} candidates -> {path}  "
          f"({len(rows)/len(eps):.1f} per episode)")

    # --- recall against the gold set -------------------------------------
    if os.path.exists(a.gold):
        gold = json.load(open(a.gold, encoding="utf-8"))["promos"]
        print("\nGold-set recall of the rules funnel (best IoU among kept candidates):")
        hits = 0
        for g in gold:
            g0, g1 = mmss(g["start"]), mmss(g["end"])
            best, best_rank = 0.0, None
            for c in per_ep.get(g["episode"], []):
                v = iou(g0, g1, c.start, c.end)
                if v > best:
                    best, best_rank = v, c
            ok = best >= 0.5
            hits += ok
            where = f"{fmt(best_rank.start)}-{fmt(best_rank.end)}" if best_rank else "-"
            print(f"  Ep{g['episode']:>2} gold {g['start']}-{g['end']}  best IoU {best:.2f}  "
                  f"({where})  {'HIT' if ok else 'miss'}")
        print(f"  -> {hits}/{len(gold)} gold promos covered (IoU>=0.5)")


if __name__ == "__main__":
    main()
