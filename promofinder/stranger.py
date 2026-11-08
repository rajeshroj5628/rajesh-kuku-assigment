"""Step 4 - the "stranger test" and one repair attempt.

A second model call sees ONLY the clip text (no logline, no earlier episodes) - exactly what
a new viewer would have - and must say who is who, what the conflict is, and whether it is
understandable. If not, and there is room under 60s, we ask for a short earlier setup
segment from the same episode and test again. One repair loop only.
"""
from __future__ import annotations

from .parse import Episode, fmt
from .textutil import transcript, snap
from .validate import total_seconds

SYS = ("You are a first-time viewer scrolling past a short-drama promo. You know NOTHING about "
       "this show. Judge honestly from the clip alone. Return ONLY JSON.")


def _clip_text(ep, promo: dict) -> str:
    """`ep` is one Episode, or a {number: Episode} dict when a promo joins several episodes."""
    parts = []
    for i, s in enumerate(promo["segments"], 1):
        e = ep[s["episode"]] if isinstance(ep, dict) else ep
        tag = f" (Episode {s['episode']})" if isinstance(ep, dict) and len({x['episode'] for x in promo['segments']}) > 1 else ""
        parts.append(f"--- SEGMENT {i}{tag} ---\n{transcript(e, s['start'], s['end'])}")
    return "\n".join(parts)


def test(llm, ep: Episode, promo: dict) -> dict:
    user = (f"CLIP (this is everything you get to see):\n{_clip_text(ep, promo)}\n\n"
            'Return JSON: {"who": "<who the main people are, by role, as far as the clip tells you>", '
            '"conflict": "<what is happening / at stake>", '
            '"understandable": <1-5, 5 = a stranger fully follows it>, '
            '"missing_context": "<the specific thing a stranger would not know, or empty>", '
            '"can_be_fixed_with_earlier_lines": <true/false>}')
    r = llm.complete_json(SYS, user, task="stranger")
    if isinstance(r, list):          # model sometimes wraps the answer in a list
        r = next((x for x in r if isinstance(x, dict)), {})
    try:
        r["understandable"] = max(1, min(5, float(r.get("understandable", 1))))
    except (TypeError, ValueError):
        r["understandable"] = 1.0
    return r


def repair(llm, ep: Episode, promo: dict, verdict: dict, max_s: float):
    """Try to prepend a short setup segment from earlier in the same episode.
    Returns True if the promo was changed."""
    room = max_s - total_seconds(promo)
    first = promo["segments"][0]
    if room < 4 or first["start"] < 3:
        return False
    budget = min(15.0, room)
    user = (f"A stranger watching this promo clip was confused.\nMissing context: "
            f"{verdict.get('missing_context', '')}\n\nEPISODE TEXT BEFORE THE CLIP:\n"
            f"{transcript(ep, 0, first['start'])}\n\nCLIP:\n{_clip_text(ep, promo)}\n\n"
            f"Pick ONE short setup segment from the episode text above (at most {budget:.0f} seconds) "
            'that supplies exactly the missing context. Use timestamps from the text. '
            'Return JSON {"start": "<mm:ss>", "end": "<mm:ss>"} or {"start": null, "end": null} '
            "if nothing earlier helps.")
    r = llm.complete_json("You are a precise promo editor. Return ONLY JSON.", user, task="repair")
    if isinstance(r, list):
        r = next((x for x in r if isinstance(x, dict)), {})
    from .textutil import parse_ts
    s, e = parse_ts(r.get("start")), parse_ts(r.get("end"))
    if s is None or e is None or e <= s or e > first["start"] + 0.01 or (e - s) > budget + 1:
        return False
    sn = snap(ep, s, e)
    if not sn or sn[1] > first["start"] + 0.01 or (sn[1] - sn[0]) < 3:
        return False
    promo["segments"].insert(0, {"episode": ep.number, "start": sn[0], "end": sn[1]})
    promo["notes"].append(f"repair: prepended setup {fmt(sn[0])}-{fmt(sn[1])}")
    return True


def run(llm, ep: Episode, promo: dict, max_s: float, threshold: float = 4.0):
    before = test(llm, ep, promo)
    promo["stranger_before"] = before["understandable"]
    promo["stranger_who"], promo["stranger_conflict"] = before.get("who", ""), before.get("conflict", "")
    promo["stranger_missing"] = before.get("missing_context", "")
    promo["stranger_after"] = before["understandable"]
    if before["understandable"] >= threshold:
        return
    if before.get("can_be_fixed_with_earlier_lines") and repair(llm, ep, promo, before, max_s):
        after = test(llm, ep, promo)
        promo["stranger_after"] = after["understandable"]
        promo["stranger_missing"] = after.get("missing_context", "")
        if after["understandable"] <= before["understandable"]:
            promo["segments"].pop(0)           # repair did not help - undo it
            promo["notes"].append("repair undone: no improvement")
            promo["stranger_after"] = before["understandable"]
    else:
        promo["notes"].append("cannot be made standalone with earlier lines of this episode")
