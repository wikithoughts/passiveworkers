#!/usr/bin/env python3
"""
council/net/dashboard.py — the live operator map (v0)
=====================================================
A self-contained HTML page the coordinator serves at GET /dashboard. It polls /status
and shows the connected nodes on a world map (positioned by their country now;
real IP-geo is the documented next step), plus live load, model, reputation, and the
recent job flow. This is the "interactivity" view — see the network breathing.

No build step; Leaflet is loaded from a CDN by the browser. Positions use country
centroids (+ small deterministic jitter so co-located nodes don't overlap).
"""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Passive Workers — Council Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  :root{--bg:#0b1020;--panel:#121a33;--ink:#e6ecff;--mut:#8aa0d0;--good:#36d399;--warn:#fbbd23;--bad:#f87272;}
  *{box-sizing:border-box} html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);
    font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
  #wrap{display:grid;grid-template-columns:1fr 360px;height:100vh}
  #map{height:100vh}
  #side{background:var(--panel);overflow:auto;padding:16px;border-left:1px solid #21305e}
  h1{font-size:16px;margin:0 0 2px} .sub{color:var(--mut);font-size:12px;margin-bottom:14px}
  .card{background:#0f1730;border:1px solid #21305e;border-radius:10px;padding:10px 12px;margin-bottom:10px}
  .row{display:flex;justify-content:space-between;gap:8px;align-items:center}
  .pill{font-size:11px;padding:2px 7px;border-radius:999px;background:#1b2750;color:var(--mut)}
  .dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px}
  .muted{color:var(--mut)} .k{color:var(--mut)} b{color:#fff}
  .stat{font-size:22px;font-weight:700} .statlbl{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
  .flex{display:flex;gap:18px} .ok{color:var(--good)} .no{color:var(--bad)}
  .job{font-size:12px;border-left:3px solid #2a3a6e;padding:2px 0 2px 8px;margin:4px 0}
</style>
</head>
<body>
<div id="wrap">
  <div id="map"></div>
  <div id="side">
    <h1>🌍 Council — live operator map</h1>
    <div class="sub">Passive Workers · varied global intelligence · positions by country (IP-geo next)</div>
    <div class="card"><div class="flex">
      <div><div class="stat" id="nodeCount">–</div><div class="statlbl">nodes online</div></div>
      <div><div class="stat" id="credTotal">–</div><div class="statlbl">credits</div></div>
      <div><div class="stat" id="conserved">–</div><div class="statlbl">ledger</div></div>
    </div></div>
    <div id="nodes"></div>
    <h1 style="font-size:13px;margin:16px 0 6px">Recent jobs</h1>
    <div id="jobs"></div>
    <div class="sub" id="updated" style="margin-top:12px"></div>
  </div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const CENTROIDS = {
  FI:[61.9,25.7],AE:[23.4,53.8],US:[39.8,-98.6],DE:[51.2,10.4],BR:[-14.2,-51.9],GB:[54,-2],
  FR:[46.6,2.2],IN:[21,78],SG:[1.35,103.8],JP:[36.2,138.3],NL:[52.1,5.3],CA:[56,-106],
  AU:[-25,133],ZA:[-29,24],NG:[9,8],KE:[0.2,37.9],EG:[26,30],SA:[24,45],IQ:[33,44],
  TR:[39,35],RU:[61,105],CN:[35,105],KR:[36,128],ID:[-2,118],VN:[14,108],MX:[23,-102],
  AR:[-38,-63],ES:[40,-3.7],IT:[42.8,12.8],SE:[62,15],PL:[52,19],"local":[20,0],"?":[0,-20]
};
function jitter(id){id=String(id||'');let h=0;for(const c of id)h=(h*31+c.charCodeAt(0))&255;return (h/255-0.5)*6;}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function loadColor(l){l=l||0;return l<0.4?'#36d399':l<0.75?'#fbbd23':'#f87272';}
const map=L.map('map',{worldCopyJump:true}).setView([30,15],2);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  {maxZoom:8,attribution:'© OpenStreetMap © CARTO'}).addTo(map);
let markers=[];
async function tick(){
  let d; try{ d=await (await fetch('/status',{cache:'no-store'})).json(); }catch(e){ return; }
  const nodes=d.online_nodes||[];
  document.getElementById('nodeCount').textContent=nodes.length;
  document.getElementById('credTotal').textContent=Math.round(d.ledger_total||0);
  const cons=document.getElementById('conserved');
  cons.textContent=d.ledger_conserved?'✓ ok':'✗ drift';
  cons.className='stat '+(d.ledger_conserved?'ok':'no');
  markers.forEach(m=>map.removeLayer(m)); markers=[];
  const list=document.getElementById('nodes'); list.innerHTML='';
  for(const n of nodes){
    const c=CENTROIDS[n.country]||CENTROIDS['?'];
    const lat=c[0]+jitter(n.node_key), lng=c[1]+jitter(n.node_key+'x');
    const col=loadColor(n.load), role=esc(n.answer_model||'judge');
    const m=L.circleMarker([lat,lng],{radius:9,color:col,fillColor:col,fillOpacity:.85,weight:2}).addTo(map);
    m.bindPopup(`<b>${esc(n.name)}</b> · ${esc(n.country)}<br>owner ${esc(n.owner)}<br>${role}`+
      `<br>load ${(100*(n.load||0)).toFixed(0)}% · rep ${(+n.reputation||0)}/10`+
      `<br>helped ${(+n.jobs_helped||0)} · seen ${(+n.age_s||0)}s ago`);
    markers.push(m);
    list.insertAdjacentHTML('beforeend',
      `<div class="card"><div class="row"><div><span class="dot" style="background:${col}"></span>`+
      `<b>${esc(n.name)}</b> <span class="muted">${esc(n.country)}</span></div>`+
      `<span class="pill">rep ${(+n.reputation||0)}</span></div>`+
      `<div class="row" style="margin-top:6px"><span class="k">${role}</span>`+
      `<span class="muted">load ${(100*(n.load||0)).toFixed(0)}% · ${(+n.age_s||0)}s</span></div></div>`);
  }
  const jobs=d.recent_jobs||[]; const jb=document.getElementById('jobs'); jb.innerHTML='';
  for(const j of jobs.slice(0,8)){
    const col=j.status==='done'?'#36d399':j.status==='failed'?'#f87272':'#fbbd23';
    jb.insertAdjacentHTML('beforeend',
      `<div class="job" style="border-left-color:${col}"><b>${esc(j.asker)}</b> `+
      `<span class="muted">${esc(j.status)}</span></div>`);
  }
  document.getElementById('updated').textContent='updated '+new Date().toLocaleTimeString();
}
tick(); setInterval(tick,3000);
</script>
</body>
</html>
"""
