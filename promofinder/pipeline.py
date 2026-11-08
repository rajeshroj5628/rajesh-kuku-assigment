"""Orchestrates steps 1-6 and writes promos.csv / promos.json / report.md / run_log.json."""
from __future__ import annotations

import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .parse import load_show, fmt, summarize
from .candidates import generate
from .judge import judge_episode
from . import stranger, rank as rank_mod, slate as slate_mod
from . import scoring, strict, quality as quality_mod
from .validate import check, snap_promo, dedupe, total_seconds
from .llm import LLMError


def clip_id(p) -> str:
    """Stable id for a clip, usable as a tracking tag (utm_content): ep27_1-51s, or ep26_40-55s+ep27_0-30s when it joins episodes."""
    return "+".join(f"ep{s['episode']}_{int(s['start'])}-{int(round(s['end']))}s" for s in p["segments"])


def seg_str(p) -> str:
    return " | ".join(f"Ep{s['episode']} {fmt(s['start'])}-{fmt(s['end'])}" for s in p["segments"])


def _pmap(fn, items, workers, log, label):
    """Run fn(item) for every item on a thread pool. Returns {item: result_or_Exception}.
    The LLM adapter shares one rate limiter, so more workers never exceed the call budget."""
    out, done = {}, 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for f in as_completed(futs):
            it = futs[f]
            try:
                out[it] = f.result()
            except (LLMError, ValueError, KeyError, TypeError, AttributeError) as exc:
                out[it] = exc
            done += 1
            if done % 5 == 0 or done == len(items):
                log(f"    [{label}] {done}/{len(items)} done")
    return out


