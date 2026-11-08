"""Evaluation metrics against a set of human-picked ("gold") clips. Used by eval/run_eval.py and by the web app.

MATCHING RULE (stated once, used everywhere): a tool clip matches a human clip when they are in the same episode and the
intersection-over-union (IoU) of their time spans is at least 0.5. Exact boundaries rarely agree (see eval/check_gold.py),
so overlap is the fair test. A clip made of several segments is compared segment by segment.

What each metric can and cannot say
- recall_any       : was the human clip found among ALL the clips the tool produced?
- recall@K         : was a matching clip inside the tool's overall top K? (the ranking test)
- MRR, median rank : rank of the best-ranked matching clip (1/rank averaged; median of ranks)
- in-episode       : is the best-ranked clip of that episode the human one (top-1), or in the top 3?
- slate precision  : of the clips the tool recommends, how many match a human pick. This is only a LOWER BOUND: humans picked clips
                     in a few episodes, so a good clip elsewhere counts as a miss. `precision_in_labelled_episodes` restricts it
                     to episodes where a human actually picked something.
Small sets (8 and 7 clips) mean every number is coarse: one clip moves recall@K by 12-14 points.
"""
from __future__ import annotations

import statistics

from promofinder.textutil import parse_ts


def iou(a0, a1, b0, b1):
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = (a1 - a0) + (b1 - b0) - inter
    return inter / union if union else 0.0


def overlap_with(g, p):
    """Best IoU between the human clip g and any segment of promo p in g's episode (0 if none)."""
    g0, g1 = parse_ts(g["start"]), parse_ts(g["end"])
    best = 0.0
    for s in p["segments"]:
        if s["episode"] == g["episode"]:
            best = max(best, iou(g0, g1, s["start"], s["end"]))
    return best


def evaluate(promos: list[dict], slate: list[dict], gold: list[dict], ks=(10, 12, 20), thr=0.5) -> dict:
    items, ranks = [], []
    for g in gold:
        scored = [(overlap_with(g, p), p) for p in promos]
        matches = [p for v, p in scored if v >= thr]
        best_v, best_p = max(scored, key=lambda x: x[0]) if scored else (0.0, None)
        same = sorted([p for p in promos if any(s["episode"] == g["episode"] for s in p["segments"]) and not p.get("blocked")], key=lambda p: p["rank"])
        champ = same[0] if same else None
        min_rank = min((p["rank"] for p in matches), default=None)
        ep_rank = min((same.index(p) + 1 for p in matches if p in same), default=None)
        if min_rank:
            ranks.append(min_rank)
        items.append({"episode": g["episode"], "note": g.get("note", ""), "gold": [parse_ts(g["start"]), parse_ts(g["end"])],
                      "iou": round(best_v, 2), "hit": best_v >= thr, "rank": min_rank, "ep_rank": ep_rank, "ep_total": len(same),
                      "tool": [best_p["segments"][0]["start"], best_p["segments"][-1]["end"]] if best_p and best_v > 0 else None,
                      "champion": ({"span": [champ["segments"][0]["start"], champ["segments"][-1]["end"]], "iou": round(overlap_with(g, champ), 2),
                                    "hook_line": champ["hook_line"], "rank": champ["rank"]} if champ else None)})
    n = max(1, len(gold))
    gold_eps = {g["episode"] for g in gold}
    slate_hits = [p for p in slate if any(overlap_with(g, p) >= thr for g in gold)]
    in_lab = [p for p in slate if p["episode"] in gold_eps]
    out = {
        "n": len(gold), "matching_rule": f"same episode and time-span IoU >= {thr}",
        "recall_any": sum(i["hit"] for i in items) / n,
        **{f"recall_at_{k}": sum(1 for i in items if i["rank"] and i["rank"] <= k) / n for k in ks},
        "mrr": round(sum(1 / r for r in ranks) / n, 3), "median_rank": statistics.median(ranks) if ranks else None,
        "in_episode_top1": sum(1 for i in items if i["ep_rank"] == 1) / n, "in_episode_top3": sum(1 for i in items if i["ep_rank"] and i["ep_rank"] <= 3) / n,
        "slate_size": len(slate), "slate_precision_lower_bound": round(len(slate_hits) / max(1, len(slate)), 3),
        "slate_matches": len(slate_hits),
        "precision_in_labelled_episodes": round(sum(1 for p in in_lab if p in slate_hits) / max(1, len(in_lab)), 3) if in_lab else None,
        "labelled_episodes_in_slate": len(in_lab), "items": items,
    }
    out["failures"] = {
        "not_in_top_12": [i for i in items if not i["rank"] or i["rank"] > 12][:6],
        "slate_clips_in_labelled_episodes_without_a_match": [
            {"slate_rank": p.get("slate_rank"), "episode": p["episode"], "span": [p["segments"][0]["start"], p["segments"][-1]["end"]],
             "hook_line": p["hook_line"]} for p in in_lab if p not in slate_hits][:6],
    }
    return out
