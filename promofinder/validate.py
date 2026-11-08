"""Step 5 - code-enforced rules. The LLM proposes; these checks decide what is allowed.

A promo is a dict:
  {"segments":[{"episode":int,"start":float,"end":float}, ...], ...}
"""
from __future__ import annotations

from .parse import Episode
from .textutil import snap

SLACK = 0.5


def total_seconds(p: dict) -> float:
    return sum(s["end"] - s["start"] for s in p["segments"])


def check(p: dict, eps: dict[int, Episode], min_s=20.0, max_s=60.0):
    """Return list of problems (empty list = valid)."""
    problems = []
    segs = p.get("segments") or []
    if not segs:
        return ["no segments"]
    prev = None
    for i, s in enumerate(segs):
        ep = eps.get(s["episode"])
        if ep is None:
            problems.append(f"segment {i}: episode {s['episode']} not found")
            continue
        if not (0 <= s["start"] < s["end"] <= ep.duration + SLACK):
            problems.append(f"segment {i}: timestamps outside episode (0-{ep.duration:.0f}s)")
        if prev and (s["episode"], s["start"]) < (prev["episode"], prev["end"]):
            problems.append(f"segment {i}: out of order or overlapping previous segment")
        prev = s
    tot = total_seconds(p)
    if tot < min_s - SLACK or tot > max_s + SLACK:
        problems.append(f"total length {tot:.1f}s outside {min_s:.0f}-{max_s:.0f}s")
    return problems


def snap_promo(p: dict, eps: dict[int, Episode]) -> bool:
    """Snap every segment to real beat boundaries. False if any segment can't be snapped."""
    for s in p["segments"]:
        ep = eps.get(s["episode"])
        r = snap(ep, s["start"], s["end"]) if ep else None
        if r is None:
            return False
        s["start"], s["end"] = r
    return True


def _iou(a: dict, b: dict) -> float:
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    union = (a["end"] - a["start"]) + (b["end"] - b["start"]) - inter
    return inter / union if union else 0.0


def overlap_ratio(p: dict, q: dict) -> float:
    best = 0.0
    for a in p["segments"]:
        for b in q["segments"]:
            if a["episode"] == b["episode"]:
                best = max(best, _iou(a, b))
    return best


def dedupe(promos: list[dict], key: str = "composite", thr: float = 0.5):
    kept = []
    for p in sorted(promos, key=lambda x: -x.get(key, 0)):
        if all(overlap_ratio(p, k) <= thr for k in kept):
            kept.append(p)
    return kept
