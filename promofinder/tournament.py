"""Swiss tournament between the best clip of each episode.

Why: asking a model for an absolute 1-5 score per clip makes almost everything a 3 or 4, so clips from
different episodes cannot be told apart. Asking "which of these two would you post first?" is a much
easier question. Each pair is asked in BOTH orders (to cancel the model's habit of favouring A or B);
a disagreement counts as a draw. Swiss pairing (similar records meet) gives a usable order from about
N/2 x rounds games instead of N(N-1)/2.
"""
from __future__ import annotations

from . import rank as rank_mod


SYS2 = ("You are the head of social for a short-drama app deciding which promo to post FIRST. A good promo opens on "
        "a hook in its first 3 seconds, stops on an unresolved cliffhanger, and is understandable to someone who has "
        "never seen the show. Return ONLY JSON.")


def ask_pair(llm, eps, a: dict, b: dict, examples: str = "", reason_first: bool = True):
    """One verdict: 'A', 'B' or None. With reason_first the model writes its comparison BEFORE choosing."""
    spec = ('Return JSON {"reason": "<one sentence comparing their hook and cliffhanger>", "winner": "A" or "B"}'
            if reason_first else 'Which should be posted first? Return JSON {"winner": "A" or "B", "reason": "<one sentence>"}')
    user = f"{examples}PROMO A:\n{rank_mod._clip(eps, a)}\n\nPROMO B:\n{rank_mod._clip(eps, b)}\n\n{spec}"
    try:
        r = llm.complete_json(SYS2, user, task="compare")
        if isinstance(r, list):
            r = next((x for x in r if isinstance(x, dict)), {})
        w = str(r.get("winner", "")).strip().upper()
    except Exception:
        return None
    return "A" if w.startswith("A") else "B" if w.startswith("B") else None


def _play(llm, eps, a: dict, b: dict, ask=None, stats: dict | None = None) -> float:
    """Points for `a` against `b`: 1 win, 0.5 draw/unknown, 0 loss. Both orders; a split verdict is a draw."""
    ask = ask or (lambda x, y: rank_mod._ask(llm, eps, x, y))
    r1 = ask(a, b)         # a shown as A
    r2 = ask(b, a)         # a shown as B
    if stats is not None:
        stats["pairs"] = stats.get("pairs", 0) + 1
        stats["unknown"] = stats.get("unknown", 0) + (r1 is None or r2 is None)
        stats["split"] = stats.get("split", 0) + (r1 is not None and r2 is not None and (r1 == "A") == (r2 == "A"))
        stats["said_A"] = stats.get("said_A", 0) + (r1 == "A") + (r2 == "A")
        stats["answers"] = stats.get("answers", 0) + (r1 is not None) + (r2 is not None)
    if r1 is None or r2 is None:
        return 0.5
    wins = (r1 == "A") + (r2 == "B")
    return wins / 2.0


LAST_STATS: dict = {}          # judge statistics of the most recent tournament (read by the quality report)


def swiss(llm, eps, pool: list[dict], rounds: int, workers: int, log, ask=None, stats=None) -> list[dict]:
    from .pipeline import _pmap
    if stats is None:
        stats = {}
    pts = {id(p): 0.0 for p in pool}
    games = {id(p): 0 for p in pool}
    met = set()
    for rd in range(1, rounds + 1):
        order = sorted(pool, key=lambda p: (-pts[id(p)], -p.get("composite", p["final_score"])))
        pairs, used = [], set()
        for i, a in enumerate(order):
            if id(a) in used:
                continue
            for b in order[i + 1:]:
                key = frozenset((id(a), id(b)))
                if id(b) in used or key in met:
                    continue
                pairs.append((a, b)); used |= {id(a), id(b)}; met.add(key)
                break
        res = _pmap(lambda i: _play(llm, eps, pairs[i][0], pairs[i][1], ask, stats), list(range(len(pairs))), workers, lambda *_: None, "t")
        for i, (a, b) in enumerate(pairs):
            r = res[i]
            s = 0.5 if isinstance(r, Exception) else r
            pts[id(a)] += s; pts[id(b)] += 1 - s
            games[id(a)] += 1; games[id(b)] += 1
        log(f"    [tournament] round {rd}/{rounds}: {len(pairs)} pairs")
    for p in pool:
        g = max(1, games[id(p)])
        p["tourney_points"], p["tourney_games"] = pts[id(p)], games[id(p)]
        p["tourney_strength"] = round(5.0 * pts[id(p)] / g, 3)       # 0-5, comparable to the 1-5 scores
    LAST_STATS.clear()
    LAST_STATS.update(stats)
    return sorted(pool, key=lambda p: (-p["tourney_strength"], -p["final_score"]))
