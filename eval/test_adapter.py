"""Test the model adapter against a fake OpenAI-compatible server (no API key, no quota, no cost).

    python eval/test_adapter.py --scripts "<folder>"

Checks, end to end through run_pipeline.py:
  1. a normal OpenAI-compatible endpoint works for the whole pipeline;
  2. a server that REJECTS response_format (HTTP 400) still works: the adapter drops it and asks again;
  3. a daily-quota 429 stops the run at once with a clear message (instead of retrying every call for minutes);
  4. a wrong model name (HTTP 404) stops with a clear message."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promofinder.mock import mock_response  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument("--scripts", required=True)
a = ap.parse_args()
MODE = {"m": "ok"}
SEEN = {"with_rf": 0, "without_rf": 0}


def task_of(system: str) -> str:
    s = system.lower()
    if "head of social" in s:
        return "compare"
    if "first-time viewer" in s:
        return "stranger"
    if "precise promo editor" in s:
        return "repair"
    if "protect a short-drama" in s:
        return "spoiler"
    return "judge"


class H(BaseHTTPRequestHandler):
    def log_message(self, *x):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        mode = MODE["m"]
        if mode == "quota":
            return self._err(429, '{"error":{"message":"You exceeded your current quota","status":"RESOURCE_EXHAUSTED","details":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}')
        if mode == "badmodel" or body.get("model") != "fake-model":
            return self._err(404, '{"error":{"message":"The model does not exist"}}')
        if "response_format" in body:
            SEEN["with_rf"] += 1
            if mode == "no_json_mode":
                return self._err(400, '{"error":{"message":"response_format is not supported by this model"}}')
        else:
            SEEN["without_rf"] += 1
        sysm = next(m["content"] for m in body["messages"] if m["role"] == "system")
        usr = next(m["content"] for m in body["messages"] if m["role"] == "user")
        out = json.dumps(mock_response(task_of(sysm), usr))
        raw = json.dumps({"choices": [{"message": {"content": out}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers()
        self.wfile.write(raw)

    def _err(self, code, text):
        raw = text.encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers()
        self.wfile.write(raw)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
results = []


def run(mode, model="fake-model"):
    MODE["m"] = mode
    SEEN.update(with_rf=0, without_rf=0)
    tmp = tempfile.mkdtemp()
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    cfg["llm"].update(provider="openai_compatible", base_url=f"http://127.0.0.1:{port}/v1", model=model, api_key_env="FAKE_KEY",
                      cache_dir=os.path.join(tmp, "cache"), min_seconds_between_calls=0)
    cfgp = os.path.join(tmp, "c.yaml")
    yaml.safe_dump(cfg, open(cfgp, "w", encoding="utf-8"))
    r = subprocess.run([sys.executable, "run_pipeline.py", "--input", a.scripts, "--config", cfgp, "--episodes", "1", "2", "3", "4", "5",
                        "--out", os.path.join(tmp, "out")], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "FAKE_KEY": "dummy"}, timeout=300)
    made = os.path.isfile(os.path.join(tmp, "out", "slate.json"))
    return r, made


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""), flush=True)


r, made = run("ok")
check("OpenAI-compatible endpoint: full pipeline runs and writes the slate", r.returncode == 0 and made, f"exit {r.returncode}")
r, made = run("no_json_mode")
check("Server without JSON mode (400 on response_format): adapter drops it and carries on", r.returncode == 0 and made and SEEN["without_rf"] > 0,
      f"{SEEN['with_rf']} rejected tries, then {SEEN['without_rf']} plain requests")
r, made = run("quota")
msg = (r.stderr + r.stdout)
check("Daily quota (429): stops at once with a clear message", r.returncode != 0 and "daily quota" in msg and "cached" in msg, msg.strip().splitlines()[-1][:90] if msg.strip() else "")
r, made = run("ok", model="wrong-model")
msg = (r.stderr + r.stdout)
check("Wrong model name (404): stops with a clear message", r.returncode != 0 and "rejected" in msg and "llm.model" in msg, msg.strip().splitlines()[-1][:90] if msg.strip() else "")
srv.shutdown()
print(f"\n{sum(results)}/{len(results)} adapter checks passed")
sys.exit(0 if all(results) else 1)
