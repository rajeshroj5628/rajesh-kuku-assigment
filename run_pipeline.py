"""Find promos in a microdrama screenplay folder.

    python run_pipeline.py --input <folder> [--out out] [--episodes 3 12 15] [--provider mock]
    python run_pipeline.py --check-llm        # one tiny call to verify key + model

The model is chosen in config.yaml (or overridden with --provider / --model / --base-url).
"""
import argparse
import os
import sys

import yaml

from promofinder.llm import LLM, LLMError


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input")
    ap.add_argument("--out", default=None)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--episodes", type=int, nargs="*", help="only these episode numbers")
    ap.add_argument("--provider"); ap.add_argument("--model"); ap.add_argument("--base-url")
    ap.add_argument("--check-llm", action="store_true")
    ap.add_argument("--no-stranger", action="store_true",
                    help="baseline: skip the stranger test + repair (step 4)")
    a = ap.parse_args()

    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    if a.no_stranger:
        cfg.setdefault("pipeline", {})["stranger_top_n_per_episode"] = 0
    for k, v in (("provider", a.provider), ("model", a.model), ("base_url", a.base_url)):
        if v:
            cfg["llm"][k] = v
    try:
        llm = LLM(cfg["llm"])
    except LLMError as e:
        sys.exit(str(e))

    if a.check_llm:
        r = llm.complete_json("Return ONLY JSON.", 'Reply with {"ok": true}', task="")
        print("LLM reachable:", r, "| model:", cfg["llm"]["model"])
        return
    if not a.input:
        sys.exit("--input is required")

    from promofinder.pipeline import run
    out = a.out or cfg.get("output_dir", "out")
    if cfg["llm"]["provider"] == "mock":
        print("!! MOCK provider: plumbing test only, results are meaningless !!")
        out = out + "_MOCK"
    ordered = run(cfg, llm, a.input, out, set(a.episodes) if a.episodes else None)
    print(f"\nDone. {len(ordered)} promos -> {os.path.join(out, 'report.md')}")
    print("LLM usage:", llm.stats)


if __name__ == "__main__":
    main()
