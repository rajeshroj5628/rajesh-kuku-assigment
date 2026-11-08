"""Step 6 - safety flags and ranking.

Ranking = composite score for everything, then head-to-head LLM comparison among the top M
eligible promos. Each pair is judged in BOTH orders to cancel position bias; if the two
verdicts disagree the pair is a tie and falls back to the composite score.
Promos with a blocking flag (default: sexual) never enter the "post first" list.
"""
from __future__ import annotations

import functools
import re

from . import scoring
from .parse import Episode
from .textutil import transcript

# Rule-based flags back up the LLM's own content_flags (code, not trust).
FLAG_RULES = {
    "sexual": r"\b(shirtless|naked|bare chest|bare skin|carnal|seductive|intimate(?:ly)?\b.*\b(bed|embrace)|forbidden entanglement|\bbed\b.*\b(lies|rests))",
    "torture": r"\b(red-hot|branding|brand(?:ed)?\b.*\biron|searing iron|glowing tip)",
    "graphic_violence": r"\b(riding crop|whip|lacerat\w+|slicing through flesh|bloody gashes|strangl\w+)",
}


def rule_flags(eps: dict[int, Episode], promo: dict) -> list[str]:
    blob = []
    for s in promo["segments"]:
        ep = eps[s["episode"]]
        blob.append(" ".join(b.text for b in ep.beats
                             if b.kind != "camera" and b.end > s["start"] and b.start < s["end"]))
    text = " ".join(blob)
    return [f for f, rx in FLAG_RULES.items() if re.search(rx, text, re.I)]


CMP_SYS = ("You are the head of social for a short-drama app deciding which promo to post FIRST. "
           "Prefer: a stronger first-3-second hook, a more unresolved cliffhanger, and a clip a "
           "stranger fully understands. Return ONLY JSON.")


def _clip(eps, p) -> str:
    return "\n".join(f"-- seg {i} --\n{transcript(eps[s['episode']], s['start'], s['end'])}"
                     for i, s in enumerate(p["segments"], 1))


def _ask(llm, eps, a, b) -> str:
    user = (f"PROMO A:\n{_clip(eps, a)}\n\nPROMO B:\n{_clip(eps, b)}\n\n"
            'Which should be posted first? Return JSON {"winner": "A" or "B", "reason": "<one sentence>"}')
    try:
        r = llm.complete_json(CMP_SYS, user, task="compare")
        w = str(r.get("winner", "")).strip().upper()
    except Exception:                      # model failure -> no verdict, caller treats as tie
        return None
    return "A" if w.startswith("A") else "B" if w.startswith("B") else None


def rank(llm, eps, promos: list[dict], top_m: int = 10, block_flags=("sexual",)):
    for p in promos:
        p["flags"] = sorted(set(p.get("flags", [])) | set(rule_flags(eps, p)))
        p["blocked"] = bool(set(p["flags"]) & set(block_flags))
        # stranger-test result nudges the composite: a clip that needs context ranks lower
        su = p.get("stranger_after")
        p["final_score"] = scoring.final(p)

    eligible = sorted([p for p in promos if not p["blocked"]], key=lambda p: -p["final_score"])
    top, rest = eligible[:top_m], eligible[top_m:]
    wins = {id(p): 0 for p in top}

    def cmp(a, b):
        r1 = _ask(llm, eps, a, b)             # a shown as A
        r2 = _ask(llm, eps, b, a)             # a shown as B
        if r1 is None or r2 is None:          # missing verdict -> tie -> fall back to score
            return -1 if a.get("final_score", 0) >= b.get("final_score", 0) else 1
        a_wins = (r1 == "A") + (r2 == "B")
        if a_wins == 2:
            wins[id(a)] += 1
            return -1
        if a_wins == 0:
            wins[id(b)] += 1
            return 1
        return -1 if a.get("final_score", 0) >= b.get("final_score", 0) else 1   # disagreement -> tie

    top = sorted(top, key=functools.cmp_to_key(cmp))
    ordered = top + rest + sorted([p for p in promos if p["blocked"]], key=lambda p: -p["final_score"])
    for i, p in enumerate(ordered, 1):
        p["rank"] = i
        p["rank_basis"] = ("head-to-head (both orders)" if p in top else
                           "blocked from top list (content flag)" if p["blocked"] else "score only")
    return ordered
