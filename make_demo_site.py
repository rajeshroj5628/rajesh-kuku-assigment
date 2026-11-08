"""Builds demo_site/: a read-only, static copy of the dashboard for Vercel, from a completed run served by the local app.
The screenplay's own wording is withheld: every quoted line, plot summary and model explanation is replaced by blocks of the same length,
so the site shows the structure (episodes, times, scores, ranks, validation, evaluation) but not the script.

    python app.py --scripts "<folder>"      (another terminal)
    python make_demo_site.py --run out
"""
import argparse, json, os, re, shutil, urllib.parse, urllib.request

ap = argparse.ArgumentParser(); ap.add_argument("--run", default="out"); ap.add_argument("--base", default="http://127.0.0.1:8000"); a = ap.parse_args()
ROOT = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(ROOT, "demo_site"); DATA = os.path.join(OUT, "data")
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(DATA)
get = lambda p: urllib.request.urlopen(a.base + p).read()
Q = "?run=" + urllib.parse.quote(a.run)

BLOCK = lambda s: re.sub(r"[^\W_]", "\u2588", s)                     # letters and digits -> blocks, spaces and punctuation stay
PREFIX = re.compile(r"^((?:\[[\d:.]+\]\s*)?(?:[A-Z][A-Z0-9 .'\-]*(?:\s*\([^)]*\))?:\s*))(.*)$", re.S)
def hide(s):
    if not isinstance(s, str) or not s: return s
    m = PREFIX.match(s)                                              # keep "ALEXANDER (Cold): " so the UI can still strip the speaker
    return m.group(1) + BLOCK(m.group(2)) if m else BLOCK(s)
TEXT_KEYS = {"hook_line", "cliffhanger_line", "why", "spoiler_reason", "stranger_who", "stranger_conflict", "stranger_missing", "note", "logline", "heading", "text"}
def walk(x):
    if isinstance(x, list): return [walk(v) for v in x]
    if isinstance(x, dict): return {k: (hide(v) if k in TEXT_KEYS and isinstance(v, str) else walk(v)) for k, v in x.items()}
    return x
def save(name, obj): open(os.path.join(DATA, name), "w", encoding="utf-8").write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

results = json.loads(get("/api/results" + Q)); save("results.json", walk(results))
for n in ("quality", "runlog", "episodes", "slate", "eval"): save(n + ".json", walk(json.loads(get(f"/api/{n}{Q}"))))
# clip detail for the preview window: every segment of every clip
clips, f2 = {}, lambda x: f"{float(x):.2f}"
for p in results:
    for s in p["segments"]:
        key = f"{s['episode']}|{f2(s['start'])}|{f2(s['end'])}"
        if key in clips: continue
        try: clips[key] = walk(json.loads(get(f"/api/clip{Q}&ep={s['episode']}&spans={s['start']}-{s['end']}")))
        except Exception as e: clips[key] = {"available": False}
save("clips.json", clips)
md = get("/api/file" + Q + "&name=slate.md").decode("utf-8")
md = re.sub(r'^(- (?:Hook|Cliffhanger|Why|Spoiler risk): )(.*)$', lambda m: m.group(1) + hide(m.group(2)), md, flags=re.M)
open(os.path.join(DATA, "slate.md"), "w", encoding="utf-8", newline="\n").write(md)

SHIM = r"""<script>
/* read-only demo: the same page as the real app, but its /api calls are answered from files in /data */
(function(){ try{ sessionStorage.setItem('pf_open','1'); }catch(e){}
  const real=window.fetch.bind(window), J=(o,c)=>new Response(JSON.stringify(o),{status:c||200,headers:{'Content-Type':'application/json'}});
  const NOPE='This is a read-only demo. To upload episodes and run the analysis, start the app on your computer (see the README).';
  window.fetch=function(u,o){
    if(typeof u==='string'&&u.startsWith('/api/')){ const url=new URL(u,location.href), p=url.pathname, q=url.searchParams;
      if(o&&o.method==='POST') return Promise.resolve(J({error:NOPE},403));
      if(p==='/api/runs') return Promise.resolve(J(['out']));
      if(p==='/api/status') return Promise.resolve(J({running:false,code:0,run:'out',log:[]}));
      if(p==='/api/inspect') return Promise.resolve(J({ok:false,error:NOPE}));
      if(p==='/api/file') return q.get('name')==='slate.md'?real('data/slate.md'):Promise.resolve(new Response('',{status:404}));
      if(p==='/api/clip'){ const f=x=>Number(x).toFixed(2), sp=(q.get('spans')||'').split('-');
        return real('data/clips.json').then(r=>r.json()).then(c=>J(c[q.get('ep')+'|'+f(sp[0])+'|'+f(sp[1])]||{available:false})); }
      const map={'/api/results':'results','/api/quality':'quality','/api/runlog':'runlog','/api/episodes':'episodes','/api/slate':'slate','/api/eval':'eval'};
      if(map[p]) return real('data/'+map[p]+'.json'); return Promise.resolve(J({error:'not available in the demo'},404)); }
    return real(u,o); };
  document.addEventListener('DOMContentLoaded',()=>{ const b=document.createElement('div'); b.id='demobar';
    b.innerHTML='Read-only demo of a completed 50-episode run &middot; the screenplay\u2019s own wording is withheld (shown as blocks) &middot; <a href="https://github.com/rajeshroj5628/rajesh-kuku-assigment" target="_blank" rel="noopener">source code</a>';
    b.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:9999;background:#ff6a4a;color:#fff;font:600 13px Segoe UI,system-ui,sans-serif;padding:7px 14px;text-align:center';
    b.querySelector('a').style.cssText='color:#fff;text-decoration:underline'; document.body.appendChild(b); document.body.style.paddingBottom='34px'; });
})();
</script>"""
html = open(os.path.join(ROOT, "ui", "app.html"), encoding="utf-8").read()
assert html.count("<script>") >= 1
html = html.replace("<script>", SHIM + "\n<script>", 1)
open(os.path.join(OUT, "index.html"), "w", encoding="utf-8", newline="\n").write(html)
json.dump({"cleanUrls": True, "headers": [{"source": "/data/(.*)", "headers": [{"key": "Cache-Control", "value": "public, max-age=300"}]}]}, open(os.path.join(OUT, "vercel.json"), "w"), indent=1)
open(os.path.join(OUT, "README.md"), "w", encoding="utf-8").write("# PromoFinder - read-only demo\n\nStatic copy of the dashboard for a completed run. Deploy with Vercel: framework preset **Other**, root directory `demo_site`, no build command.\nThe screenplay text is replaced by blocks (see `make_demo_site.py`). Uploading and running need the real app: `python app.py`.\n")
print("demo_site built:", sum(len(f) for _, _, f in os.walk(OUT)), "files")
