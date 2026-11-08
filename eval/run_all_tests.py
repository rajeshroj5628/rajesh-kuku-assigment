"""Run every automated test and record what really passed (eval_reports/tests.json). The write-up reads that file, so its
test counts are never typed by hand.

    python eval/run_all_tests.py --scripts "<folder>"      (start `python app.py --scripts "<folder>"` first for the browser test)

Tests: core unit tests (validators, scoring, slate, parser), disguised-drama / format tests, adapter tests against a fake
OpenAI-compatible server, and the browser test of the web app. The browser test is skipped, and says so, if the app is not running
or Playwright is not installed. Nothing here needs a real model key or the network."""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument("--scripts", required=True)
ap.add_argument("--run", default="out")
a = ap.parse_args()


def app_up():
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/api/runs", timeout=3).read()
        return True
    except Exception:
        return False


suites = [("core unit tests", ["eval/test_core.py"]),
          ("parser formats + disguised drama", ["eval/transfer_test.py", "--scripts", a.scripts]),
          ("model adapter vs fake OpenAI-style server", ["eval/test_adapter.py", "--scripts", a.scripts]),
          ("web app in a real browser", ["eval/frontend_check.py", "--run", a.run, "--scripts", a.scripts])]
out = {"when": datetime.datetime.now().isoformat(timespec="seconds"), "suites": []}
for name, args in suites:
    if name.startswith("web app") and not app_up():
        out["suites"].append({"name": name, "passed": None, "total": None, "skipped": "the app was not running on port 8000"})
        print(f"SKIPPED  {name}: the app was not running", flush=True)
        continue
    r = subprocess.run([sys.executable] + args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    m = re.findall(r"(\d+)/(\d+) [a-z ]*(?:tests?|checks?) passed", r.stdout)
    passed, total = (int(m[-1][0]), int(m[-1][1])) if m else (0, 0)
    out["suites"].append({"name": name, "passed": passed, "total": total, "exit": r.returncode})
    print(f"{'PASS' if r.returncode == 0 else 'FAIL'}  {name}: {passed}/{total}", flush=True)
os.makedirs(os.path.join(ROOT, "eval_reports"), exist_ok=True)
json.dump(out, open(os.path.join(ROOT, "eval_reports", "tests.json"), "w", encoding="utf-8"), indent=1)
ran = [s for s in out["suites"] if s["total"] is not None]
print(f"\n{sum(s['passed'] for s in ran)}/{sum(s['total'] for s in ran)} automated checks passed ({len(out['suites']) - len(ran)} suite(s) skipped)")
sys.exit(0 if all(s.get("exit", 0) == 0 for s in ran) else 1)