def run(cfg: dict, llm, input_dir: str, out_dir: str, only_eps=None, log=print):
    t0 = time.time()
    scoring.configure(cfg)
    os.makedirs(out_dir, exist_ok=True)
    w, fn, pl = cfg["window"], cfg["funnel"], cfg.get("pipeline", {})
    min_s, max_s = w["min_seconds"], w["max_seconds"]

    eps_list = load_show(input_dir, log)
    if only_eps:
        eps_list = [e for e in eps_list if e.number in only_eps]
    input_summary = summarize(eps_list)
    eps = {e.number: e for e in eps_list}
    log(f"[1] parsed {len(eps)} episodes: {input_summary['beats']} beats ({input_summary['dialogue_beats']} dialogue), "
        f"episodes {input_summary['episode_seconds'][0]}-{input_summary['episode_seconds'][1]} s")
    if input_summary["dialogue_beats"] and input_summary["with_tone"] < 0.5 * input_summary["dialogue_beats"]:
        log("[1] note: most dialogue lines have no (tone) label, so the rules funnel is weaker here; the model carries more of the choice")
    if set(input_summary["timing"]) - {"screenplay"}:
        log(f"[1] WARNING: timestamps are not from the screenplay for some episodes {input_summary['timing']}: "
            "'estimated' means they were built from the order and length of the lines, so every clip time is approximate")
    if input_summary["untimed_blocks"]:
        log(f"[1] note: {input_summary['untimed_blocks']} script blocks had no timestamp and were not used (counted, not hidden)")
    if input_summary["with_logline"] < 0.5 * len(eps):
        log("[1] note: few episodes have a logline; the spoiler check will have less context")

    # ---- step 2 + 3 + 5: funnel, judge, snap/validate --------------------------------
    promos, dropped, failures = [], [], []
    workers = int(pl.get("workers", 4))
    cand_by_ep = {n: generate(ep, min_s, max_s, fn["top_k_per_episode"], fn["overlap_iou"])
                  for n, ep in eps.items()}

    def _judge(n):
        return judge_episode(llm, eps[n], cand_by_ep[n])

    judged = _pmap(_judge, list(eps), workers, log, "judge")      # {n: list | Exception}
    for n, ep in eps.items():
        cands, got = cand_by_ep[n], judged[n]
        if isinstance(got, Exception):
            # one bad episode must not kill the whole run: log it, keep going
            failures.append({"stage": "judge", "episode": n, "error": str(got)[:200]})
            log(f"[2-5] Ep{n:>2}: JUDGE FAILED ({str(got)[:80]}) - skipped, rerun to retry")
            continue
        ok_n = 0
        for p in got:
            if not snap_promo(p, eps):
                dropped.append({"episode": n, "reason": "could not snap to beat boundaries",
                                "segments": seg_str(p)})
                continue
            problems = check(p, eps, min_s, max_s)
            if problems:
                dropped.append({"episode": n, "reason": "; ".join(problems), "segments": seg_str(p)})
                continue
            # strict validator: every displayed hook / cliffhanger line must be a real line of the clip.
            # A line just outside the clip is pulled in (extend, or trim the far side at the 60 s limit);
            # a line that is not real at all is replaced by the clip's real first / last line and flagged.
            st = strict.fix(eps, p, min_s, max_s)
            problems = check(p, eps, min_s, max_s)
            if problems:
                dropped.append({"episode": n, "reason": "; ".join(problems), "segments": seg_str(p)})
                continue
            p["grounded_hook"], p["grounded_cliff"] = st["hook"] != "corrected", st["cliff"] != "corrected"   # model's ORIGINAL quote verified?
            for which, s_ in st.items():
                if s_ in ("extended", "trimmed"):
                    p["notes"].append(f"{which} line was just outside the clip: clip {s_} to include it")
                elif s_ == "corrected":
                    p["notes"].append(f"{which} line quoted by the model is not in the clip: replaced by the clip's real line")
            if "corrected" in st.values():
                p["composite"] = round(p["composite"] - 0.5, 3)      # penalise, keep for review
            promos.append(p)
            ok_n += 1
        log(f"[2-5] Ep{n:>2}: {len(cands)} windows -> {len(got)} judged -> {ok_n} valid")

    promos = dedupe(promos)
    log(f"[5] {len(promos)} promos after validation + dedupe ({len(dropped)} dropped)")

    # sensitive-content flags are needed NOW: the stranger test and the final head-to-head must look at the same clips
    block = set(pl.get("block_flags", ["sexual"]))
    for p in promos:
        p["flags"] = sorted(set(p.get("flags", [])) | set(rank_mod.rule_flags(eps, p)))
        p["blocked"] = bool(set(p["flags"]) & block)

    # ---- step 4: stranger test on the best per episode ---------------------------------
    top_n = pl.get("stranger_top_n_per_episode", 3)
    shortlist = int(pl.get("stranger_shortlist", 0))
    if shortlist:      # tiered cost: one cheap judge call per episode, expensive checks only on the show's best N
        tasks = [(p["episode"], p) for p in slate_mod.shortlist(promos, shortlist, int(cfg.get("slate", {}).get("max_per_episode", 2)))]
    else:
        by_ep = {}
        for p in sorted(promos, key=lambda x: -x["composite"]):
            by_ep.setdefault(p["episode"], []).append(p)
        tasks = [(n, p) for n, lst in by_ep.items() for p in lst[:top_n]]   # each promo is independent

    def _stranger(i):
        n, p = tasks[i]
        stranger.run(llm, eps[n], p, max_s, pl.get("understandable_threshold", 4.0))

    results = _pmap(_stranger, list(range(len(tasks))), workers, log, "stranger")
    tested = 0
    for i, (n, p) in enumerate(tasks):
        if isinstance(results[i], Exception):
            failures.append({"stage": "stranger", "episode": n, "error": str(results[i])[:200]})
            p["notes"].append("stranger test failed - untested")
            continue
        problems = check(p, eps, min_s, max_s)               # repair may have changed length
        if problems:
            p["notes"].append("post-repair check failed: " + "; ".join(problems))
            p["segments"] = [s for s in p["segments"]][-1:]  # fall back to the original cut
        tested += 1
    log(f"[4] stranger-tested {tested} promos")

    # ---- step 6: rank --------------------------------------------------------------------
    ordered = rank_mod.rank(llm, eps, promos, pl.get("pairwise_top_m", 10),
                            tuple(pl.get("block_flags", ["sexual"])))
    log(f"[6] ranked {len(ordered)} promos")

    if cfg.get("slate", {}).get("enabled", True) and ordered:
        try:
            picked, held, ordered = slate_mod.build(llm, eps, ordered, cfg, max_s, workers, log)
            slate_mod.write(out_dir, picked, held)
            from . import tournament as _t
            qual = quality_mod.compute(eps, ordered, picked, dropped, failures, dict(_t.LAST_STATS), input_summary, min_s, max_s)
            quality_mod.write(out_dir, qual)
            for h in qual["health"]:
                log(f"[quality] {'ok  ' if h['level'] == 'ok' else 'WARN'} {h['text']}")
        except (LLMError, ValueError, KeyError, TypeError) as exc:
            failures.append({"stage": "slate", "error": str(exc)[:200]})
            log(f"!! slate stage failed: {exc}")

    _write(out_dir, ordered, dropped, cfg, llm, time.time() - t0, len(eps), failures)
    if failures:
        log(f"!! {len(failures)} model-call failures logged in run_log.json - rerun to retry (cached calls are free)")
    return ordered


