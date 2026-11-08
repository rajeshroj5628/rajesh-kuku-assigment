"""Will it survive a different drama? A stress test on a DISGUISED copy of the pack (no model calls).

    python eval/transfer_test.py --scripts "<folder>"

Makes a temporary copy in which: the main characters have new names; files are called part_07.txt (no 'episode' in the name);
half of the files have lost their EPISODE NUMBER / LOGLINE / CONTINUITY header; some use Windows line endings and a BOM.
Then checks that
  1. every episode is still found, with the right numbering;
  2. the rules funnel proposes (nearly) the same windows, i.e. it does not depend on this show's character names;
  3. the whole pipeline runs on it with the offline mock model and writes a slate.
It cannot prove quality on a new show; it shows the plumbing and the heuristics are not tied to this one."""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaml  # noqa: E402
from promofinder.candidates import generate  # noqa: E402
from promofinder.parse import load_show  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument("--scripts", required=True)
a = ap.parse_args()
NAMES = {"JULIANA": "PRIYA", "ALEXANDER": "ARJUN", "CATHERINE": "MEERA", "GERALD": "KABIR", "RUPERT": "VIKRAM", "ASHBURTON": "SHARMA", "ASHCROFT": "VERMA"}
tmp = tempfile.mkdtemp()
src = sorted(glob.glob(os.path.join(a.scripts, "**", "*.txt"), recursive=True))
orig = load_show(a.scripts)
for i, path in enumerate(src, 1):
    text = open(path, encoding="utf-8-sig", errors="replace").read()
    for old, new in NAMES.items():
        text = re.sub(old, new, text, flags=re.I) if old.isupper() else text
        text = re.sub(old.title(), new.title(), text)
    if i % 2 == 0:                                            # drop the header the parser could use for the episode number
        text = "\n".join(l for l in text.splitlines() if not re.match(r"\s*(EPISODE NUMBER|EPISODE LOGLINE|CONTINUITY NOTES)", l, re.I))
    if i % 3 == 0:
        text = text.replace("\n", "\r\n")
    data = text.encode("utf-8")
    if i % 5 == 0:
        data = b"\xef\xbb\xbf" + data
    open(os.path.join(tmp, f"part_{i:02d}.txt"), "wb").write(data)

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""), flush=True)


logs = []
new = load_show(tmp, logs.append)
check("Every episode is found, numbered 1..N in file order", [e.number for e in new] == list(range(1, len(src) + 1)), f"{len(new)} of {len(src)}")
check("Beats parsed identically with CRLF / BOM / missing headers", sum(len(e.beats) for e in new) == sum(len(e.beats) for e in orig),
      f"{sum(len(e.beats) for e in new)} vs {sum(len(e.beats) for e in orig)}")
cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
f, w = cfg["funnel"], cfg["window"]
same = tot = 0
for eo, en in zip(orig, new):
    co = {(round(c.start, 1), round(c.end, 1)) for c in generate(eo, w["min_seconds"], w["max_seconds"], f["top_k_per_episode"], f["overlap_iou"])}
    cn = {(round(c.start, 1), round(c.end, 1)) for c in generate(en, w["min_seconds"], w["max_seconds"], f["top_k_per_episode"], f["overlap_iou"])}
    same += len(co & cn)
    tot += len(co)
check("Funnel proposes the same windows after the characters are renamed", same / tot >= 0.97, f"{100 * same / tot:.1f}% of {tot} windows identical")
r = subprocess.run([sys.executable, "run_pipeline.py", "--input", tmp, "--provider", "mock", "--episodes", "1", "2", "3", "4", "5", "6", "7", "8", "--out", os.path.join(tmp, "out")],
                   cwd=ROOT, capture_output=True, text=True, timeout=600)
slate = os.path.join(tmp, "out_MOCK", "slate.json")
check("Whole pipeline runs on the disguised copy and writes a slate", r.returncode == 0 and os.path.isfile(slate), f"exit {r.returncode}")
shutil.rmtree(tmp, ignore_errors=True)

# ---- other screenplay formats: no timestamps at all, inline "[mm:ss] NAME: text" lines, and .srt subtitles --------------------
from promofinder.parse import fmt  # noqa: E402
variants = {}
first5 = orig[:5]
for name in ("untimed", "inline", "srt"):
    d = tempfile.mkdtemp()
    variants[name] = d
    for e in first5:
        if name == "untimed":                                   # the same script with every Start/End line removed
            raw = open(e.source, encoding="utf-8-sig", errors="replace").read()
            raw = "\n".join(l for l in raw.splitlines() if not re.search(r"Start:\s*\(", l))
            open(os.path.join(d, f"ep_{e.number:02d}.txt"), "w", encoding="utf-8").write(raw)
        elif name == "inline":
            rows = [f"[{fmt(b.start)}] {(b.label or b.kind.upper())}{' (' + b.tone + ')' if b.tone else ''}: {b.text}" for b in e.beats if b.kind != "camera"]
            open(os.path.join(d, f"ep_{e.number:02d}.txt"), "w", encoding="utf-8").write("\n".join(rows))
        else:
            def ts(t):
                return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d},{int(round((t % 1) * 1000)):03d}"
            blocks = [f"{i}\n{ts(b.start)} --> {ts(b.end)}\n{b.text}\n" for i, b in enumerate([b for b in e.beats if b.kind == 'dialogue'], 1)]
            open(os.path.join(d, f"ep_{e.number:02d}.srt"), "w", encoding="utf-8").write("\n".join(blocks))
expected = {"untimed": "estimated", "inline": "inline", "srt": "srt"}
for name, d in variants.items():
    got = load_show(d, logs.append)
    modes = {e.timing for e in got}
    check(f"Format '{name}': 5 episodes read, timing mode = {expected[name]}", len(got) == 5 and modes == {expected[name]} and all(e.beats for e in got),
          f"{len(got)} episodes, {sum(len(e.beats) for e in got)} beats, mode {sorted(modes)}")
    rr = subprocess.run([sys.executable, "run_pipeline.py", "--input", d, "--provider", "mock", "--out", os.path.join(d, "out")], cwd=ROOT, capture_output=True, text=True, timeout=600)
    check(f"Format '{name}': whole pipeline runs and warns where times are approximate",
          rr.returncode == 0 and os.path.isfile(os.path.join(d, "out_MOCK", "slate.json")) and (name != "untimed" or "estimated" in rr.stdout),
          f"exit {rr.returncode}")
    shutil.rmtree(d, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} transfer checks passed")
sys.exit(0 if all(results) else 1)
