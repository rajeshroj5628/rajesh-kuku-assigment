"""Label-free quality report for ONE run. It needs no human-picked clips, so it works on a show nobody has labelled.

Everything here is measured from the run itself: is every shown line a real line of its clip, does the clip open on its hook and stop
on its cliffhanger, did the model's own judge stay consistent, did any model call fail. Thresholds are rules of thumb, shown next to
each number, so a reader can disagree with them. A "warn" means look at this before trusting the ranking.
"""
from __future__ import annotations

import json
import os

from . import strict
from .textutil import quote_in
from .validate import total_seconds


def compute(eps: dict, ordered: list[dict], picked: list[dict], dropped: list, failures: list, tstats: dict,
            input_summary: dict, min_s: float, max_s: float) -> dict:
    n = max(1, len(ordered))
    pos = [strict.positions(eps, p) for p in ordered]
    hooks = [x["hook_offset"] for x in pos if x["hook_offset"] is not None]
    cliffs = [x["cliff_gap"] for x in pos if x["cliff_gap"] is not None]
    spos = [strict.positions(eps, p) for p in picked]
    fields = [s for p in ordered for s in (p.get("quote_status") or {}).values()]
    corrected = sum(1 for s in fields if s == "corrected")
    pulled = sum(1 for s in fields if s in ("extended", "trimmed"))
    durs = [total_seconds(p) for p in ordered]
    st = [p["stranger_after"] for p in ordered if p.get("stranger_after") is not None]
    pairs = max(1, tstats.get("pairs", 0))
    split = tstats.get("split", 0) / pairs if tstats.get("pairs") else None
    said_a = tstats.get("said_A", 0) / max(1, tstats.get("answers", 0)) if tstats.get("answers") else None
    q = {
        "episodes": len(eps), "episodes_with_a_clip": len({p["episode"] for p in ordered}), "clips": len(ordered), "slate": len(picked),
        "dropped_by_validators": len(dropped), "model_call_failures": len(failures),
        "clips_within_length": round(sum(1 for d in durs if min_s - 0.5 <= d <= max_s + 0.5) / n, 3),
        "quote_fields": len(fields), "quotes_ok": len(fields) - corrected - pulled, "quotes_pulled_in": pulled, "quotes_replaced": corrected,
        "hook_within_3s_all": round(sum(1 for h in hooks if h <= 3.0) / max(1, len(hooks)), 3),
        "ends_on_cliffhanger_all": round(sum(1 for c in cliffs if c <= 3.0) / max(1, len(cliffs)), 3),
        "hook_within_3s_slate": round(sum(1 for x in spos if x["hook_offset"] is not None and x["hook_offset"] <= 3.0) / max(1, len(picked)), 3),
        "ends_on_cliffhanger_slate": round(sum(1 for x in spos if x["cliff_gap"] is not None and x["cliff_gap"] <= 3.0) / max(1, len(picked)), 3),
        "flagged_clips": sum(1 for p in ordered if p.get("flags")), "slate_distinct_episodes": len({p["episode"] for p in picked}),
        "stranger_mean": round(sum(st) / len(st), 2) if st else None, "stranger_tested": len(st),
        "judge_split_rate": None if split is None else round(split, 3), "judge_said_A": None if said_a is None else round(said_a, 3),
        "input": input_summary,
    }
    # every shown hook and cliffhanger line must be a real line of ITS clip (any segment: a clip may start with a short setup segment)
    def in_clip(p, line):
        return any(quote_in(eps[s['episode']], [s], line) for s in p['segments'])
    q['shown_quotes_outside_clip'] = sum(1 for p in ordered if not (in_clip(p, p['hook_line']) and in_clip(p, p['cliffhanger_line'])))
    W = []   # (level, text)
    W.append(("ok" if not failures else "warn", f"model calls: {len(failures)} failed" + ("" if not failures else " - rerun to retry (cached calls are free)")))
    W.append(("ok" if q['shown_quotes_outside_clip'] == 0 else "warn", f"shown hook/cliffhanger lines that are not in their clip: {q['shown_quotes_outside_clip']} of {len(ordered)} clips"))
    W.append(("ok" if corrected / max(1, len(fields)) <= 0.10 else "warn",
              f"model's quoted lines: {q['quotes_ok']} right, {pulled} pulled into the clip, {corrected} replaced by the clip's real line "
              f"({100 * corrected / max(1, len(fields)):.0f}% replaced; rule of thumb: warn above 10%)"))
    W.append(("ok" if q["hook_within_3s_all"] >= 0.95 else "warn", f"clips that open on their hook line within 3 s: {100 * q['hook_within_3s_all']:.0f}% (warn below 95%)"))
    W.append(("ok" if q["ends_on_cliffhanger_all"] >= 0.75 else "warn", f"clips that end on their cliffhanger line (within 3 s): {100 * q['ends_on_cliffhanger_all']:.0f}% (warn below 75%)"))
    if split is not None:
        W.append(("ok" if split <= 0.35 else "warn", f"head-to-head judge consistency: {100 * split:.0f}% of pairs got different answers in the two orders "
                  f"(warn above 35%: ranking across episodes is then unreliable)"))
    if said_a is not None:
        W.append(("ok" if 0.35 <= said_a <= 0.65 else "warn", f"position bias of the judge: it answered 'A' {100 * said_a:.0f}% of the time (50% = none)"))
    if st:
        W.append(("ok" if sum(st) / len(st) >= 3.5 else "warn", f"stranger test (does a newcomer follow the clip?): mean {sum(st) / len(st):.1f}/5 over {len(st)} clips (warn below 3.5)"))
    W.append(("ok" if len(picked) else "warn", f"slate: {len(picked)} clips from {q['slate_distinct_episodes']} different episodes (at most 2 per episode)"))
    W.append(("ok" if q["episodes_with_a_clip"] == len(eps) else "warn", f"{q['episodes_with_a_clip']} of {len(eps)} episodes produced at least one clip"))
    inp = input_summary
    if inp.get("dialogue_beats") and inp["with_tone"] < 0.5 * inp["dialogue_beats"]:
        W.append(("warn", "most dialogue lines have no (tone) label: the rules funnel is weaker on this show, the model carries more of the choice"))
    if inp.get("timing") and set(inp["timing"]) - {"screenplay"}:
        W.append(("warn", f"timestamps came from {inp['timing']} (not the screenplay): 'estimated' times are built from the order and length of the lines, so clip times are approximate"))
    if inp.get("untimed_blocks"):
        W.append(("warn", f"{inp['untimed_blocks']} script blocks had no timestamp and were not used"))
    q["health"] = [{"level": a, "text": b} for a, b in W]
    return q


def write(out_dir: str, q: dict):
    with open(os.path.join(out_dir, "quality.json"), "w", encoding="utf-8") as f:
        json.dump(q, f, indent=1)
    lines = ["# Run quality (no human labels needed)", "",
             "Measured from this run alone. Thresholds are rules of thumb; a warning means look before trusting the ranking.", ""]
    lines += [f"- {'OK  ' if h['level'] == 'ok' else 'WARN'} {h['text']}" for h in q["health"]]
    with open(os.path.join(out_dir, "quality.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
