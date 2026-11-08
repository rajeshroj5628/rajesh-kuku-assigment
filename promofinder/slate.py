"""Step 7 - from a ranked list to a SLATE an editor can post.

A flat ranking of 376 clips is not a plan. The slate is a short, ordered list that
  * skips clips with sensitive-content flags (they go to a 'held back' list for a human),
  * is checked for SPOILERS: a viewer who clicks a promo starts at Episode 1, so a clip that
    gives away a late twist wastes the story (cheap LLM check, shortlist only),
  * may JOIN an earlier episode's ending as setup when the clip alone confuses a stranger
    (the brief allows joins across episodes),
  * is DIVERSE: at most one clip per episode, no run of the same hook or cliffhanger type.
Code does the selection; the LLM only answers two narrow questions (spoiler level, does a
setup segment help).
"""
from __future__ import annotations

import json
import os

from . import stranger, strict
from .parse import Episode, fmt
from .textutil import parse_ts, snap, transcript
from .validate import check, overlap_ratio, total_seconds

SPOILER_SYS = ("You protect a short-drama's plot for NEW viewers. A new viewer who clicks a promo "
               "starts at Episode 1 and knows nothing else. Return ONLY JSON.")
SKIP_FLAGS = {"sexual", "torture", "graphic_violence", "self_harm"}


def _pmap(fn, items, workers):
    from .pipeline import _pmap as pm
    return pm(fn, items, workers, lambda *_: None, "slate")


def spoiler_check(llm, eps: dict[int, Episode], p: dict, first_ep: Episode) -> dict:
    """0 = fine for a newcomer, 1 = reveals a notable plot point, 2 = gives away a major twist."""
    if p["episode"] <= 3:
        return {"level": 0, "reason": "early episode"}
    clip = stranger._clip_text(eps, p)
    early = " ".join(f"[Ep{n}] {eps[n].logline}" for n in sorted(eps)[:3] if eps[n].logline)
    user = (f"THE SHOW'S OPENING (what a new viewer learns in the first three episodes): {early}\n\n"
            f"PROMO CLIP (from episode {p['episode']}):\n{clip}\n\n"
            "Does this clip give away a plot point a new viewer should discover by watching from Episode 1? "
            "Anything already clear from the opening above is NOT a spoiler. Characters you were not told about "
            "are simply new characters, not proof that nothing is revealed. "
            'Return JSON {"level": 0|1|2, "reason": "<one short sentence>"} where '
            "0 = nothing important is spoiled, 1 = reveals a notable plot point, 2 = reveals a major twist "
            "(a hidden identity, a death, who the villain really is, how the story ends).")
    r = llm.complete_json(SPOILER_SYS, user, task="spoiler")
    if isinstance(r, list):
        r = next((x for x in r if isinstance(x, dict)), {})
    try:
        lvl = int(max(0, min(2, float(r.get("level", 0)))))
    except (TypeError, ValueError):
        lvl = 0
    return {"level": lvl, "reason": str(r.get("reason", ""))[:200]}


def try_join_previous(llm, eps: dict[int, Episode], p: dict, max_s: float) -> bool:
    """Prepend a short ending of the PREVIOUS episode as setup, if a stranger then understands more."""
    n = p["segments"][0]["episode"]
    prev = eps.get(n - 1)
    room = max_s - total_seconds(p)
    if prev is None or room < 5 or p["segments"][0]["start"] > 0.35 * eps[n].duration and not p.get("stranger_missing"):
        return False
    budget = min(15.0, room)
    t0 = max(0.0, prev.duration - 45)
    user = (f"A stranger watching this promo clip was confused.\nMissing context: {p.get('stranger_missing', '')}\n\n"
            f"END OF THE PREVIOUS EPISODE (Episode {n - 1}):\n{transcript(prev, t0, prev.duration + 1)}\n\n"
            f"CLIP (Episode {n}):\n{stranger._clip_text(eps, p)}\n\n"
            f"Pick ONE short segment from the previous episode's ending (at most {budget:.0f} seconds) that supplies "
            'exactly the missing context. Use its timestamps. Return JSON {"start": "<mm:ss>", "end": "<mm:ss>"} '
            'or {"start": null, "end": null} if nothing there helps.')
    r = llm.complete_json("You are a precise promo editor. Return ONLY JSON.", user, task="join")
    if isinstance(r, list):
        r = next((x for x in r if isinstance(x, dict)), {})
    s, e = parse_ts(r.get("start")), parse_ts(r.get("end"))
    if s is None or e is None or e <= s or (e - s) > budget + 1:
        return False
    sn = snap(prev, s, e)
    if not sn or (sn[1] - sn[0]) < 3 or (sn[1] - sn[0]) > budget + 1:
        return False
    seg = {"episode": n - 1, "start": sn[0], "end": sn[1]}
    trial = dict(p, segments=[seg] + [dict(x) for x in p["segments"]])
    if check(trial, eps, 20.0, max_s):
        return False
    after = stranger.test(llm, eps, trial)["understandable"]
    before = p.get("stranger_after") or 0
    if after < before + 1:                       # must clearly help, otherwise keep the clean cut
        return False
    p["segments"] = trial["segments"]
    p["stranger_before_join"] = before
    p["stranger_after"] = after
    p["joined_previous"] = True
    p["notes"].append(f"joined: Ep{n - 1} {fmt(sn[0])}-{fmt(sn[1])} added as setup (stranger {before} -> {after})")
    return True