def _row(p):
    return {
        "rank": p["rank"], "clip_id": clip_id(p), "episode": p["episode"], "segments": seg_str(p),
        "total_seconds": round(total_seconds(p), 1),
        "hook_type": p["hook_type"], "hook_line": p["hook_line"],
        "cliffhanger_type": p["cliffhanger_type"], "cliffhanger_line": p["cliffhanger_line"],
        "why_it_works": p["why"], "score": p["final_score"],
        "hook_1to5": p["scores"]["hook"], "cliff_1to5": p["scores"]["cliff"],
        "standalone_1to5": p["scores"]["standalone"],
        "stranger_before": p.get("stranger_before", ""), "stranger_after": p.get("stranger_after", ""),
        "flags": ",".join(p["flags"]), "rank_basis": p["rank_basis"],
        "source": p["source"], "notes": "; ".join(p["notes"]),
    }


def _write(out_dir, ordered, dropped, cfg, llm, secs, n_eps, failures=()):
    rows = [_row(p) for p in ordered]
    with open(os.path.join(out_dir, "promos.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())) if rows else None
        if w:
            w.writeheader(); w.writerows(rows)
    json.dump(ordered, open(os.path.join(out_dir, "promos.json"), "w", encoding="utf-8"), indent=1)
    json.dump(dropped, open(os.path.join(out_dir, "dropped_by_validator.json"), "w", encoding="utf-8"), indent=1)
    json.dump({"episodes": n_eps, "promos": len(ordered), "dropped": len(dropped),
               "model_call_failures": list(failures), "seconds": round(secs, 1), "scoring": dict(scoring.W), "llm": {"provider": cfg["llm"]["provider"],
                                                   "model": cfg["llm"]["model"], **llm.stats}},
              open(os.path.join(out_dir, "run_log.json"), "w", encoding="utf-8"), indent=1)

    top = [p for p in ordered if not p["blocked"]][:10]
    lines = ["# Promo report", "",
             f"Model: `{cfg['llm']['provider']}/{cfg['llm']['model']}` - {n_eps} episodes - "
             f"{len(ordered)} promos - LLM calls: {llm.stats['calls']} "
             f"(+{llm.stats['cache_hits']} cached)", "", "## Post these first", ""]
    for p in top:
        lines += [f"**#{p['rank']} - {seg_str(p)}** ({total_seconds(p):.0f}s) - score {p['final_score']}",
                  f"- Hook ({p['hook_type']}): \"{p['hook_line']}\"",
                  f"- Cliffhanger ({p['cliffhanger_type']}): \"{p['cliffhanger_line']}\"",
                  f"- Why: {p['why']}",
                  f"- Stranger test: {p.get('stranger_before', '-')} -> {p.get('stranger_after', '-')} / 5"
                  + (f" | flags: {', '.join(p['flags'])}" if p["flags"] else "")
                  + (f" | notes: {'; '.join(p['notes'])}" if p["notes"] else ""), ""]
    blocked = [p for p in ordered if p["blocked"]]
    if blocked:
        lines += ["## Held back for content flags (needs a human decision)", ""]
        lines += [f"- {seg_str(p)}: {', '.join(p['flags'])}" for p in blocked]
    open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8").write("\n".join(lines))
