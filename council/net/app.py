#!/usr/bin/env python3
"""
council/net/app.py — the Living Council Map (Phase E, map-forward)
=================================================================
The end-user experience, served by the coordinator at GET /. A single self-contained
page (Leaflet from CDN; no build step). You sign in with a handle, ask the council a
question, and WATCH a global council of diverse minds deliberate on a world map — nodes
glow while thinking, arcs flow asker→nodes→judge→answer — then the side panel shows the
terse merge (TL;DR), where the minds AGREE vs DIFFER (by country), and a one-tap compare
of the council vs a single model with a ▲/▼ "was it more useful?" vote (the demand signal).

All server-supplied strings are HTML-escaped (same XSS discipline as the dashboard).
"""

APP_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Passive Workers — the Council</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  :root{--bg:#0a0e1c;--panel:#0f1730;--edge:#21305e;--ink:#e6ecff;--mut:#8aa0d0;
        --good:#36d399;--warn:#fbbd23;--bad:#f87272;--acc:#6ea8ff;}
  *{box-sizing:border-box} html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);
    font:14.5px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
  #wrap{display:grid;grid-template-columns:1fr 400px;height:100vh}
  #map{height:100vh;background:#0a0e1c}
  #panel{background:var(--panel);border-left:1px solid var(--edge);overflow:auto;padding:18px 18px 40px}
  h1{font-size:17px;margin:0} .brand{display:flex;justify-content:space-between;align-items:baseline}
  .sub{color:var(--mut);font-size:12px;margin:2px 0 14px}
  .me{font-size:12.5px;color:var(--mut)} .me b{color:#fff}
  textarea{width:100%;background:#0c1430;color:var(--ink);border:1px solid var(--edge);border-radius:10px;
    padding:10px 12px;font:inherit;resize:vertical;min-height:64px}
  button{font:inherit;cursor:pointer;border:0;border-radius:10px;padding:9px 14px;color:#04122e;
    background:var(--acc);font-weight:600} button.ghost{background:#1b2750;color:var(--ink)}
  button:disabled{opacity:.5;cursor:default}
  .row{display:flex;gap:8px;align-items:center} .between{justify-content:space-between}
  .card{background:#0c1430;border:1px solid var(--edge);border-radius:12px;padding:12px 14px;margin:12px 0}
  .persp{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px dashed #1b2750}
  .persp:last-child{border-bottom:0}
  .dot{width:9px;height:9px;border-radius:50%;display:inline-block;flex:0 0 auto}
  .pill{font-size:11px;padding:2px 7px;border-radius:999px;background:#1b2750;color:var(--mut)}
  .flag{font-size:16px} .muted{color:var(--mut)} .tl{font-size:15px;line-height:1.55}
  h3{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:14px 0 6px}
  ul{margin:4px 0;padding-left:18px} li{margin:3px 0}
  .agree li{color:#bfe9d4} .differ li{color:#ffd9a8}
  .vote button{padding:7px 12px} .thanks{color:var(--good);font-size:13px}
  .leaflet-popup-content-wrapper,.leaflet-popup-tip{background:#0f1730;color:var(--ink)}
  @keyframes pulse{0%,100%{stroke-opacity:.35;fill-opacity:.15}50%{stroke-opacity:1;fill-opacity:.55}}
  .thinking{animation:pulse 1.15s infinite}
  @keyframes flow{to{stroke-dashoffset:-20}}
  .arc{stroke-dasharray:3 7;animation:flow .7s linear infinite}
  @media(max-width:820px){#wrap{grid-template-columns:1fr;grid-template-rows:42vh 1fr}#map{height:42vh}}
</style>
</head>
<body>
<div id="wrap">
  <div id="map"></div>
  <div id="panel">
    <div class="brand"><h1>🌍 Passive&nbsp;Workers</h1><span class="me" id="me"></span></div>
    <div class="sub">A global council of diverse minds — watch them deliberate.</div>
    <div class="sub" id="netstat" style="margin-top:-10px"></div>

    <div id="auth" class="card" style="display:none">
      <div class="row between"><b>Pick a handle to begin</b></div>
      <div class="row" style="margin-top:8px"><input id="handle" placeholder="e.g. ahmed"
        style="flex:1;background:#0c1430;color:var(--ink);border:1px solid var(--edge);border-radius:10px;padding:9px 11px;font:inherit"/>
        <button id="signin">Start</button></div>
      <div class="muted" id="authmsg" style="margin-top:6px;font-size:12px"></div>
    </div>

    <div id="askbox">
      <textarea id="q" placeholder="Ask the council anything…"></textarea>
      <div class="row between" style="margin-top:8px">
        <span class="muted" id="hint">3 diverse minds will answer · costs 35 credits</span>
        <span class="row" style="gap:8px">
          <select id="minds" title="how many minds answer (cost scales)"
            style="background:#0c1430;color:var(--ink);border:1px solid var(--edge);border-radius:8px;padding:6px 8px;font:inherit">
            <option>1</option><option>2</option><option selected>3</option><option>4</option><option>5</option>
          </select>
          <button id="ask">Ask the council →</button>
        </span>
      </div>
    </div>

    <div id="live" style="display:none"></div>
    <div id="answer" style="display:none"></div>

    <details class="card" id="histcard" style="display:none;margin-top:14px">
      <summary class="muted">🕘 My questions</summary>
      <div id="hist"></div>
    </details>

    <details class="card" style="margin-top:18px"><summary class="muted">⏻ Contribute your computer (earn credits)</summary>
      <div class="muted" style="font-size:12.5px;margin-top:8px">Run a worker so your machine joins the council and earns credits for your handle:</div>
      <pre id="contribute" style="white-space:pre-wrap;background:#0a1126;border:1px solid var(--edge);border-radius:8px;padding:8px;font-size:11.5px;overflow:auto"></pre>
    </details>
  </div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const CENTROIDS={FI:[61.9,25.7],AE:[23.4,53.8],US:[39.8,-98.6],DE:[51.2,10.4],BR:[-14.2,-51.9],
 GB:[54,-2],FR:[46.6,2.2],IN:[21,78],SG:[1.35,103.8],JP:[36.2,138.3],NL:[52.1,5.3],CA:[56,-106],
 AU:[-25,133],ZA:[-29,24],NG:[9,8],KE:[0.2,37.9],EG:[26,30],SA:[24,45],IQ:[33,44],TR:[39,35],
 RU:[61,105],CN:[35,105],KR:[36,128],ID:[-2,118],VN:[14,108],MX:[23,-102],ES:[40,-3.7],IT:[42.8,12.8],
 SE:[62,15],PL:[52,19]};
const YOU=[25.2,55.3];  // asker anchor (overridden by geolocation if allowed)
let you=YOU.slice();
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function cc(s){return String(s||'').replace(/^sim-/,'').toUpperCase()}
function flag(s){const c=cc(s);if(!/^[A-Z]{2}$/.test(c))return '🖥';return String.fromCodePoint(...[...c].map(x=>0x1F1A5+x.charCodeAt(0)))}
function centroid(country){return CENTROIDS[cc(country)]||[10,-30]}
function jit(k){k=String(k||'');let h=0;for(const ch of k)h=(h*31+ch.charCodeAt(0))&255;return (h/255-.5)*7}
function statusColor(s){return s==='answered'?'#36d399':s==='thinking'?'#fbbd23':'#6ea8ff'}

const map=L.map('map',{worldCopyJump:true,zoomControl:false}).setView([28,20],2.4);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{maxZoom:8,attribution:'© OSM © CARTO'}).addTo(map);
const ambient=L.layerGroup().addTo(map), jobLayer=L.layerGroup().addTo(map);

// ---- auth ----
let handle=localStorage.getItem('pw_handle'), secret=localStorage.getItem('pw_secret');
function uHeaders(){return secret?{'X-User-Secret':secret,'Content-Type':'application/json'}:{'Content-Type':'application/json'}}
async function refreshMe(){
  const auth=document.getElementById('auth'),askbox=document.getElementById('askbox');
  if(!secret){auth.style.display='block';askbox.style.display='none';document.getElementById('me').textContent='';return}
  auth.style.display='none';askbox.style.display='';
  try{const m=await (await fetch('/me',{headers:uHeaders()})).json();
    document.getElementById('me').innerHTML='@'+esc(m.handle)+' · <b>'+m.balance+'</b> cr';
    document.getElementById('contribute').textContent=
      'PW_COORDINATOR='+location.origin+' PW_TOKEN=<operator-token> \\\n  PW_OWNER='+m.handle+
      ' PW_NAME=my-pc PW_COUNTRY=<XX> PW_ANSWER_MODEL=gemma3:4b PW_LENS=practical \\\n  python -m council.net.agent';
  }catch(e){}
  refreshHistory();
}
// ---- question history (persistent, deep-linkable artifacts) ----
async function refreshHistory(){
  if(!secret)return;
  try{
    const r=await fetch('/jobs/mine',{headers:uHeaders()});if(!r.ok)return;
    const l=await r.json(),hc=document.getElementById('histcard'),el=document.getElementById('hist');
    if(!l.length){hc.style.display='none';return}
    hc.style.display='';
    el.innerHTML=l.map(j=>{
      const ic=j.status==='done'?'✓':j.status==='failed'?'✗':'…';
      return '<div style="cursor:pointer;padding:6px 2px;border-top:1px dashed #1b2750" '+
        'onclick="openJob(\''+esc(j.job_id)+'\')">'+ic+' '+esc((j.question||'').slice(0,90))+'</div>';
    }).join('');
  }catch(e){}
}
document.getElementById('signin').onclick=async()=>{
  const h=document.getElementById('handle').value.trim();if(!h)return;
  const r=await fetch('/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({handle:h})});
  if(r.status===409){document.getElementById('authmsg').textContent='“'+h+'” is taken — try another.';return}
  if(!r.ok){document.getElementById('authmsg').textContent='Could not start. Try again.';return}
  const d=await r.json();handle=d.handle;secret=d.user_secret;
  localStorage.setItem('pw_handle',handle);localStorage.setItem('pw_secret',secret);
  document.getElementById('auth').style.display='none';refreshMe();
};

// ---- ambient online nodes (so the map breathes before you ask) ----
function byMachine(arr){const m={};for(const x of arr||[]){(m[x.machine_key]=m[x.machine_key]||{country:x.country,items:[]}).items.push(x);}return m;}
async function ambientTick(){
  try{const d=await (await fetch('/status')).json();ambient.clearLayers();
    const hdr=document.getElementById('netstat');
    if(hdr)hdr.textContent='🖥 '+(d.machines||0)+' machine'+((d.machines===1)?'':'s')+' · '+(d.minds||0)+' minds online';
    const byM=byMachine(d.online_nodes);
    for(const mk in byM){const m=byM[mk],c=centroid(m.country);
      L.circleMarker([c[0]+jit(mk),c[1]+jit(mk+'x')],
        {radius:5,color:'#33406b',fillColor:'#33406b',fillOpacity:.55,weight:1}).addTo(ambient)
        .bindPopup('<b>'+flag(m.country)+' '+esc(cc(m.country))+'</b><br>'+m.items.length+' mind(s): '+
          esc(m.items.map(x=>x.answer_model||'judge').join(', ')));
    }
  }catch(e){}
}

// ---- ask + live render ----
let polling=null,bwait=0,lastAns='';
function openJob(id){            // watch any job live (new ask, history click, or #job= link)
  if(polling)clearInterval(polling);
  bwait=0;lastAns='';
  document.getElementById('answer').style.display='none';
  location.hash='job='+id;
  poll(id);polling=setInterval(()=>poll(id),1500);
}
// responder dial: cost preview scales with minds (per-mind 10 cr + judge 5, server defaults)
function updateHint(){
  const n=+(document.getElementById('minds').value||3);
  document.getElementById('hint').textContent=
    n+' diverse mind'+(n===1?'':'s')+' will answer · costs '+(n*10+5)+' credits';
}
document.getElementById('minds').onchange=updateHint;
document.getElementById('ask').onclick=async()=>{
  if(!secret){document.getElementById('auth').style.display='block';return}
  const q=document.getElementById('q').value.trim();if(!q)return;
  document.getElementById('answer').style.display='none';
  const minds=+(document.getElementById('minds').value||3);
  const r=await fetch('/jobs',{method:'POST',headers:uHeaders(),body:JSON.stringify({question:q,minds:minds})});
  const j=await r.json();
  if(j.status==='failed'){document.getElementById('live').style.display='block';
    document.getElementById('live').innerHTML='<div class="card" style="color:var(--bad)">✗ '+esc(j.error||'failed')+'</div>';return}
  if(j.balance)document.getElementById('me').innerHTML='@'+esc(j.balance.handle)+' · <b>'+j.balance.balance+'</b> cr';
  openJob(j.job_id);
};

function drawMap(v){
  jobLayer.clearLayers();
  L.circleMarker(you,{radius:7,color:'#fff',fillColor:'#6ea8ff',fillOpacity:.9,weight:2}).addTo(jobLayer).bindPopup('you (asker)');
  const jc=v.judge_country?centroid(v.judge_country):null;
  // group minds by physical machine — one marker per computer (honest topology)
  const byM=byMachine(v.answers);
  for(const mk in byM){const m=byM[mk],c=centroid(m.country);
    const p=[c[0]+jit(mk),c[1]+jit(mk+'x')];
    const anyThinking=m.items.some(x=>x.status_label==='thinking');
    const allAnswered=m.items.every(x=>x.status_label==='answered');
    const col=allAnswered?'#36d399':anyThinking?'#fbbd23':'#6ea8ff';
    L.polyline([you,p],{color:col,weight:1.4,opacity:.55,className:'arc'}).addTo(jobLayer);
    if(jc&&allAnswered&&mk!==v.judge_machine_key)L.polyline([p,jc],{color:'#6ea8ff',weight:1.2,opacity:.4,className:'arc'}).addTo(jobLayer);
    L.circleMarker(p,{radius:9,color:col,fillColor:col,fillOpacity:.5,weight:2,className:anyThinking?'thinking':''}).addTo(jobLayer)
      .bindPopup('<b>'+flag(m.country)+' '+esc(cc(m.country))+'</b> — '+m.items.length+' mind(s)<br>'+
        m.items.map(x=>esc(x.model)+' · '+esc(x.lens)+' — '+esc(x.status_label)+(x.score!=null?' '+x.score:'')).join('<br>'));
  }
  if(jc)L.circleMarker(jc,{radius:9,color:'#c4b5fd',fillColor:'#c4b5fd',
    fillOpacity:v.judge_status==='done'?.6:.25,weight:2,className:v.judge_status==='claimed'?'thinking':''})
    .addTo(jobLayer).bindPopup('⚖️ judge · '+esc(cc(v.judge_country)));
}

function renderLive(v){
  const el=document.getElementById('live');el.style.display='block';
  const machines=new Set((v.answers||[]).map(a=>a.machine_key)).size;
  let h='<div class="card"><div class="row between"><b>The council is '+
    (v.status==='judging'?'deliberating ⚖️':v.status==='done'?'decided ✓':'thinking…')+'</b>'+
    '<span class="muted">'+machines+' machine'+(machines===1?'':'s')+' · '+(v.answers||[]).filter(a=>a.status_label==='answered').length+'/'+(v.answers||[]).length+' minds</span></div>';
  for(const a of v.answers||[]){
    h+='<div class="persp"><span class="dot" style="background:'+statusColor(a.status_label)+'"></span>'+
       '<span class="flag">'+flag(a.country)+'</span><span><b>'+esc(cc(a.country))+'</b> '+
       '<span class="muted">'+esc(a.model)+' · '+esc(a.lens)+'</span></span>'+
       '<span class="pill" style="margin-left:auto">'+esc(a.status_label)+(a.score!=null?' '+a.score:'')+'</span></div>';
  }
  el.innerHTML=h+'</div>';
}

function renderAnswer(v){
  const A=document.getElementById('answer');A.style.display='block';
  const co=v.council||{};
  const ext=(v.baseline&&v.baseline.text)?v.baseline:null;     // independent single model
  const base=ext||(v.answers||[]).find(a=>a.is_baseline);      // fallback: best council mind
  let h='<div class="card"><h3>The council’s answer</h3><div class="tl">'+esc(v.merged||'')+'</div>';
  if((co.consensus||[]).length){h+='<h3>Where they agree</h3><ul class="agree">'+co.consensus.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>'}
  if((co.disagreements||[]).length){h+='<h3>Where they differ</h3><ul class="differ">'+
    co.disagreements.map(d=>'<li>'+esc(d.point)+(d.sides?(' — <span class="muted">'+esc(d.sides)+'</span>'):'')+'</li>').join('')+'</ul>'}
  if((co.unique||[]).length){h+='<h3>Only one mind raised</h3><ul>'+co.unique.map(u=>{
     const who=(v.answers||[]).find(a=>a.worker_id===u.worker_id);
     return '<li>'+(who?flag(who.country)+' ':'')+esc(u.point)+'</li>'}).join('')+'</ul>'}
  h+='</div>';
  // A2 — every node's individual answer (read each one)
  h+='<details class="card"><summary class="muted">▸ The '+(v.answers||[]).length+' individual answers (read each mind)</summary>';
  for(const a of v.answers||[]){
    h+='<div style="margin-top:10px;border-top:1px dashed #1b2750;padding-top:8px"><div class="row between">'+
       '<b>'+flag(a.country)+' '+esc(cc(a.country))+' <span class="muted">'+esc(a.model)+' · '+esc(a.lens)+'</span></b>'+
       '<span class="pill">'+(a.score!=null?a.score+'/10':'…')+(a.is_baseline?' · best single':'')+'</span></div>'+
       '<div class="tl muted" style="margin-top:5px">'+esc(a.text||'(no answer)')+'</div></div>';
  }
  h+='</details>';
  if(base){
    const tag=ext?((ext.source==='api'?'🌐 ':'🖥 ')+esc(ext.model)+' · independent')
                 :(flag(base.country)+' '+esc(base.model)+' · best council mind');
    h+='<div class="card"><div class="row between"><h3 style="margin:0">vs a single model'+
    ' <span class="muted">('+tag+')</span></h3>'+
    '<button class="ghost" id="cmp">compare</button></div>'+
    '<div id="single" class="tl muted" style="display:none;margin-top:8px">'+esc(base.text||'')+'</div></div>';}
  // A3 — what just happened / credits
  const rec=v.receipt||{},pay=rec.payouts||{};
  h+='<details class="card"><summary class="muted">▸ What just happened · credits</summary>'+
     '<div class="muted" style="font-size:12.5px;margin-top:8px">Credits are <b>non-tradeable</b> — you earn them when your computer helps answer others, and spend them when you ask. They’re not money and can’t be traded.</div>'+
     '<div style="margin-top:8px">You spent <b>'+(+(rec.total_cost||0)).toFixed(0)+'</b> credits for this question.</div>'+
     '<div style="margin-top:6px" class="muted">each computer’s contribution:</div><ul>';
  for(const a of v.answers||[]){
    const earned=(pay[a.owner]!=null)?(' → earned '+(+pay[a.owner]).toFixed(1)+' cr'):'';
    const sp=(a.tokens&&a.elapsed_s)?(esc(a.tokens+' tokens in '+(+a.elapsed_s).toFixed(1)+'s · '+(a.tokens/Math.max(0.1,a.elapsed_s)).toFixed(0)+' tok/s')):'(pending)';
    h+='<li>'+flag(a.country)+' '+esc(a.model)+' — '+sp+earned+'</li>';
  }
  h+='</ul></details>';
  h+='<div class="card vote"><div class="row between"><b>Was the council more useful than one model?</b></div>'+
     '<div class="row" style="margin-top:8px;gap:8px"><button id="vc">▲ Council</button>'+
     '<button class="ghost" id="vt">tie</button><button class="ghost" id="vs">▼ One model</button>'+
     '<span class="thanks" id="thx" style="margin-left:auto"></span></div></div>';
  A.innerHTML=h;
  const c=document.getElementById('cmp');if(c)c.onclick=()=>{const s=document.getElementById('single');s.style.display=s.style.display==='none'?'block':'none';s.classList.toggle('muted')};
  const vote=async(verdict)=>{await fetch('/jobs/'+v.job_id+'/feedback',{method:'POST',headers:uHeaders(),
     body:JSON.stringify({verdict})});const m=await (await fetch('/metrics')).json();
     document.getElementById('thx').textContent='thanks! council wins '+
       (m.council_win_rate==null?'—':Math.round(m.council_win_rate*100)+'%')+' so far ('+m.total+')';};
  document.getElementById('vc').onclick=()=>vote('council');
  document.getElementById('vt').onclick=()=>vote('tie');
  document.getElementById('vs').onclick=()=>vote('single');
}

async function poll(id){
  let v;try{v=await (await fetch('/jobs/'+id)).json()}catch(e){return}
  drawMap(v);renderLive(v);
  if(v.status==='done'){
    // re-render only when content changes (the independent baseline may land a bit later)
    const sig=((v.baseline&&v.baseline.text)?'ext':'fb')+'·'+(v.answers||[]).length;
    if(sig!==lastAns){lastAns=sig;renderAnswer(v);refreshMe();}
    if(polling&&((v.baseline&&v.baseline.text)||++bwait>480)){clearInterval(polling);polling=null}}
  if(v.status==='failed'){if(polling){clearInterval(polling);polling=null}
    document.getElementById('live').innerHTML='<div class="card" style="color:var(--bad)">✗ '+esc(v.error||'failed')+'</div>';}
}

if(navigator.geolocation)navigator.geolocation.getCurrentPosition(p=>{you=[p.coords.latitude,p.coords.longitude]},()=>{},{timeout:4000});
refreshMe();ambientTick();setInterval(ambientTick,5000);updateHint();
// deep link: /#job=<id> reopens that question (shareable result)
if(secret&&location.hash.indexOf('#job=')===0)openJob(location.hash.slice(5));
</script>
</body>
</html>
"""
