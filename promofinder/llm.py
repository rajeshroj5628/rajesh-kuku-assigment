"""Swappable LLM adapter (stdlib only).

Providers (config.yaml -> llm.provider):
  gemini             Google Generative Language REST API (free tier works)
  openai_compatible  any /chat/completions endpoint: Groq, OpenRouter, OpenAI, or an
                     API Kuku provides in the interview - just set base_url + model
  mock               offline stub used ONLY to test the plumbing; never counts as a result

The API key is read from an environment variable (or a local .env file) and is sent
in a header, never in the URL, never logged. Every call is cached on disk so reruns
are free and a rate-limit crash can simply be restarted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def parse_json_loose(text: str):
    """Models sometimes wrap JSON in fences or add prose. Extract the first JSON value."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        a, b = t.find(open_c), t.rfind(close_c)
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("model did not return valid JSON")


class LLMError(RuntimeError):
    pass


class LLMRejected(Exception):
    """The server refused the request itself (a 4xx that retrying cannot fix)."""
    def __init__(self, code, body):
        super().__init__(f"HTTP {code}: {body[:200]}")
        self.code, self.body = code, body


class LLM:
    def __init__(self, cfg: dict):
        load_dotenv()
        self.provider = cfg.get("provider", "gemini")
        self.model = cfg.get("model", "")
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        self.temperature = cfg.get("temperature", 0)
        self.key_env = cfg.get("api_key_env", "GEMINI_API_KEY")
        self.min_gap = float(cfg.get("min_seconds_between_calls", 4.5))  # ~13 req/min, free-tier safe
        self.cache_dir = cfg.get("cache_dir", ".llm_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.stats = {"calls": 0, "cache_hits": 0, "prompt_tokens": 0, "output_tokens": 0}
        self._lock = threading.Lock()      # protects stats + the shared rate limiter
        self._next_slot = 0.0
        self.key = os.environ.get(self.key_env, "")
        if self.provider != "mock" and not self.key:
            raise LLMError(
                f"No API key: set environment variable {self.key_env} "
                f"(or put {self.key_env}=... in a local .env file).")

    # ---------------------------------------------------------------- public
    def complete_json(self, system: str, user: str, task: str = "") -> dict | list:
        ck = hashlib.sha256(json.dumps(
            [self.provider, self.model, self.temperature, system, user]).encode()).hexdigest()
        cpath = os.path.join(self.cache_dir, ck + ".json")
        if os.path.exists(cpath):
            self._inc("cache_hits")
            return json.load(open(cpath, encoding="utf-8"))

        last_err = None
        for attempt in range(6):
            raw = ""
            try:
                raw = self._call(system, user, task)
                out = parse_json_loose(raw)
                json.dump(out, open(cpath, "w", encoding="utf-8"))
                return out
            except ValueError as e:                       # bad JSON -> retry w/ nudge
                last_err = e
                user = user + "\n\nReturn ONLY valid JSON, no prose, no code fences."
            except LLMRejected as e:
                raise SystemExit(f"LLM request rejected ({e}).\nCheck llm.model / llm.provider / base_url / your key in config.yaml.")
            except LLMError as e:
                last_err = e
                time.sleep(min(60, 8 * (attempt + 1)))
        raise LLMError(f"LLM call failed after retries: {last_err}")

    # --------------------------------------------------------------- internals
    def _inc(self, key, n=1):
        with self._lock:
            self.stats[key] += n

    def _throttle(self):
        """Reserve the next call slot (calls start >= min_gap apart across ALL threads)."""
        with self._lock:
            now = time.time()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.min_gap
        if slot > now:
            time.sleep(slot - now)

    def _http(self, url, payload, headers):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:   # must come before OSError: HTTPError subclasses it
            body = e.read().decode(errors="replace")[:300]
            body = body.replace(self.key, "***") if self.key else body
            if e.code == 429 and re.search(r"PerDay|insufficient_quota|exceeded your current quota|billing", body, re.I):
                raise SystemExit(f"The daily quota for model '{self.model}' is used up ({body[:140].strip()}).\n"
                                 "Everything finished so far is cached. Switch llm.model or the key in config.yaml (or wait for the reset) "
                                 "and run the same command again: it continues where it stopped.")
            if e.code in (429, 500, 502, 503, 504):
                raise LLMError(f"HTTP {e.code} (retryable): {body}")
            raise LLMRejected(e.code, body)
        except urllib.error.URLError as e:
            raise LLMError(f"network error: {e.reason}")
        except (TimeoutError, ConnectionError, OSError) as e:   # plain socket timeouts / resets
            raise LLMError(f"network/timeout (retryable): {e}")

    def _call(self, system, user, task):
        if self.provider == "mock":
            from .mock import mock_response
            self._inc("calls")
            return json.dumps(mock_response(task, user))
        self._throttle()
        self._inc("calls")
        if self.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            res = self._http(url, {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": self.temperature,
                                     "responseMimeType": "application/json"},
            }, {"x-goog-api-key": self.key})
            try:
                text = res["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                raise LLMError(f"empty/blocked response: {str(res)[:200]}")
            u = res.get("usageMetadata", {})
            self._inc("prompt_tokens", u.get("promptTokenCount", 0))
            self._inc("output_tokens", u.get("candidatesTokenCount", 0))
            return text
        if self.provider == "openai_compatible":
            if not self.base_url:
                raise SystemExit("llm.base_url is required for provider openai_compatible")
            payload = {"model": self.model, "temperature": self.temperature,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]}
            if not getattr(self, "_no_resp_format", False):
                payload["response_format"] = {"type": "json_object"}
            try:
                res = self._http(f"{self.base_url}/chat/completions", payload, {"Authorization": f"Bearer {self.key}"})
            except LLMRejected as e:
                if "response_format" in payload and e.code in (400, 422) and re.search(r"response_format|json_object|json mode", e.body, re.I):
                    self._no_resp_format = True            # this server has no JSON mode: rely on the prompt + loose parsing
                    payload.pop("response_format")
                    res = self._http(f"{self.base_url}/chat/completions", payload, {"Authorization": f"Bearer {self.key}"})
                else:
                    raise
            try:
                text = res["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                raise LLMError(f"empty response: {str(res)[:200]}")
            u = res.get("usage", {})
            self._inc("prompt_tokens", u.get("prompt_tokens", 0))
            self._inc("output_tokens", u.get("completion_tokens", 0))
            return text
        raise SystemExit(f"Unknown llm.provider: {self.provider}")
