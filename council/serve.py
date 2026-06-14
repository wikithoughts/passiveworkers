#!/usr/bin/env python3
"""
council/serve.py — the local research desk (single-user UI for council.local)
==============================================================================
    python -m council.serve          # → http://127.0.0.1:8770
    pw serve

One process, one user, no auth, no accounts, no map, no telemetry: a brief box, live
progress while your models research, the rendered report, and the history of every
report in ./reports/. The network app (council/net) is the separate multiplayer mode.
"""

from __future__ import annotations

import os
import pathlib
import threading
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from council.local import run as run_research

app = FastAPI(title="Passive Workers — local research desk")
REPORTS = pathlib.Path("reports")
_jobs: dict = {}          # id → {"log": [..], "done": bool, "file": str|None, "error": str|None}
_lock = threading.Lock()


class Brief(BaseModel):
    brief: str = Field(..., max_length=4000)
    depth: str = Field(default="standard", pattern="^(quick|standard|deep)$")
    analysts: int = Field(default=3, ge=1, le=4)


def _work(job_id: str, b: Brief) -> None:
    j = _jobs[job_id]

    def progress(msg: str) -> None:
        with _lock:
            j["log"].append(msg)

    try:
        path = run_research(b.brief, depth=b.depth, n_analysts=b.analysts,
                            on_progress=progress)
        with _lock:
            j["file"], j["done"] = path.name, True
    except (Exception, SystemExit) as e:
        # run_research raises SystemExit on the common first-run failures (Ollama down, no models).
        # SystemExit is a BaseException, NOT caught by `except Exception` — without it the job would
        # hang at done=False forever. Record the error and mark done so the desk shows it cleanly.
        with _lock:
            j["error"], j["done"] = f"{type(e).__name__}: {e}", True


@app.post("/research")
def research(b: Brief):
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {"log": [], "done": False, "file": None, "error": None}
    threading.Thread(target=_work, args=(job_id, b), daemon=True).start()
    return {"job_id": job_id}


@app.get("/progress/{job_id}")
def progress(job_id: str):
    j = _jobs.get(job_id)
    if not j:
        raise HTTPException(404)
    with _lock:
        return {"log": list(j["log"]), "done": j["done"], "file": j["file"], "error": j["error"]}


@app.get("/reports")
def reports():
    if not REPORTS.exists():
        return []
    return sorted((p.name for p in REPORTS.glob("*.md")), reverse=True)


@app.get("/report/{name}", response_class=PlainTextResponse)
def report(name: str):
    p = REPORTS / pathlib.Path(name).name      # basename only — no traversal
    if not p.exists() or p.suffix != ".md":
        raise HTTPException(404)
    return p.read_text()


@app.get("/", response_class=HTMLResponse)
def home():
    from council import get_version
    return SERVE_HTML.replace("__PW_VERSION__", get_version())


