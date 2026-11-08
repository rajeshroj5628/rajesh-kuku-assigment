"""The scoring formulas in one place, with weights that live in config.yaml (`scoring:`), so they can be read and changed.

What the numbers are: the model's own 1-5 judgement of a clip. They are NOT measured audience behaviour. The only thing
that touches real behaviour is the experiment proposed in the write-up (A/B on clip_id).

    composite    = (w_hook*hook + w_cliff*cliff + w_standalone*standalone) / (w_hook + w_cliff + w_standalone) + w_rules*rules
    final_score  = composite + stranger_bonus * (stranger_understandable - stranger_baseline)      (only for clips that were stranger-tested)
"""
from __future__ import annotations

W = {"hook": 1.0, "cliff": 1.0, "standalone": 1.0,        # relative weights of the three model ratings
     "rules": 0.05,                                       # small tie-breaker from the rules funnel
     "stranger_bonus": 0.3, "stranger_baseline": 3.0}     # reward / penalty for how well a newcomer follows the clip


def configure(cfg: dict) -> dict:
    """Read the `scoring:` section of config.yaml (missing keys keep their defaults)."""
    for k, v in (cfg.get("scoring") or {}).items():
        if k in W:
            W[k] = float(v)
    return dict(W)


def composite(hook: float, cliff: float, standalone: float, rules: float) -> float:
    tot = W["hook"] + W["cliff"] + W["standalone"]
    return round((W["hook"] * hook + W["cliff"] * cliff + W["standalone"] * standalone) / tot + W["rules"] * rules, 3)


def final(p: dict) -> float:
    su = p.get("stranger_after")
    return round(p["composite"] + (W["stranger_bonus"] * (su - W["stranger_baseline"]) if su is not None else 0.0), 3)
