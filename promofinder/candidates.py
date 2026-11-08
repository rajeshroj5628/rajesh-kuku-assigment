"""Step 2 - rules funnel.

Generate every 20-60s window whose start and end sit on script beat boundaries,
score it with cheap, explainable rules for hook / cliffhanger / cleanliness, and
keep the top K non-overlapping windows per episode. No LLM is used here.

Scores are heuristics for *pre-filtering only*; the LLM stages decide quality.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parse import Episode, Beat, fmt

# ---- lexicons (kept small and generic so they transfer to other shows) -----
VIOLENCE = r"\b(slap|slaps|strikes?|struck|punch|shoves?|tackl\w+|drags?|dragged|hurls?|flings?|grabs?|seiz\w+|snatch\w+|crash\w+|shatter\w+|explo\w+|stab\w*|shoots?|shot|whip|kicks?|collapses|chains?|lunges?|burst\w*|slams?)\b"
REVEAL = r"\b(truth|secret|dead|alive|poison\w*|husband|wife|marry|marrying|married|pregnan\w+|baby|betray\w*|lied|lie|liar|knew|framed|imposter|impostor|divorce\w*|affair|cheat\w*|fired|arrest\w*|police|heir|inherit\w*|fake|engaged|engagement|wedding|ex-?(husband|wife|boyfriend|girlfriend)|identity|blood|murder\w*|kidnap\w*)\b"
THREAT = r"\b(kill|die|dead|burn|destroy|never|last chance|regret|pay for|or else|will not|won't|ruin|expose|leave you|take everything)\b"
SHOUT_TONE = r"(shout|scream|roar|furious|enraged|hysteric|thunder|boom|gasp|shriek|panick|bellow|barked)"
MENACE_TONE = r"(ominous|menac|chill|sinister|deadly|cold|dangerous|threat|whisper)"
SOFT_END = r"\b(thank you|goodbye|good night|rest now|sleep|safe tonight|peace|home|together)\b"
CLIFF_SFX = r"(cliffhanger|crescendo|sting|swell|boom|drone|explo|door|latch|knock|footstep|bell|gunshot|scream|silence|cut to black|cuts? to black)"
CONTINUATION = r"^(\.\.\.|and\b|but\b|so\b|then\b|because\b|or\b|which\b|who\b)"
PRONOUN_START = r"^(he|she|they|it|him|her|them|his)\b"


@dataclass
class Candidate:
    episode: int
    start: float
    end: float
    first: int           # first beat idx
    last: int            # last beat idx
    score: float
    parts: dict = field(default_factory=dict)

    @property
    def duration(self):
        return self.end - self.start

    def as_row(self):
        return {"episode": self.episode, "start": fmt(self.start), "end": fmt(self.end),
                "duration_s": round(self.duration, 1), "score": round(self.score, 2),
                **{f"s_{k}": round(v, 2) for k, v in self.parts.items()
                   if isinstance(v, (int, float))}}


def _content(beats: list[Beat]) -> list[Beat]:
    """Beats that carry story content (camera lines overlap them in time)."""
    return [b for b in beats if b.kind != "camera"]


def _hook_score(win: list[Beat], t0: float) -> tuple[float, list[str]]:
    s, why = 0.0, []
    early = [b for b in win if b.start < t0 + 3.0] or win[:1]
    first = early[0]
    txt = first.text
    words = len(txt.split())
    if first.kind == "dialogue":
        if words <= 8:
            s += 1.0; why.append("short opener")
        if "!" in txt:
            s += 1.0; why.append("exclamation")
        if "?" in txt and words <= 12:
            s += 0.4
        if re.search(SHOUT_TONE, first.tone, re.I):
            s += 1.0; why.append("shouted")
        if re.search(THREAT, txt, re.I):
            s += 0.7; why.append("threat")
        if re.match(CONTINUATION, txt.strip(), re.I) or txt.strip().startswith("..."):
            s -= 2.0; why.append("continuation opener")
        if re.match(PRONOUN_START, txt.strip(), re.I):
            s -= 0.8; why.append("pronoun opener")
        if re.search(r"continu", first.tone, re.I):
            s -= 1.5
    for b in early:
        blob = b.text
        if b.kind == "action" and re.search(VIOLENCE, blob, re.I):
            s += 1.6; why.append("violent action")
        if re.search(REVEAL, blob, re.I):
            s += 0.8; why.append("reveal words")
        if b.kind == "sound" and re.search(r"(explo|crash|slam|gasp|scream|crack)", blob, re.I):
            s += 0.8; why.append("loud sfx")
    return s, why


def _cliff_score(win: list[Beat], ep_end: float, t1: float) -> tuple[float, list[str]]:
    s, why = 0.0, []
    tail = [b for b in win if b.end > t1 - 5.0] or win[-1:]
    last_dlg = next((b for b in reversed(tail) if b.kind == "dialogue"), None)
    if t1 >= ep_end - 3.0:
        s += 2.0; why.append("episode ending")
    if last_dlg:
        txt = last_dlg.text.strip()
        if txt.endswith("?"):
            s += 1.5; why.append("ends on question")
        if txt.endswith("...") or txt.endswith("-") or txt.endswith("—"):
            s += 1.2; why.append("trailing off")
        if re.search(THREAT, txt, re.I):
            s += 0.8
        if re.search(MENACE_TONE, last_dlg.tone, re.I):
            s += 0.6
        if re.search(SOFT_END, txt, re.I):
            s -= 1.2; why.append("resolved ending")
    for b in tail:
        if b.kind == "sound" and re.search(CLIFF_SFX, b.text, re.I):
            s += 1.0; why.append("cliff sfx")
        if b.kind == "action" and re.search(VIOLENCE, b.text, re.I):
            s += 0.6
        if b.kind == "action" and re.search(r"(fade|soft|peaceful|warm|smile|embrace)", b.text, re.I):
            s -= 0.8; why.append("soft ending")
    return s, why


def _clean_score(win: list[Beat], b_first: Beat, b_last: Beat, ep: Episode) -> tuple[float, list[str]]:
    s, why = 0.0, []
    scene_starts = {sc["start"] for sc in ep.scenes if sc["start"] is not None}
    if any(abs(b_first.start - t) < 0.6 for t in scene_starts):
        s += 0.6; why.append("starts at scene start")
    # dead air inside the window
    prev_end = win[0].end
    gap = 0.0
    for b in win[1:]:
        gap = max(gap, b.start - prev_end)
        prev_end = max(prev_end, b.end)
    if gap > 6.0:
        s -= 1.5; why.append(f"dead air {gap:.0f}s")
    # density: want dialogue/action to move
    dlg = sum(len(b.text.split()) for b in win if b.kind == "dialogue")
    dur = max(1.0, win[-1].end - win[0].start)
    wps = dlg / dur
    if wps < 0.8:
        s -= 0.8; why.append("sparse dialogue")
    elif wps > 1.5:
        s += 0.3
    # many scene changes = more context needed
    scenes = {b.scene for b in win}
    if len(scenes) > 3:
        s -= 0.6; why.append("many scene changes")
    return s, why


def generate(ep: Episode, min_s=20.0, max_s=60.0, top_k=12, overlap_iou=0.5):
    cb = _content(ep.beats)
    cands: list[Candidate] = []
    for i, bi in enumerate(cb):
        for j in range(i, len(cb)):
            bj = cb[j]
            dur = bj.end - bi.start
            if dur > max_s:
                break
            if dur < min_s:
                continue
            win = cb[i:j + 1]
            h, hw = _hook_score(win, bi.start)
            c, cw = _cliff_score(win, ep.duration, bj.end)
            k, kw = _clean_score(win, bi, bj, ep)
            total = 1.0 * h + 1.2 * c + 0.8 * k
            cands.append(Candidate(ep.number, bi.start, bj.end, bi.idx, bj.idx, total,
                                   {"hook": h, "cliff": c, "clean": k,
                                    "why": hw + cw + kw}))
    # greedy non-maximum suppression by time overlap
    cands.sort(key=lambda c: c.score, reverse=True)
    kept: list[Candidate] = []
    for c in cands:
        ok = True
        for k in kept:
            inter = max(0.0, min(c.end, k.end) - max(c.start, k.start))
            union = (c.end - c.start) + (k.end - k.start) - inter
            if union > 0 and inter / union > overlap_iou:
                ok = False
                break
        if ok:
            kept.append(c)
        if len(kept) >= top_k:
            break
    return kept
