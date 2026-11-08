"""Promo Finder - local web app.   python app.py   then open http://127.0.0.1:8000

Upload episode .txt files (or point at a folder), press Run, watch live progress, browse the ranked
promos. Runs the same pipeline as run_pipeline.py. Binds to localhost only; nothing is sent anywhere
except the model calls the pipeline itself makes. The API key stays in .env and is never served.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(ROOT, "uploads")
RUNS = os.path.join(ROOT, "runs")
PORT = int(os.environ.get("PORT", "8000"))

STATE = {"proc": None, "log": [], "out": None, "code": None, "started": 0.0}
LOCK = threading.Lock()

sys.path.insert(0, ROOT)
from promofinder.parse import load_show, summarize   # noqa: E402

ALLOWED = (".txt", ".md", ".srt")                       # screenplay file types the upload accepts

DEFAULT_SCRIPTS = os.environ.get("PROMO_SCRIPTS", "")     # set with --scripts <folder>
_EPS = {}                                                  # input dir -> {episode number: Episode}


def scripts_for(run):
    """Folder holding the screenplay text a run was made from (for the clip preview)."""
    d = run_dirs().get(run)
    if d:
        try:
            with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
                m = json.load(f).get("input_dir", "")
            if m and os.path.isdir(m):
                return m
        except (OSError, ValueError):
            pass
    return DEFAULT_SCRIPTS if DEFAULT_SCRIPTS and os.path.isdir(DEFAULT_SCRIPTS) else ""


def episode_of(run, num):
    d = scripts_for(run)
    if not d:
        return None
    if d not in _EPS:
        _EPS[d] = {e.number: e for e in load_show(d)}
    return _EPS[d].get(num)


def clip_payload(run, num, spans):
    ep = episode_of(run, num)
    if not ep:
        return {"available": False}
    lines = []
    for s0, s1 in spans:
        lines.append([{"start": b.start, "end": b.end, "kind": b.kind, "label": b.label, "tone": b.tone,
                       "text": b.text} for b in ep.beats if b.kind != "camera" and b.end > s0 and b.start < s1])
    return {"available": True, "duration": ep.duration, "logline": ep.logline,
            "scenes": [{"n": s["n"], "heading": s["heading"], "start": s["start"], "end": s["end"]} for s in ep.scenes],
            "segments": lines}


def load_slate(d, promos):
    """The slate with the full promo records (so matching can look at every segment)."""
    sp = os.path.join(d, "slate.json")
    if not os.path.isfile(sp):
        return []
    full = {p["rank"]: p for p in promos}
    out = []
    for s in json.load(open(sp, encoding="utf-8")):
        base = full.get(s.get("rank"))
        out.append(dict(base, slate_rank=s.get("slate_rank")) if base and base["episode"] == s["episode"] else s)
    return out


GOLD_LABELS = {"gold_user.json": "Your 8 picks", "gold_builder.json": "7 picks drafted by the AI assistant"}


def eval_payload(run):
    from eval.metrics import evaluate
    from eval.run_eval import role_of
    d = run_dirs().get(run)
    with open(os.path.join(d, "promos.json"), encoding="utf-8") as f:
        promos = json.load(f)
    slate = load_slate(d, promos)
    sets = []
    edir = os.path.join(ROOT, "eval")
    for fn in sorted(x for x in os.listdir(edir) if x.startswith("gold_") and x.endswith(".json")):
        path = os.path.join(edir, fn)
        try:
            role, when = role_of(path)
        except SystemExit as e:
            role, when = f"CHANGED after freezing: {e}", None
        with open(path, encoding="utf-8") as f:
            gold = json.load(f)["promos"]
        r = evaluate(promos, slate, gold)
        n = r["n"]
        sets.append({**r, "name": GOLD_LABELS.get(fn, fn), "file": fn, "role": role, "frozen_at": when, "total": n,
                     "hits": round(r["recall_any"] * n), "top1": round(r["in_episode_top1"] * n), "top3": round(r["in_episode_top3"] * n)})
    return {"sets": sets, "independent_test": any(s["role"] == "heldout" for s in sets)}


def unzip_job(job, data):
    """Unpack an uploaded archive into uploads/<job>, keeping only screenplay files. No path from inside the archive is used
    (so nothing can be written outside the folder), and counts and sizes are capped."""
    import io
    import zipfile
    d = os.path.join(UPLOADS, job)
    os.makedirs(d, exist_ok=True)
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("that is not a valid .zip file")
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > 3000:
        raise ValueError("the archive has more than 3000 files")
    saved, total, seen = 0, 0, set()
    for i in infos:
        base = os.path.basename(i.filename)
        if not base or base.startswith(".") or "__MACOSX" in i.filename or not base.lower().endswith(ALLOWED) or i.file_size > 5_000_000:
            continue
        total += i.file_size
        if total > 200_000_000:
            raise ValueError("the archive is larger than 200 MB once unpacked")
        name, k = base, 1
        while name in seen:
            name, k = f"{k}_{base}", k + 1
        seen.add(name)
        with z.open(i) as src, open(os.path.join(d, name), "wb") as out:
            out.write(src.read(5_000_001))
        saved += 1
    if not saved:
        raise ValueError("no .txt, .md or .srt screenplay files were found in the archive")
    return saved


def inspect_job(job):
    """What the parser finds in an upload, before anything is run: episodes, beats, tones, how times were obtained."""
    msgs = []
    try:
        eps = load_show(os.path.join(UPLOADS, job), msgs.append)
    except SystemExit as e:
        return {"ok": False, "error": str(e), "messages": msgs}
    return {"ok": True, "summary": summarize(eps), "messages": msgs, "episodes": [e.number for e in eps]}


FILES_OK = {"slate.md", "quality.md", "report.md", "promos.csv", "promos.json", "slate.json", "run_log.json", "quality.json"}


def score_picks(name, gold, promos):
    """How did the tool do against a list of human-picked clips? gold = [{episode,start,end,note}]"""
    from eval.evaluate import best_match
    from promofinder.textutil import parse_ts
    items = []
    for g in gold:
        v, p = best_match(g, promos)
        same = sorted([x for x in promos if x["episode"] == g["episode"]], key=lambda x: x["rank"])
        items.append({"episode": g["episode"], "note": g.get("note", ""), "gold": [parse_ts(g["start"]), parse_ts(g["end"])],
                      "iou": round(v, 2), "hit": v >= 0.5, "rank": p["rank"] if p else None,
                      "ep_rank": same.index(p) + 1 if p in same else None, "ep_total": len(same),
                      "tool": [p["segments"][0]["start"], p["segments"][-1]["end"]] if p else None})
    return {"name": name, "items": items, "total": len(items),
            "hits": sum(i["hit"] for i in items),
            "top1": sum(1 for i in items if i["ep_rank"] == 1),
            "top3": sum(1 for i in items if i["ep_rank"] and i["ep_rank"] <= 3)}


def eval_custom(run, picks):
    from promofinder.textutil import parse_ts
    d = run_dirs().get(run)
    if not d:
        return {"error": "unknown run"}
    clean = []
    for g in picks[:60]:
        try:
            ep, s, e = int(g["episode"]), parse_ts(g["start"]), parse_ts(g["end"])
        except (KeyError, TypeError, ValueError):
            return {"error": "each pick needs an episode, a start and an end (like 01:24)"}
        if s is None or e is None or e <= s:
            return {"error": f"bad times for episode {ep}: use mm:ss, with the end after the start"}
        clean.append({"episode": ep, "start": g["start"], "end": g["end"], "note": str(g.get("note", ""))[:80]})
    with open(os.path.join(d, "promos.json"), encoding="utf-8") as f:
        promos = json.load(f)
    return {"set": score_picks("Your picks", clean, promos)}


def run_dirs():
    """Folders that hold a finished promos.json: out, out_baseline, runs/*"""
    found = {}
    for name in sorted(os.listdir(ROOT)):                       # out, out_baseline, out_v2 ...
        if name.startswith("out") and os.path.isfile(os.path.join(ROOT, name, "promos.json")):
            found[name] = os.path.join(ROOT, name)
    if os.path.isdir(RUNS):
        for d in sorted(os.listdir(RUNS)):
            if os.path.isfile(os.path.join(RUNS, d, "promos.json")):
                found["runs/" + d] = os.path.join(RUNS, d)
    return found


def start_run(body):
    with LOCK:
        if STATE["proc"] and STATE["proc"].poll() is None:
            return {"error": "A run is already in progress."}
    job = str(body.get("job") or "")
    if job:
        if not re.fullmatch(r"[a-z0-9]{6,24}", job):
            return {"error": "bad job id"}
        input_dir = os.path.join(UPLOADS, job)
    else:
        input_dir = str(body.get("input_dir") or "").strip().strip('"')
    if not input_dir or not os.path.isdir(input_dir):
        return {"error": "Input folder not found. Upload .txt files or enter a valid folder path."}
    if not any(f.lower().endswith(".txt") for _, _, fs in os.walk(input_dir) for f in fs):
        return {"error": "No .txt episode files in that folder."}
    name = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("run_name") or "")) or time.strftime("run_%Y%m%d_%H%M%S")
    out = os.path.join(RUNS, name)
    cmd = [sys.executable, "-u", "run_pipeline.py", "--input", input_dir, "--out", out]
    eps = re.findall(r"\d+", str(body.get("episodes") or ""))
    if eps:
        cmd += ["--episodes"] + eps
    if not body.get("stranger", True):
        cmd.append("--no-stranger")
    if body.get("mock"):
        cmd += ["--provider", "mock"]
    os.makedirs(out + ("_MOCK" if body.get("mock") else ""), exist_ok=True)
    with open(os.path.join(out + ("_MOCK" if body.get("mock") else ""), "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"input_dir": input_dir}, f)
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    with LOCK:
        STATE.update(proc=proc, log=[], out=name + ("_MOCK" if body.get("mock") else ""),
                     code=None, started=time.time())

    def pump():
        for line in proc.stdout:
            with LOCK:
                STATE["log"].append(line.rstrip())
                del STATE["log"][:-400]
        code = proc.wait()
        with LOCK:
            STATE["code"] = code
    threading.Thread(target=pump, daemon=True).start()
    return {"ok": True, "run": "runs/" + STATE["out"]}


class H(BaseHTTPRequestHandler):
    def _send(self, code, data, ctype="application/json"):
        raw = data if isinstance(data, bytes) else (data if isinstance(data, str) else json.dumps(data)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):          # quiet
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(ROOT, "ui", "app.html"), "rb") as f:
                return self._send(200, f.read(), "text/html")
        if u.path == "/api/runs":
            return self._send(200, sorted(run_dirs()))
        if u.path == "/api/results":
            d = run_dirs().get((q.get("run") or [""])[0])
            if not d:
                return self._send(404, {"error": "unknown run"})
            with open(os.path.join(d, "promos.json"), "rb") as f:
                return self._send(200, f.read())
        run = (q.get("run") or [""])[0]
        if u.path == "/api/clip" and run in run_dirs():
            try:
                spans = [tuple(float(x) for x in s.split("-")) for s in (q.get("spans") or [""])[0].split(",") if s]
                return self._send(200, clip_payload(run, int((q.get("ep") or ["0"])[0]), spans))
            except (ValueError, OSError) as e:
                return self._send(400, {"error": str(e)})
        if u.path == "/api/quality" and run in run_dirs():
            qp = os.path.join(run_dirs()[run], "quality.json")
            if os.path.isfile(qp):
                with open(qp, "rb") as f:
                    return self._send(200, f.read())
            return self._send(200, {})
        if u.path == "/api/runlog" and run in run_dirs():
            lp = os.path.join(run_dirs()[run], "run_log.json")
            if os.path.isfile(lp):
                with open(lp, "rb") as f:
                    return self._send(200, f.read())
            return self._send(200, {})
        if u.path == "/api/episodes" and run in run_dirs():
            d = scripts_for(run)
            if not d:
                return self._send(200, {"show": "", "episodes": {}})
            if d not in _EPS:
                _EPS[d] = {e.number: e for e in load_show(d)}
            show = os.path.basename(d.rstrip("\\/"))
            return self._send(200, {"show": show, "episodes": {str(n): {"duration": e.duration, "logline": e.logline}
                                                               for n, e in _EPS[d].items()}})
        if u.path == "/api/inspect":
            job = (q.get("job") or [""])[0]
            if not re.fullmatch(r"[a-z0-9]{6,24}", job) or not os.path.isdir(os.path.join(UPLOADS, job)):
                return self._send(404, {"ok": False, "error": "nothing uploaded yet"})
            return self._send(200, inspect_job(job))
        if u.path == "/api/file" and run in run_dirs():
            name = (q.get("name") or [""])[0]
            fp = os.path.join(run_dirs()[run], name)
            if name not in FILES_OK or not os.path.isfile(fp):
                return self._send(404, {"error": "no such file"})
            with open(fp, "rb") as f:
                return self._send(200, f.read(), "text/csv" if name.endswith(".csv") else "text/markdown" if name.endswith(".md") else "application/json")
        if u.path == "/api/slate" and run in run_dirs():
            sp = os.path.join(run_dirs()[run], "slate.json")
            if os.path.isfile(sp):
                with open(sp, "rb") as f:
                    return self._send(200, f.read())
            return self._send(200, [])
        if u.path == "/api/eval" and run in run_dirs():
            try:
                return self._send(200, eval_payload(run))
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        if u.path == "/api/status":
            with LOCK:
                running = bool(STATE["proc"]) and STATE["code"] is None
                return self._send(200, {"running": running, "code": STATE["code"], "run": STATE["out"],
                                        "log": STATE["log"][-120:],
                                        "seconds": round(time.time() - STATE["started"]) if STATE["proc"] else 0})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        n = int(self.headers.get("Content-Length") or 0)
        if n > (80_000_000 if u.path == "/api/upload_zip" else 20_000_000):
            return self._send(413, {"error": "too large"})
        body = self.rfile.read(n)
        if u.path == "/api/upload":
            job = (q.get("job") or [""])[0]
            name = os.path.basename((q.get("name") or [""])[0])
            if not re.fullmatch(r"[a-z0-9]{6,24}", job) or not name.lower().endswith(ALLOWED):
                return self._send(400, {"error": "only .txt, .md and .srt screenplay files (or one .zip of them) can be uploaded"})
            d = os.path.join(UPLOADS, job)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, name), "wb") as f:
                f.write(body)
            return self._send(200, {"ok": True})
        if u.path == "/api/upload_zip":
            job = (q.get("job") or [""])[0]
            if not re.fullmatch(r"[a-z0-9]{6,24}", job):
                return self._send(400, {"error": "bad upload"})
            try:
                return self._send(200, {"ok": True, "files": unzip_job(job, body)})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if u.path == "/api/run":
            try:
                return self._send(200, start_run(json.loads(body or b"{}")))
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        if u.path == "/api/evalcustom":
            try:
                b = json.loads(body or b"{}")
                return self._send(200, eval_custom(str(b.get("run", "")), list(b.get("picks") or [])))
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        if u.path == "/api/stop":
            with LOCK:
                p = STATE["proc"]
                if p and p.poll() is None:
                    p.terminate()
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


if __name__ == "__main__":
    if "--scripts" in sys.argv:       # python app.py --scripts <folder>: enables clip preview for existing runs
        DEFAULT_SCRIPTS = sys.argv[sys.argv.index("--scripts") + 1]
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Promo Finder running at http://127.0.0.1:{PORT}   (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
