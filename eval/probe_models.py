"""One tiny call per candidate model: reports OK / quota limit so we can choose a model with room."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promofinder.llm import load_dotenv  # noqa: E402

load_dotenv()
key = os.environ["GEMINI_API_KEY"]
models = sys.argv[1:] or ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                          "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash",
                          "gemini-2.5-flash-lite", "gemini-flash-latest", "gemma-4-31b-it"]
for m in models:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": 'Reply with {"ok": true}'}]}],
                       "generationConfig": {"responseMimeType": "application/json"}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
            print(f"{m:<28} OK   {time.time()-t:4.1f}s")
    except urllib.error.HTTPError as e:
        b = e.read().decode(errors="replace").replace(key, "***")
        qid = re.search(r'"quotaId":\s*"([^"]+)"', b)
        qv = re.search(r'"quotaValue":\s*"([^"]+)"', b)
        print(f"{m:<28} HTTP {e.code}  {qid.group(1) if qid else b[:70].replace(chr(10), ' ')}"
              f"{'  limit=' + qv.group(1) if qv else ''}")
    except Exception as e:
        print(f"{m:<28} {type(e).__name__}")
    time.sleep(1.5)
