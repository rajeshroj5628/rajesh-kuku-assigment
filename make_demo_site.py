"""Builds fronthand/index.html and fronthand/data/: a read-only, static copy of the dashboard for Vercel, from a completed run served by the local app.
The screenplay's own wording is withheld: every quoted line, plot summary and model explanation is replaced by blocks of the same length,
so the site shows the structure (episodes, times, scores, ranks, validation, evaluation) but not the script.

    python app.py --scripts "<folder>"      (another terminal)
    python make_demo_site.py --run out
"""
import argparse, json, os, re, shutil, urllib.parse, urllib.request

ap = argparse.ArgumentParser(); ap.add_argument("--run", default="out"); ap.add_argument("--base", default="http://127.0.0.1:8000"); a = ap.parse_args()
ROOT = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(ROOT, "fronthand"); DATA = os.path.join(OUT, "data")
shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
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

SHIM = r"""<script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>
<script>
/* Static demo of the real app. Same screens, same first page (upload only), but there is no server: uploads are read in this browser to count
   the episodes, and "Run" opens the results of the completed 50-episode run instead of calling a model. The page says so on screen. */
(function(){
  const real=window.fetch.bind(window), J=(o,c)=>new Response(JSON.stringify(o),{status:c||200,headers:{'Content-Type':'application/json'}});
  const JOBS={}; let T0=0;
  const decode=b=>new TextDecoder('utf-8').decode(b);
  async function textsOf(job){ const out=[];
    for(const f of (JOBS[job]||[])){
      if(f.zip){ try{ const z=await JSZip.loadAsync(f.buf); for(const n of Object.keys(z.files)){ if(z.files[n].dir||/__MACOSX|\/\./.test(n)||!/\.(txt|md|srt)$/i.test(n)) continue; out.push({name:n.split('/').pop(),text:await z.files[n].async('string')}); } }catch(e){} }
      else out.push({name:f.name,text:decode(f.buf)}); }
    return out.sort((x,y)=>x.name.localeCompare(y.name,undefined,{numeric:true})); }
  async function inspect(job){
    const files=await textsOf(job); if(!files.length) return {ok:false,error:'No .txt, .md or .srt episode files were found.'};
    let beats=0,dlg=0,tone=0,timed=0; const nums=[],secs=[];
    files.forEach((f,i)=>{ const m=f.text.match(/EPISODE NUMBER:\s*(\d+)/i); nums.push(m?+m[1]:(+((f.name.match(/(\d+)/)||[])[1])||i+1));
      const blocks=f.text.match(/^\*\*[^\n]*:\*\*\s*$/gm)||[]; beats+=blocks.length;
      blocks.forEach(b=>{ if(!/^\*\*(ACTION|SOUND|CAMERA)/i.test(b)){ dlg++; if(/\(/.test(b)) tone++; } });
      if(/Start:/i.test(f.text)) timed++;
      let mx=0; for(const e of f.text.matchAll(/End:\s*\(?(\d+):(\d+(?:\.\d+)?)/gi)) mx=Math.max(mx,+e[1]*60+ +e[2]); if(mx) secs.push(Math.round(mx)); });
    nums.sort((a,b)=>a-b);
    return {ok:true,episodes:nums,summary:{episodes:files.length,beats,dialogue_beats:dlg,with_tone:tone,untimed_blocks:0,timing:{[timed?'screenplay':'estimated']:files.length},episode_seconds:secs.length?[Math.min(...secs),Math.max(...secs)]:[0,0]},
      messages:['Demo site: the files were only counted in your browser. Run opens the results of the completed 50-episode run; the real analysis needs the Python app (see the README).']}; }
  const LOG=['[demo] This site is a static demo: there is no server and no model behind it.','[demo] Your files were counted in your browser only.','[demo] Opening the results of the completed 50-episode run (a real run made earlier with the Python app).'];
  window.fetch=function(u,o){
    if(typeof u==='string'&&u.startsWith('/api/')){ const url=new URL(u,location.href), p=url.pathname, q=url.searchParams, post=o&&o.method==='POST';
      if(post&&(p==='/api/upload'||p==='/api/upload_zip')){ const j=q.get('job'); (JOBS[j]=JOBS[j]||[]).push({name:q.get('name')||'episodes.zip',zip:p==='/api/upload_zip',buf:o.body}); return Promise.resolve(J({ok:true})); }
      if(post&&p==='/api/run'){ T0=Date.now(); return Promise.resolve(J({ok:true})); }
      if(post) return Promise.resolve(J({error:'Not available in the demo.'},403));
      if(p==='/api/inspect') return inspect(q.get('job')).then(r=>J(r));
      if(p==='/api/status'){ if(!T0) return Promise.resolve(J({running:false,code:null,run:null,seconds:0,log:[]}));
        const s=(Date.now()-T0)/1000, n=s<1?1:(s<2?2:3), done=s>=3.2;
        return Promise.resolve(J({running:!done,code:done?0:null,run:'out',seconds:Math.round(Math.min(s,3.2)*10)/10,log:LOG.slice(0,n).concat(done?['Done.']:[])})); }
      if(p==='/api/runs') return Promise.resolve(J(['out']));
      if(p==='/api/file') return q.get('name')==='slate.md'?real('data/slate.md'):Promise.resolve(new Response('',{status:404}));
      if(p==='/api/clip'){ const f=x=>Number(x).toFixed(2), sp=(q.get('spans')||'').split('-');
        return real('data/clips.json').then(r=>r.json()).then(c=>J(c[q.get('ep')+'|'+f(sp[0])+'|'+f(sp[1])]||{available:false})); }
      const map={'/api/results':'results','/api/quality':'quality','/api/runlog':'runlog','/api/episodes':'episodes','/api/slate':'slate','/api/eval':'eval'};
      if(map[p]) return real('data/'+map[p]+'.json'); return Promise.resolve(J({error:'not available in the demo'},404)); }
    return real(u,o); };
  document.addEventListener('DOMContentLoaded',()=>{
    const b=document.createElement('div'); b.id='demobar';
    b.innerHTML='Demo of PromoFinder &middot; results are from a completed 50-episode run &middot; the screenplay\u2019s own wording is hidden (shown as blocks) &middot; <a href="https://github.com/rajeshroj5628/rajesh-kuku-assigment" target="_blank" rel="noopener">run it for real: source code</a>';
    b.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:9999;background:#ff6a4a;color:#fff;font:600 13px Segoe UI,system-ui,sans-serif;padding:7px 14px;text-align:center';
    b.querySelector('a').style.cssText='color:#fff;text-decoration:underline'; document.body.appendChild(b); document.body.style.paddingBottom='34px';
    const sub=document.querySelector('#v-run .panel .sub'); if(sub) sub.innerHTML='<b>Demo site.</b> Add your episode files: they are counted in this browser only. <b>Run</b> then opens the results of the completed 50-episode run, because the real analysis (model calls) needs the Python app from the GitHub repository.'; });
})();
</script>"""
html = open(os.path.join(OUT, "app.html"), encoding="utf-8").read()
assert html.count("<script>") >= 1
html = html.replace("<script>", SHIM + "\n<script>", 1)
open(os.path.join(OUT, "index.html"), "w", encoding="utf-8", newline="\n").write(html)
json.dump({"cleanUrls": True, "headers": [{"source": "/data/(.*)", "headers": [{"key": "Cache-Control", "value": "public, max-age=300"}]}]}, open(os.path.join(OUT, "vercel.json"), "w"), indent=1)
open(os.path.join(OUT, "README.md"), "w", encoding="utf-8").write("# PromoFinder - read-only demo\n\nStatic copy of the dashboard for a completed run. Deploy with Vercel: framework preset **Other**, root directory `fronthand`, no build command.\nThe screenplay text is replaced by blocks (see `make_demo_site.py`). Uploading and running need the real app: `python app.py`.\n")
print("fronthand built:", sum(len(f) for _, _, f in os.walk(OUT)), "files")

