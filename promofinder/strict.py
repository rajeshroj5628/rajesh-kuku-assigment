"""Strict validator for the quoted hook / cliffhanger lines. Pure code, no model calls.

The model quotes a hook line and a cliffhanger line. Before anything else trusts them we check, per clip:
  1. is the quoted line really in the clip?                       -> "ok"
  2. no, but it is a real line just outside it                    -> "extended" (or "trimmed" at the other end
                                                                      when the 60 s limit is hit)
  3. no, and it is not a real line anywhere near the clip         -> "corrected": the quote is replaced by the clip's
                                                                      real first / last line and the clip is flagged
After this every displayed hook and cliffhanger line is a real line of the clip, and the status says how we got there.

`tighten` (used on the final slate) then makes the clip open ON the hook (within 3 s) and end ON the cliffhanger.
"""
from __future__ import annotations

from .textutil import content_beats, quote_beat
from .validate import check, total_seconds

REACH = 12.0           # how far outside the clip we look for a line the model quoted


def line_text(b) -> str:
    if b.kind == "dialogue":
        return f"{b.label} ({b.tone}): {b.text}" if b.tone else f"{b.label}: {b.text}"
    return f"{b.kind.upper()}: {b.text}"


def _first_start_ge(cb, t):
    c = [b for b in cb if b.start >= t - 1e-6]
    return c[0].start if c else None


def _last_end_le(cb, t):
    c = [b for b in cb if b.end <= t + 1e-6]
    return c[-1].end if c else None


def _fit(ep, seg, b, keep_lo, keep_hi, min_s, max_s, other_total):
    """Return a new (start, end) that contains beat b and the other quoted beat's span [keep_lo, keep_hi],
    stays within max_s (trimming the far side if needed) and is >= min_s; or None."""
    cb = content_beats(ep)
    s, e = min(seg["start"], b.start), max(seg["end"], b.end)
    room = max_s - other_total
    if e - s > room + 0.5:                       # too long: trim the side away from the line we are adding
        if b.start >= seg["end"] - 1e-6:         # line is after the clip -> move the start forward
            s2 = _first_start_ge(cb, e - room)
            s = s2 if s2 is not None else s
        else:                                    # line is before the clip -> pull the end back
            e2 = _last_end_le(cb, s + room)
            e = e2 if e2 is not None else e
    if keep_lo is not None and (s > keep_lo + 1e-6 or e < keep_hi - 1e-6):
        return None                              # trimming would cut off the other quoted line
    if e - s > room + 0.5 or (e - s + other_total) < min_s - 0.5:
        return None
    return s, e


def fix(eps: dict, p: dict, min_s: float, max_s: float) -> dict:
    """Make hook_line / cliffhanger_line verifiable inside the clip. Returns {"hook": status, "cliff": status}."""
    status = {}
    for which, field, idx in (("hook", "hook_line", 0), ("cliff", "cliffhanger_line", -1)):
        seg = p["segments"][idx]
        ep = eps[seg["episode"]]
        if quote_beat(ep, p[field], seg["start"] - 0.01, seg["end"] + 0.01):
            status[which] = "ok"
            continue
        b = quote_beat(ep, p[field], seg["start"] - REACH, seg["end"] + REACH)
        done = None
        if b is not None:
            other = "cliffhanger_line" if which == "hook" else "hook_line"
            oseg = p["segments"][-1 if which == "hook" else 0]
            ob = quote_beat(eps[oseg["episode"]], p[other], oseg["start"] - 0.01, oseg["end"] + 0.01) if oseg is seg else None
            keep = (ob.start, ob.end) if ob is not None else (None, None)
            other_total = total_seconds(p) - (seg["end"] - seg["start"])
            fit = _fit(ep, seg, b, keep[0], keep[1], min_s, max_s, other_total)
            if fit:
                old = (seg["start"], seg["end"])
                seg["start"], seg["end"] = fit
                if check(p, eps, min_s, max_s):          # must still be a valid promo
                    seg["start"], seg["end"] = old
                else:
                    done = "extended" if (fit[0] <= old[0] and fit[1] >= old[1]) else "trimmed"
        if done is None:                                 # not a real line near the clip: use the clip's real one
            cb = [x for x in content_beats(ep) if x.end > seg["start"] and x.start < seg["end"] and x.text.strip()]
            real = (cb[0] if which == "hook" else cb[-1]) if cb else None
            if real is not None:
                p[field] = line_text(real)
            done = "corrected"
        status[which] = done
    p["quote_status"] = status
    return status


def tighten(eps: dict, p: dict, min_s: float, hook_within: float = 3.0, cliff_within: float = 3.0) -> list[str]:
    """Final polish for a clip we are about to recommend: open on the hook line, end on the cliffhanger line."""
    notes = []
    s0, s1 = p["segments"][0], p["segments"][-1]
    hb = quote_beat(eps[s0["episode"]], p["hook_line"], s0["start"] - 0.01, s0["end"] + 0.01)
    cbm = quote_beat(eps[s1["episode"]], p["cliffhanger_line"], s1["start"] - 0.01, s1["end"] + 0.01)
    if hb is not None and hb.start - s0["start"] > hook_within and total_seconds(p) - (hb.start - s0["start"]) >= min_s:
        notes.append(f"opening trimmed {hb.start - s0['start']:.1f}s so the clip opens on the hook")
        s0["start"] = hb.start
    if cbm is not None and s1["end"] - cbm.end > cliff_within and total_seconds(p) - (s1["end"] - cbm.end) >= min_s:
        notes.append(f"ending trimmed {s1['end'] - cbm.end:.1f}s so the clip stops on the cliffhanger")
        s1["end"] = cbm.end
    return notes


def positions(eps: dict, p: dict) -> dict:
    """Where do the quoted lines sit? hook_offset = seconds from clip start to the hook line; cliff_gap = seconds from
    the end of the cliffhanger line to the end of the clip. None when the line is not found in the clip."""
    s0, s1 = p["segments"][0], p["segments"][-1]
    hb = quote_beat(eps[s0["episode"]], p["hook_line"], s0["start"] - 0.01, s0["end"] + 0.01)
    cb = quote_beat(eps[s1["episode"]], p["cliffhanger_line"], s1["start"] - 0.01, s1["end"] + 0.01)
    return {"hook_offset": None if hb is None else round(max(0.0, hb.start - s0["start"]), 2),
            "cliff_gap": None if cb is None else round(max(0.0, s1["end"] - cb.end), 2)}