SERVE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Research desk — Passive Workers</title>
<style>
:root{--bg:#0b1020;--ink:#e8ecff;--muted:#a7b6e0;--edge:#21305e;--card:#101736;--cardin:#0c1430;
  --acc:#6ea8ff;--bad:#f87272;--btn:#2447b2;--btn-edge:#2e57d6}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,Segoe UI,Inter,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:28px 18px}
h1{font-size:20px;margin:0 0 2px} .sub{color:var(--muted);font-size:13px;margin-bottom:18px}
textarea{width:100%;box-sizing:border-box;min-height:84px;background:var(--cardin);color:var(--ink);
  border:1px solid var(--edge);border-radius:12px;padding:11px;font:inherit;resize:vertical}
select,button{background:var(--cardin);color:var(--ink);border:1px solid var(--edge);border-radius:10px;
  padding:8px 12px;font:inherit;cursor:pointer;transition:filter .12s ease,transform .02s ease}
button:hover,select:hover{filter:brightness(1.12)} button:active{transform:translateY(1px)}
button:focus-visible,select:focus-visible,textarea:focus-visible,a:focus-visible{
  outline:2px solid var(--acc);outline-offset:2px;border-radius:8px}
button.go{background:var(--btn);border-color:var(--btn-edge);font-weight:600;margin-left:auto}
.row{display:flex;gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap}
.card{background:var(--card);border:1px solid var(--edge);border-radius:14px;padding:14px 16px;margin-top:16px}
.muted{color:var(--muted)} .log div{font-size:12.5px;color:var(--muted);padding:1px 0}
.err{color:var(--bad)}
.spin{display:none;width:12px;height:12px;margin-right:7px;vertical-align:-1px;border:2px solid var(--edge);
  border-top-color:var(--acc);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){.spin{animation:none}}
.report h1,.report h3{margin:14px 0 6px}.report h4{margin:10px 0 4px}
.report a{color:var(--acc);word-break:break-all}
.hist div{cursor:pointer;padding:6px 2px;border-top:1px dashed #1b2750}
.hist div:hover{color:#fff}
.pwfoot{margin-top:22px;padding-top:12px;border-top:1px solid var(--edge);color:var(--muted);font-size:11.5px}
.pwfoot a{color:var(--acc)}
@media(max-width:820px){.wrap{padding:20px 12px}}
@media(max-width:480px){.row{gap:8px}.go{margin-left:0;width:100%}select{flex:1 1 auto}}
</style></head><body><div class="wrap">
<h1><span aria-hidden="true">🌍</span> Passive Workers</h1>
<div class="sub">Research desk · your models, your connection, your disk — nothing leaves this machine but the web searches.</div>
<textarea id="brief" aria-label="Research brief" placeholder="What should be researched?"></textarea>
<div class="row">
  <select id="depth" aria-label="Research depth"><option value="quick">quick (~2–5 min)</option>
    <option value="standard" selected>standard (~5–15 min)</option>
    <option value="deep">deep (~15–30 min)</option></select>
  <select id="analysts" aria-label="Number of analysts"><option>1</option><option>2</option><option selected>3</option><option>4</option></select>
  <span class="muted">local models as independent analysts</span>
  <button class="go" id="go" aria-label="Start research">Research →</button>
</div>
<div class="card" id="live" style="display:none" aria-live="polite"><span class="spin" id="spin" aria-hidden="true"></span><b id="status">working…</b><div class="log" id="log" role="log" aria-label="Research progress"></div></div>
<div class="card report" id="out" style="display:none"></div>
<div class="card hist"><b><span aria-hidden="true">📄</span> Past reports</b><div id="hist" class="muted">none yet</div></div>
<footer class="pwfoot" aria-label="About">Passive&nbsp;Workers v__PW_VERSION__ · <a href="https://github.com/wikithoughts/passiveworkers" target="_blank" rel="noopener">GitHub</a></footer>
<script>
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function md(t){
  let h=esc(t||'');
  h=h.replace(/^### (.*)$/gm,'<h4>$1</h4>').replace(/^## (.*)$/gm,'<h3>$1</h3>').replace(/^# (.*)$/gm,'<h1>$1</h1>');
  h=h.replace(/\\*\\*([^*]+)\\*\\*/g,'<b>$1</b>');
  h=h.replace(/(https?:\\/\\/[^\\s<)\\]]+)/g,'<a href="$1" target="_blank" rel="noopener">$1</a>');
  h=h.replace(/\\n{2,}/g,'</p><p>').replace(/\\n/g,'<br>');
  return '<p>'+h+'</p>';
}
async function refreshHist(){
  try{const l=await (await fetch('/reports')).json();
    const el=document.getElementById('hist');
    if(!l.length){el.textContent='none yet';return}
    el.innerHTML=l.map(n=>'<div onclick="openReport(\\''+esc(n)+'\\')">'+esc(n)+'</div>').join('');
  }catch(e){}
}
async function openReport(name){
  const t=await (await fetch('/report/'+encodeURIComponent(name))).text();
  const o=document.getElementById('out');o.style.display='';o.innerHTML=md(t);
  o.scrollIntoView({behavior:'smooth'});
}
let timer=null;
document.getElementById('go').onclick=async()=>{
  const brief=document.getElementById('brief').value.trim();if(!brief)return;
  const body={brief:brief,depth:document.getElementById('depth').value,
              analysts:+document.getElementById('analysts').value};
  const r=await fetch('/research',{method:'POST',headers:{'Content-Type':'application/json'},
                                   body:JSON.stringify(body)});
  const j=await r.json();
  document.getElementById('live').style.display='';document.getElementById('out').style.display='none';
  document.getElementById('log').innerHTML='';
  const st=document.getElementById('status'),sp=document.getElementById('spin');
  st.className='';st.textContent='working…';sp.style.display='inline-block';
  if(timer)clearInterval(timer);
  timer=setInterval(async()=>{
    const p=await (await fetch('/progress/'+j.job_id)).json();
    document.getElementById('log').innerHTML=p.log.map(l=>'<div>'+esc(l)+'</div>').join('');
    if(p.done){clearInterval(timer);timer=null;sp.style.display='none';
      st.className=p.error?'err':'';st.textContent=p.error?('✗ '+p.error):'done ✓';
      if(p.file){openReport(p.file)}
      refreshHist();
    }
  },1500);
};
refreshHist();
</script></div></body></html>
"""


def main() -> None:
    import uvicorn
    host = os.environ.get("PW_SERVE_HOST", "127.0.0.1")  # 0.0.0.0 only inside containers
    print(f"🔬 Research desk → http://{host}:8770  (Ctrl-C to stop)", flush=True)
    uvicorn.run(app, host=host, port=8770, log_level="warning")


if __name__ == "__main__":
    main()