def shortlist(promos: list[dict], n: int, max_per_ep: int = 2) -> list[dict]:
    """The n best clips of the WHOLE show, wherever they are: by the model's own score for the clip (the composite, which does
    not include the stranger-test bonus, so a clip is not favoured just because it happened to be tested first), at most
    `max_per_ep` per episode, a second clip of an episode must not overlap the first, and sensitive clips are left out."""
    ok = [p for p in promos if not p["blocked"] and not set(p["flags"]) & SKIP_FLAGS]
    ok.sort(key=lambda p: -p["composite"])
    out, cnt = [], {}
    for p in ok:
        if cnt.get(p["episode"], 0) >= max_per_ep:
            continue
        if any(q["episode"] == p["episode"] and overlap_ratio(p, q) > 0.2 for q in out):
            continue
        out.append(p)
        cnt[p["episode"]] = cnt.get(p["episode"], 0) + 1
        if len(out) >= n:
            break
    return out


def select(pool: list[dict], size: int, penal: float, tw: float = 0.5, max_per_ep: int = 1) -> list[dict]:
    """Greedy, diverse choice: best adjusted score first; at most `max_per_ep` clips per episode (and never two that overlap);
    mild penalties for neighbouring episodes and for repeating a hook / cliffhanger type. Pure code, no model calls.
    `tw` = weight of the head-to-head tournament record against the absolute score (0..1)."""
    def adj(p):
        base = p["final_score"]
        if p.get("tourney_strength") is not None:
            base = tw * p["tourney_strength"] + (1 - tw) * base
        return base - penal * (p.get("spoiler_level") or 0)
    picked, used_h, used_c = [], {}, {}
    rest = sorted(pool, key=lambda p: -adj(p))
    while rest and len(picked) < size:
        def val(p):
            near = sum(1 for q in picked if q["episode"] != p["episode"] and abs(q["episode"] - p["episode"]) <= 1)
            same = sum(1 for q in picked if q["episode"] == p["episode"])
            return adj(p) - 0.3 * near - 0.4 * same - 0.15 * used_h.get(p["hook_type"], 0) - 0.1 * used_c.get(p["cliffhanger_type"], 0)
        cand = [p for p in rest if sum(1 for q in picked if q["episode"] == p["episode"]) < max_per_ep
                and not any(q["episode"] == p["episode"] and overlap_ratio(p, q) > 0.2 for q in picked)]
        if not cand:
            break
        best = max(cand, key=val)
        picked.append(best); rest.remove(best)
        used_h[best["hook_type"]] = used_h.get(best["hook_type"], 0) + 1
        used_c[best["cliffhanger_type"]] = used_c.get(best["cliffhanger_type"], 0) + 1
    return picked


def best_first(picked: list[dict]) -> list[dict]:
    """The slate is shown in the order you would post it: best clip first, by the overall rank. (The variety rules in `select` decide WHICH
    twelve clips are chosen; they must not decide the order they are shown in.)"""
    return sorted(picked, key=lambda p: (p.get("rank", 10 ** 9), -p.get("final_score", 0)))


def reorder(ordered: list[dict], pool: list[dict], tw: float) -> list[dict]:
    """Overall ranking after the tournament: the finalists come first, ordered by their head-to-head record blended with
    the absolute score; everyone else keeps the previous order; clips held back for content stay last.
    The previous rank is kept in `rank_before_tournament` so the change can be measured."""
    for p in ordered:
        p.setdefault("rank_before_tournament", p["rank"])
    def strength(p):
        return tw * p["tourney_strength"] + (1 - tw) * p["final_score"]
    champs = sorted(pool, key=lambda p: -strength(p))
    ids = {id(p) for p in champs}
    rest = [p for p in ordered if id(p) not in ids and not p["blocked"]]
    blocked = [p for p in ordered if id(p) not in ids and p["blocked"]]
    new = champs + rest + blocked
    for i, p in enumerate(new, 1):
        p["rank"] = i
        if id(p) in ids:
            p["rank_basis"] = "finalist, head-to-head tournament"
    return new


