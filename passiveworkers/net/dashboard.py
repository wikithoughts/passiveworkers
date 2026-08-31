#!/usr/bin/env python3
"""
passiveworkers/net/dashboard.py — the live operator map (v0)
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
<!--__LEAFLET_CSS__-->
<style>
  /*__THEME__*/
  *{box-sizing:border-box} html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);
    font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
  #wrap{display:grid;grid-template-columns:1fr 360px;height:100vh}
  #map{height:100vh}
  #side{background:var(--panel);overflow:auto;padding:16px;border-left:1px solid var(--edge)}
  h1{font-size:16px;margin:0 0 2px} .sub{color:var(--mut);font-size:12px;margin-bottom:14px}
  .card{background:var(--card);border:1px solid var(--edge);border-radius:10px;padding:10px 12px;margin-bottom:10px}
  .row{display:flex;justify-content:space-between;gap:8px;align-items:center}
  .pill{font-size:11px;padding:2px 7px;border-radius:999px;background:#1b2750;color:var(--mut)}
  .dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px}
  .muted{color:var(--mut)} .k{color:var(--mut)} b{color:#fff}
  .stat{font-size:22px;font-weight:700} .statlbl{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
  .flex{display:flex;gap:18px} .ok{color:var(--good)} .no{color:var(--bad)}
  .job{font-size:12px;border-left:3px solid #2a3a6e;padding:2px 0 2px 8px;margin:4px 0}
  a{color:var(--acc)} :focus-visible{outline:2px solid var(--acc);outline-offset:2px;border-radius:8px}
  @media(max-width:820px){#wrap{grid-template-columns:1fr;grid-template-rows:42vh 1fr}#map{height:42vh}
    #side{border-left:0;border-top:1px solid var(--edge)}}
  @media(max-width:480px){#side{padding:12px}.flex{gap:12px}.stat{font-size:19px}}
  .lbtn{font-size:10px;padding:1px 7px;margin-left:4px;border-radius:999px;background:#1b2750;
    color:var(--mut);cursor:pointer;font-weight:400;text-transform:none;letter-spacing:0}
  .lbtn.on{background:#2447b2;color:#fff}
  .lead{display:flex;align-items:center;gap:8px;font-size:12.5px;padding:4px 0;border-bottom:1px solid #18213f}
  .lead .rank{color:var(--mut);width:18px;text-align:right}
  .lead .who{flex:1;font-weight:600;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .lead .met{color:var(--mut)}
</style>
</head>
<body>
<div id="wrap">
  <div id="map" aria-label="World map of online council nodes"></div>
  <div id="side">
    <h1><span aria-hidden="true">🌍</span> Passive Workers</h1>
    <div class="sub">Live operator map · varied global intelligence · positions by geo-verified country where available</div>
    <div class="card"><div class="flex" aria-live="polite">
      <div><div class="stat" id="nodeCount">–</div><div class="statlbl">nodes online</div></div>
      <div><div class="stat" id="credTotal">–</div><div class="statlbl">credits</div></div>
      <div><div class="stat" id="conserved">–</div><div class="statlbl">ledger</div></div>
    </div></div>
    <div id="nodes"></div>
    <h1 style="font-size:13px;margin:16px 0 6px">Top operators
      <span class="lbtn on" id="lb_reputation">rep</span><span class="lbtn" id="lb_helped">helped</span><span class="lbtn" id="lb_credits">earned</span>
    </h1>
    <div id="leaders"></div>
    <h1 style="font-size:13px;margin:16px 0 6px">Recent jobs</h1>
    <div id="jobs"></div>
    <div class="sub" id="updated" style="margin-top:12px" aria-live="polite"></div>
    <!--__FOOTER__-->
  </div>
</div>
<!--__LEAFLET_JS__-->
<script>
/*__PWC__*/
/*__CENTROIDS__*/
function jitter(id){id=String(id||'');let h=0;for(const c of id)h=(h*31+c.charCodeAt(0))&255;return (h/255-0.5)*6;}
/*__ESC__*/
function loadColor(l){l=l||0;return l<0.4?PWC.good:l<0.75?PWC.warn:PWC.bad;}
const map=L.map('map',{worldCopyJump:true}).setView([30,15],2);
/*__MAP_TILE__*/.addTo(map);
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
    const disp=n.geo_country||n.country;   // D43: prefer the geo-verified country for placement
    const c=CENTROIDS[disp]||CENTROIDS['?'];
    const lat=c[0]+jitter(n.node_key), lng=c[1]+jitter(n.node_key+'x');
    const col=loadColor(n.load), role=esc(n.answer_model||'judge');
    const geoBadge=n.geo_mismatch
      ? `<span class="pill" title="self-reported vs geo-verified" style="color:${PWC.warn}">⚠ says ${esc(n.country)} · geo ${esc(n.geo_country)}</span>`
      : (n.geo_country ? `<span class="pill" title="geo-verified" style="color:${PWC.good}">✓ ${esc(n.geo_country)}</span>` : '');
    const m=L.circleMarker([lat,lng],{radius:9,color:col,fillColor:col,fillOpacity:.85,weight:2}).addTo(map);
    m.bindPopup(`<b>${esc(n.name)}</b> · ${esc(disp)}${n.geo_mismatch?' (says '+esc(n.country)+')':''}<br>owner ${esc(n.owner)}<br>${role}`+
      `<br>load ${(100*(n.load||0)).toFixed(0)}% · rep ${(+n.reputation||0)}/10`+
      `<br>helped ${(+n.jobs_helped||0)} · seen ${(+n.age_s||0)}s ago`);
    markers.push(m);
    list.insertAdjacentHTML('beforeend',
      `<div class="card"><div class="row"><div><span class="dot" style="background:${col}"></span>`+
      `<b>${esc(n.name)}</b> <span class="muted">${esc(disp)}</span> ${geoBadge}</div>`+
      `<span class="pill">rep ${(+n.reputation||0)}</span></div>`+
      `<div class="row" style="margin-top:6px"><span class="k">${role}</span>`+
      `<span class="muted">load ${(100*(n.load||0)).toFixed(0)}% · ${(+n.age_s||0)}s</span></div></div>`);
  }
  const jobs=d.recent_jobs||[]; const jb=document.getElementById('jobs'); jb.innerHTML='';
  const JT={chat:'💬 ask',research_report:'🔬 research',shard_map:'⚙️ batch',assisted:'🤝 assisted',
    download_extract:'📥 extract',code_generation:'💻 code'};
  for(const j of jobs.slice(0,8)){
    const col=j.status==='done'?PWC.good:j.status==='failed'?PWC.bad:PWC.warn;
    const ago=(j.age_s!=null)?(j.age_s<90?Math.round(j.age_s)+'s':Math.round(j.age_s/60)+'m')+' ago':'';
    jb.insertAdjacentHTML('beforeend',
      `<div class="job" style="border-left-color:${col}"><b>${esc(JT[j.type]||j.type||'job')}</b> `+
      `<span class="muted">${esc(j.status)}${ago?' · '+ago:''}</span></div>`);
  }
  document.getElementById('updated').textContent='updated '+new Date().toLocaleTimeString();
}
// D44: operator leaderboard (pseudonymous; owner only). Slower cadence — it changes slowly.
let lbSort='reputation';
function lbMetric(o){
  if(lbSort==='helped') return (+o.jobs_helped||0)+' jobs';
  if(lbSort==='credits') return (+o.credits_earned||0)+' cr';
  return 'rep '+(+o.reputation||0)+'/10';
}
async function tickLeaders(){
  let d; try{ d=await (await fetch('/leaderboard?sort='+lbSort,{cache:'no-store'})).json(); }catch(e){ return; }
  const box=document.getElementById('leaders'); if(!box) return; box.innerHTML='';
  const ops=d.operators||[];
  if(!ops.length){ box.innerHTML='<div class="muted" style="font-size:12px">no operators yet</div>'; return; }
  ops.forEach((o,i)=>{
    const dot=o.online?`<span class="dot" style="background:${PWC.good}"></span>`:'';
    const cc=(o.countries||[]).length?' <span class="muted">'+esc((o.countries||[]).join(' '))+'</span>':'';
    box.insertAdjacentHTML('beforeend',
      `<div class="lead"><span class="rank">${i+1}</span><span class="who">${dot}${esc(o.owner)}${cc}</span>`+
      `<span class="met">${esc(lbMetric(o))}</span></div>`);
  });
}
['reputation','helped','credits'].forEach(s=>{
  const b=document.getElementById('lb_'+s);
  if(b) b.onclick=()=>{
    lbSort=s;
    ['reputation','helped','credits'].forEach(x=>document.getElementById('lb_'+x).classList.toggle('on',x===s));
    tickLeaders();
  };
});
tickLeaders(); setInterval(tickLeaders,15000);
tick(); setInterval(tick,3000);
</script>
</body>
</html>
"""

# Shared UI fragments (theme, footer, Leaflet bootstrap, esc(), centroids, status colors), substituted
# in at import (see passiveworkers.net.ui_common).
from passiveworkers.net import ui_common as _ui  # noqa: E402
DASHBOARD_HTML = (DASHBOARD_HTML
                  .replace("/*__THEME__*/", _ui.THEME_CSS)
                  .replace("<!--__FOOTER__-->", _ui.FOOTER_HTML)
                  .replace("<!--__LEAFLET_CSS__-->", _ui.LEAFLET_CSS)
                  .replace("<!--__LEAFLET_JS__-->", _ui.LEAFLET_JS)
                  .replace("/*__MAP_TILE__*/", _ui.MAP_TILE_JS)
                  .replace("/*__PWC__*/", _ui.STATUS_COLORS_JS)
                  .replace("/*__ESC__*/", _ui.ESC_JS)
                  .replace("/*__CENTROIDS__*/", _ui.CENTROIDS_JS))
