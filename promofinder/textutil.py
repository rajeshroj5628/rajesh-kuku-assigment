"""Small helpers: timestamps, transcripts, snapping to beat boundaries."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .parse import Episode, fmt


def parse_ts(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.match(r"^\s*(\d+):(\d+(?:\.\d+)?)\s*$", str(s))
    return int(m.group(1)) * 60 + float(m.group(2)) if m else None


def fmt_mmss(t: float) -> str:
    return fmt(t)


def content_beats(ep: Episode):
    return [b for b in ep.beats if b.kind != "camera"]


def transcript(ep: Episode, t0: float = 0.0, t1: float | None = None, camera=False) -> str:
    """Compact, timestamped text of a time range. Camera lines are dropped by default
    (they overlap the dialogue and cost tokens) but kept if camera=True."""
    t1 = ep.duration + 1 if t1 is None else t1
    out = []
    for b in ep.beats:
        if b.kind == "camera" and not camera:
            continue
        if b.end <= t0 or b.start >= t1:
            continue
        tag = b.label if b.kind == "dialogue" else b.kind.upper()
        tone = f" ({b.tone})" if b.tone and b.kind == "dialogue" else ""
        out.append(f"[{fmt(b.start)}] {tag}{tone}: {b.text}")
    return "\n".join(out)


def snap(ep: Episode, start: float, end: float, tol: float = 4.0):
    """Snap a requested [start,end] to the nearest content-beat boundaries.
    Returns (start, end) or None if nothing within tolerance."""
    cb = content_beats(ep)
    if not cb:
        return None
    s_b = min(cb, key=lambda b: abs(b.start - start))
    e_b = min(cb, key=lambda b: abs(b.end - end))
    if abs(s_b.start - start) > tol or abs(e_b.end - end) > tol:
        return None
    if e_b.end <= s_b.start:
        return None
    return s_b.start, e_b.end


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", s.lower().replace("’", "'")).strip()


LABEL_RE = re.compile(r"^\s*\[?\d*:?\d*\.?\d*\]?\s*[A-Z][A-Z0-9 '.\-/]*(\s*\([^)]*\))*\s*:\s*")


def strip_label(quote: str) -> str:
    """Models often copy the transcript's "SPEAKER (tone): " / "ACTION: " prefix - drop it."""
    return LABEL_RE.sub("", quote or "")


def _toks(s: str) -> list[str]:
    return norm(s).split()


def _score(q: list[str], t: list[str]) -> float:
    """How much of the quote (as tokens, in order) is in this beat's text. 1.0 = the quote appears verbatim."""
    n = len(q)
    for i in range(len(t) - n + 1):
        if t[i:i + n] == q:
            return 1.0
    if n < 4:                                   # very short lines ("Yes.", "No!") must match exactly
        return 0.0
    blocks = SequenceMatcher(None, q, t, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks) / n


def quote_beat(ep: Episode, quote: str, lo: float, hi: float, thr: float = 0.85):
    """The content beat inside [lo, hi] that carries the quoted line, or None.
    Token-aware: a one-word line matches only a beat that really says that word."""
    q = _toks(strip_label(quote))
    if not q:
        return None
    best, best_s = None, 0.0
    for b in content_beats(ep):
        if b.end <= lo or b.start >= hi:
            continue
        t = _toks(b.text)
        if not t:
            continue
        s = _score(q, t)
        if s > best_s:
            best, best_s = b, s
    return best if best_s >= (1.0 if len(q) < 4 else thr) else None


def quote_in(ep: Episode, segs: list[dict], quote: str) -> bool:
    """Grounding check: is the quoted line really in the clip?"""
    return any(quote_beat(ep, quote, s["start"] - 0.01, s["end"] + 0.01) is not None for s in segs)
