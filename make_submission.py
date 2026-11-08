"""Build submission_repo/ - the ONLY folder to upload to GitHub.

Copies source, config, README and eval code; never copies .env, .llm_cache, out*, or any episode text.
Then scans the result for (a) your API key and (b) lines from the confidential scripts.

    python make_submission.py --scripts "<folder with the episode .txt files>"
"""
import argparse
import glob
import os
import re
import shutil
import sys

KEEP = ["promofinder/*.py", "run_pipeline.py", "run_funnel.py", "make_submission.py",
        "config.yaml", "requirements.txt", "README.md", ".env.example", ".gitignore",
        "eval/*.py", "eval/gold_*.json", "ui/viewer.html", "ui/app.html", "app.py", "examples/editor_picks.json", "eval/GOLD_FREEZE.json", "make_demo_site.py", "demo_site/*", "demo_site/data/*"]
OUT = "submission_repo"


def secret_values():
    vals = []
    if os.path.exists(".env"):
        for line in open(".env", encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if len(v) >= 12:
                    vals.append(v)
    return vals


def script_lines(folder):
    """Distinctive dialogue strings from the confidential scripts (>= 40 chars)."""
    lines = set()
    for p in glob.glob(os.path.join(folder, "**", "*.txt"), recursive=True):
        for l in open(p, encoding="utf-8-sig", errors="replace"):
            l = l.strip().strip('"')
            if len(l) >= 40 and not l.startswith(("**", "Start:", "Scene", "EPISODE", "CONTINUITY")):
                lines.add(l)
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripts", required=True)
    a = ap.parse_args()

    if os.path.isdir(OUT):                                  # keep .git, so a rebuild never loses the repository history
        for name in os.listdir(OUT):
            if name != ".git":
                path = os.path.join(OUT, name)
                shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.remove(path)
    for pat in KEEP:
        for f in (x for x in glob.glob(pat) if os.path.isfile(x)):
            dst = os.path.join(OUT, f)
            os.makedirs(os.path.dirname(dst) or OUT, exist_ok=True)
            shutil.copy2(f, dst)

    secrets, lines = secret_values(), script_lines(a.scripts)
    problems = 0
    for root, _, files in os.walk(OUT):
        for fn in files:
            path = os.path.join(root, fn)
            text = open(path, encoding="utf-8", errors="replace").read()
            for s in secrets:
                if s in text:
                    print(f"!! SECRET found in {path}"); problems += 1
            hit = next((l for l in lines if l in text), None)
            if hit:
                print(f"!! SCRIPT TEXT found in {path}: {hit[:50]}..."); problems += 1
    n = sum(len(f) for _, _, f in os.walk(OUT))
    print(f"{OUT}/ built with {n} files; {problems} problems")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()