def build(llm, eps: dict[int, Episode], ordered: list[dict], cfg: dict, max_s: float, workers: int, log):
    sc = cfg.get("slate", {})
    size, n_pool = int(sc.get("size", 12)), int(sc.get("candidates", 60))
    max_per_ep = int(sc.get("max_per_episode", 2))
    penal = float(sc.get("spoiler_penalty", 0.0))
    first_ep = eps[min(eps)]
    # Finalists = the best clips of the whole show (up to max_per_episode from one episode); every one of them was
    # stranger-tested and now meets the others head to head.
    pool = shortlist(ordered, n_pool, max_per_ep)
    held = [p for p in ordered if set(p["flags"]) & SKIP_FLAGS or p["blocked"]]
    log(f"[7] finalists: {len(pool)} clips from {len({p['episode'] for p in pool})} of {len(eps)} episodes (at most {max_per_ep} per episode)")

    # 1) head-to-head Swiss tournament between the finalists (optionally judged by a different model)
    tc = sc.get("tournament", {})
    tw = float(tc.get("weight", 0.5))
    if tc.get("enabled", True) and len(pool) >= 4:
        from . import tournament
        tllm = llm
        # a second model for this round only makes sense on the same provider: if the main provider is switched (for example to an
        # endpoint Kuku provides), the main model judges this round too, instead of sending a foreign model name to the wrong server
        if tc.get("model") and cfg["llm"]["provider"] == tc.get("provider", cfg["llm"]["provider"]):
            from .llm import LLM
            tllm = LLM({**cfg["llm"], "model": tc["model"]})
        rounds = int(tc.get("rounds", 4))
        tournament.swiss(tllm, eps, pool, rounds, workers, log)
        if tllm is not llm:
            for k in ("calls", "cache_hits", "prompt_tokens", "output_tokens"):
                llm.stats[k] = llm.stats.get(k, 0) + tllm.stats.get(k, 0)
            llm.stats["tournament_model"] = tc["model"]
        log(f"[7] tournament: {len(pool)} finalists, {rounds} rounds, both orders, model {(tllm.model)}")
    if tc.get("enabled", True) and all(p.get("tourney_strength") is not None for p in pool):
        ordered = reorder(ordered, pool, tw)                 # the overall ranking now uses the tournament too

    # 2) choose the slate, then label and polish only the clips we actually recommend
    picked = best_first(select(pool, size, penal, tw, max_per_ep))
    res = _pmap(lambda i: spoiler_check(llm, eps, picked[i], first_ep), list(range(len(picked))), workers)
    for i, p in enumerate(picked):
        r = res[i]
        if isinstance(r, Exception):
            p["spoiler_level"], p["spoiler_reason"] = None, "check failed"
        else:
            p["spoiler_level"], p["spoiler_reason"] = r["level"], r["reason"]
    log(f"[7] spoiler labels for the slate: {sum(1 for p in picked if p.get('spoiler_level') == 2)} major, "
        f"{sum(1 for p in picked if p.get('spoiler_level') == 1)} notable")
    need = [p for p in picked if p["episode"] >= 2 and (p.get("stranger_after") or 5) < 4.0]
    jr = _pmap(lambda i: try_join_previous(llm, eps, need[i], max_s), list(range(len(need))), workers)
    log(f"[7] tried joining the previous episode for {len(need)} unclear slate clips: {sum(1 for x in jr.values() if x is True)} improved")
    min_s = float(cfg["window"]["min_seconds"])
    for i, p in enumerate(picked, 1):
        p["slate_rank"] = i
        p["land_on_episode"] = sc.get("landing_episode", 1)
        p["notes"] += strict.tighten(eps, p, min_s)          # open ON the hook, stop ON the cliffhanger
    log(f"[7] slate: {len(picked)} promos from {len({p['episode'] for p in picked})} episodes")
    return picked, held, ordered


def write(out_dir: str, picked: list[dict], held: list[dict]):
    keep = ("slate_rank", "episode", "segments", "hook_line", "hook_type", "cliffhanger_line", "cliffhanger_type",
            "why", "final_score", "rank", "stranger_after", "spoiler_level", "spoiler_reason",
            "joined_previous", "land_on_episode", "notes", "flags",
            "tourney_strength", "tourney_points", "tourney_games", "rank_before_tournament")
    with open(os.path.join(out_dir, "slate.json"), "w", encoding="utf-8") as f:
        json.dump([{k: p.get(k) for k in keep} for p in picked], f, indent=1)
    L = ["# Recommended slate", "",
         "Post in this order. Every clip should link to the start of the series (new viewers begin at Episode 1).", ""]
    for p in picked:
        segs = " + ".join(f"Ep{s['episode']} {fmt(s['start'])}-{fmt(s['end'])}" for s in p["segments"])
        L += [f"**{p['slate_rank']}. {segs}** ({total_seconds(p):.0f}s) - overall rank #{p['rank']}",
              f"- Hook: \"{p['hook_line']}\"", f"- Cliffhanger: \"{p['cliffhanger_line']}\"", f"- Why: {p['why']}",
              f"- Spoiler risk: {['none', 'notable', 'MAJOR'][p.get('spoiler_level') or 0]}"
              + (f" ({p['spoiler_reason']})" if p.get("spoiler_reason") else "")
              + (" | joined with the previous episode's ending as setup" if p.get("joined_previous") else ""), ""]
    if held:
        L += ["## Held back for a human decision (sensitive-content flags)", ""]
        L += [f"- Ep{p['episode']} {fmt(p['segments'][0]['start'])}-{fmt(p['segments'][-1]['end'])}: {', '.join(p['flags'])}"
              for p in held[:15]]
    with open(os.path.join(out_dir, "slate.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
