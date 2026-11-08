"""Editor calibration examples: a few clips a human editor chose to post, shown to the judge so it learns the taste.

The file (examples/editor_picks.json) holds only timestamps and a short note, never script text. The clip text is
pulled from the show being run at run time. `show_hint` is the start of Episode 1's summary: if the show being run
is a different one, the examples are silently skipped (their timestamps would point at the wrong scenes).
"""
from __future__ import annotations

import json
import os

from .textutil import parse_ts, transcript


def load(path: str, eps: dict) -> str:
    if not path or not os.path.isfile(path) or not eps:
        return ""
    d = json.load(open(path, encoding="utf-8"))
    hint = d.get("show_hint", "")
    first = eps[min(eps)]
    if hint and not (first.logline or "").startswith(hint):
        return ""
    parts = []
    for i, ex in enumerate(d.get("examples", []), 1):
        ep, s, e = eps.get(ex["episode"]), parse_ts(ex["start"]), parse_ts(ex["end"])
        if ep is None or s is None or e is None:
            continue
        parts.append(f"EXAMPLE {i} - a clip our editor chose to post ({ex.get('note', '')}):\n{transcript(ep, s, e)}")
    return ("WHAT OUR EDITOR LIKES - study these before judging:\n" + "\n\n".join(parts) + "\n\n") if parts else ""
