"""Step 3 - LLM pairing: for each rules-funnel window, name the hook and the cliffhanger.

One call per episode. The model sees the whole episode (so it understands the scene)
plus the numbered candidate windows, and may also propose up to 2 windows the rules
missed (the rules are blind to scenes with little dialogue, e.g. action sequences).
"""
from __future__ import annotations

from .candidates import Candidate
from . import scoring
from .parse import Episode, fmt
from .textutil import transcript, parse_ts

BRIEF = """You are a senior short-drama promo editor (ReelShort / DramaBox style).
A PROMO is a 20-60 second clip cut from the show itself to pull NEW viewers in on
Instagram / TikTok / YouTube. A good promo:
 1. HOOK - in its first 3 seconds something makes a scrolling viewer stop (a slap, a reveal,
    a shocking line, a threat, a sudden arrival).
 2. CLIFFHANGER - it cuts at an unresolved moment so the viewer must know what happens next
    (a held threat, an arrival, an unanswered question, a twist line).
 3. STANDALONE - it makes sense to someone who has never seen the show.
You only choose moments and cut points. No editing, music or text overlays."""

JSON_SPEC = """Return ONLY JSON of this shape:
{
 "candidates": [
  {"id": <int>,
   "hook_line": "<verbatim line or action from the first 3s of the window, copied exactly from the transcript>",
   "hook_type": "slap|violence|reveal|accusation|threat|arrival|shock_line|other",
   "hook_strength": <1-5>,
   "cliffhanger_line": "<verbatim last line/beat>",
   "cliffhanger_type": "held_threat|arrival|question|twist_line|explosion|cut_off|resolved|other",
   "cliff_strength": <1-5>,
   "standalone_strength": <1-5, would a stranger follow it?>,
   "why": "<ONE sentence: why it works or why it doesn't>",
   "content_flags": [<any of "sexual","graphic_violence","torture","self_harm">],
   "trim_start": "<mm:ss or null - a better start if the window starts mid-thought>",
   "trim_end": "<mm:ss or null - a better end if it should cut earlier, at the unresolved moment>"}
 ],
 "extra_windows": [  // 0-2 strong promos the candidate list missed; same fields plus start/end
  {"start": "<mm:ss>", "end": "<mm:ss>", ...same fields without id...}
 ]
}
Rules: quote hook_line and cliffhanger_line EXACTLY from the transcript (do not paraphrase or invent).
Score honestly - most windows are mediocre; 5 is rare. Every window must stay between 20 and 60 seconds
(total) after any trim. Use timestamps that appear in the transcript."""


def _cand_lines(cands: list[Candidate]) -> str:
    return "\n".join(f"- id={i} start={fmt(c.start)} end={fmt(c.end)} ({c.duration:.0f}s)"
                     for i, c in enumerate(cands))


def judge_episode(llm, ep: Episode, cands: list[Candidate]) -> list[dict]:
    user = (f"EPISODE {ep.number} (length {fmt(ep.duration)})\n"
            f"Logline: {ep.logline}\nContinuity: {ep.continuity}\n\n"
            f"TRANSCRIPT (each line starts with its timestamp):\n{transcript(ep)}\n\n"
            f"CANDIDATE WINDOWS:\n{_cand_lines(cands)}\n\n{JSON_SPEC}")
    res = llm.complete_json(BRIEF, user, task="judge")
    if isinstance(res, list):                       # model returned a bare list of candidates
        res = {"candidates": res}
    if not isinstance(res, dict):
        raise ValueError("judge response was not a JSON object")
    promos: list[dict] = []
    byid = {int(r.get("id", -1)): r for r in (res.get("candidates") or []) if isinstance(r, dict)}
    for i, c in enumerate(cands):
        r = byid.get(i)
        if r is None:
            continue
        s, e = c.start, c.end
        ts, te = parse_ts(r.get("trim_start")), parse_ts(r.get("trim_end"))
        if ts is not None and c.start - 5 <= ts < c.end - 10:
            s = ts
        if te is not None and s + 15 < te <= c.end + 5:
            e = te
        promos.append(_mk(ep.number, s, e, r, "rules+llm", c.score))
    for r in (res.get("extra_windows") or [])[:2]:
        if not isinstance(r, dict):
            continue
        s, e = parse_ts(r.get("start")), parse_ts(r.get("end"))
        if s is not None and e is not None and e > s:
            promos.append(_mk(ep.number, s, e, r, "llm_extra", 0.0))
    return promos


def _num(v, lo=1, hi=5, default=1):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _mk(epn, s, e, r, source, rule_score) -> dict:
    hook, cliff, stand = (_num(r.get("hook_strength")), _num(r.get("cliff_strength")),
                          _num(r.get("standalone_strength")))
    return {
        "episode": epn,
        "segments": [{"episode": epn, "start": s, "end": e}],
        "hook_line": str(r.get("hook_line") or ""), "hook_type": r.get("hook_type") or "",
        "cliffhanger_line": str(r.get("cliffhanger_line") or ""),
        "cliffhanger_type": r.get("cliffhanger_type") or "",
        "why": str(r.get("why") or ""),
        "flags": [f for f in (r.get("content_flags") or []) if isinstance(f, str)],
        "scores": {"hook": hook, "cliff": cliff, "standalone": stand, "rules": round(rule_score, 2)},
        # composite: LLM judgement dominates; rule score is a small tie-breaker
        "composite": scoring.composite(hook, cliff, stand, rule_score),
        "source": source, "notes": [],
    }
