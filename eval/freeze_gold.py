"""Freeze a reference ("gold") file so it cannot drift while the tool is being tuned.

    python eval/freeze_gold.py eval/gold_heldout.json --role heldout --scripts "<folder>"

Checks the picks against the real screenplay beat boundaries (eval/check_gold.py), then records the file's SHA-256 and the
time in eval/GOLD_FREEZE.json. eval/evaluate.py refuses to score a frozen file whose hash no longer matches, and prints
the role: 'dev' sets were looked at while the tool was built (optimistic); a 'heldout' set must be scored once, after
the last design change, and never used to tune anything."""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG = os.path.join(ROOT, "eval", "GOLD_FREEZE.json")

ap = argparse.ArgumentParser()
ap.add_argument("gold")
ap.add_argument("--role", choices=["dev", "heldout"], required=True)
ap.add_argument("--scripts", required=True, help="folder with the episode .txt files, used to check the boundaries")
ap.add_argument("--force", action="store_true", help="freeze even if some picks are off the beat boundaries")
a = ap.parse_args()
r = subprocess.run([sys.executable, os.path.join(ROOT, "eval", "check_gold.py"), a.gold, "--scripts", a.scripts], capture_output=True, text=True)
print(r.stdout)
if "CHECK" in r.stdout and not a.force:
    sys.exit("Some picks are not on screenplay beat boundaries. Fix them (or pass --force to accept them as written) and run again.")
raw = open(a.gold, "rb").read()
reg = json.load(open(REG, encoding="utf-8")) if os.path.isfile(REG) else {}
reg[os.path.relpath(a.gold, ROOT).replace("\\", "/")] = {"sha256": hashlib.sha256(raw).hexdigest(), "role": a.role,
                                                         "frozen_at": datetime.datetime.now().isoformat(timespec="seconds")}
json.dump(reg, open(REG, "w", encoding="utf-8"), indent=1)
print(f"frozen {a.gold} as '{a.role}'")
