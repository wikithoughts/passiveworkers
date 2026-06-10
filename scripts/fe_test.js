// Headless runtime test of the Living Council Map front-end.
// Loads the REAL page JS with a mocked DOM + Leaflet + mock backend, then exercises
// the sign-in and Ask handlers and asserts the UI actually updates.
// Run: node scripts/fe_test.js   (extracts the inline JS from council.net.app itself)
const { execSync } = require('child_process');
const js = execSync(
  `python3 -c "import re,importlib;m=importlib.import_module('council.net.app');` +
  `print(re.findall(r'<script>(.*?)</script>', m.APP_HTML, re.DOTALL)[-1])"`,
  { cwd: __dirname + '/..', encoding: 'utf8' });

// ---- DOM mock ----
const els = {};
function makeEl(id){
  return { id, style:{}, _v:'', innerHTML:'', textContent:'',
    onclick:null, _listeners:{},
    get value(){return this._v}, set value(v){this._v=v},
    insertAdjacentHTML(_p,h){ this.innerHTML += h; },
    classList:{toggle(){}, add(){}, remove(){}},
    appendChild(){}, querySelector(){return null} };
}
global.document = {
  getElementById(id){ return els[id] || (els[id]=makeEl(id)); },
};
// ---- storage / env mocks ----
const store = {};
global.localStorage = { getItem:k=>k in store?store[k]:null, setItem:(k,v)=>{store[k]=String(v)}, removeItem:k=>{delete store[k]} };
global.navigator = {};                 // no geolocation
global.location = { origin:'http://127.0.0.1:8791' };
global.setInterval = ()=>0; global.clearInterval = ()=>{};
global.console = console;
// ---- Leaflet stub: universal chainable proxy ----
const chain = new Proxy(function(){}, { get:()=>()=>chain, apply:()=>chain, construct:()=>chain });
global.L = chain;
// ---- mock backend ----
const fetchCalls = [];
function resp(data){ return { status:200, ok:true, content:'x', json:async()=>data, text:async()=>JSON.stringify(data) }; }
global.fetch = async (url, opts={}) => {
  const method = (opts.method||'GET').toUpperCase();
  fetchCalls.push(method+' '+url);
  if(url==='/status') return resp({machines:2,minds:2,online_nodes:[
     {node_key:'k1',machine_key:'mFI',name:'hel',country:'FI',owner:'o',answer_model:'llama3.2',lens:'x',can_judge:0,load:0.1,age_s:1,reputation:0,jobs_helped:0},
     {node_key:'k2',machine_key:'mAE',name:'macA',country:'AE',owner:'o',answer_model:'gemma3:4b',lens:'opp',can_judge:0,load:0.2,age_s:1,reputation:0,jobs_helped:0}
   ],ledger_total:0,ledger_conserved:true,recent_jobs:[]});
  if(url==='/me') return resp({handle:'tester',balance:65,reputation:0,helped:0,asked:1});
  if(url==='/users'&&method==='POST') return resp({handle:'tester',user_secret:'SEC123',balance:100,reputation:0,helped:0,asked:0});
  if(url==='/jobs'&&method==='POST') return resp({job_id:'JOB1',status:'pending_answers',assigned:['n1','n2','n3'],balance:{handle:'tester',balance:65}});
  if(url==='/jobs/JOB1') return resp({job_id:'JOB1',status:'done',question:'q',merged:'Asia is the pick — fast mobile-first growth.',
     council:{consensus:['mobile-first growth'],disagreements:[{point:'EU vs Asia',sides:'A:Asia B:EU'}],unique:[{worker_id:'w1',point:'first-mover edge'}]},
     judge_country:'DE',judge_status:'done',judge_machine_key:'mDE',
     answers:[{worker_id:'w1',owner:'o',model:'gemma3:4b',lens:'opportunity',country:'AE',node_key:'k1',machine_key:'mAE',status:'done',status_label:'answered',score:9,text:'Asia answer text',tokens:120,elapsed_s:3.2,is_baseline:false},
              {worker_id:'w2',owner:'o2',model:'llama3.2',lens:'first',country:'FI',node_key:'k2',machine_key:'mFI',status:'done',status_label:'answered',score:7,text:'Europe answer text',tokens:90,elapsed_s:5.0,is_baseline:false}],
     baseline:{text:'Independent single-model answer text',model:'qwen3:14b',source:'local',elapsed_s:31.0},
     receipt:{total_cost:35,payouts:{o:12.5,o2:9.0},judge_fee:5}});
  if(url==='/jobs/JOB1/feedback'&&method==='POST') return resp({ok:true});
  if(url==='/metrics') return resp({council:1,single:0,tie:0,total:1,council_win_rate:1.0});
  return resp({});
};

const wait = ms => new Promise(r=>setTimeout(r,ms));
const assert = (c,m)=>{ if(!c){ console.error('✗ FAIL:', m); process.exit(1);} console.log('✓', m); };

(async ()=>{
  // 1) the whole script must run without throwing → handlers bind
  try { eval(js); } catch(e){ console.error('✗ script threw on load:', e.message); process.exit(1); }
  await wait(30);
  assert(typeof els['ask'].onclick==='function', 'Ask handler bound (script ran fully)');
  assert(typeof els['signin'].onclick==='function', 'Sign-in handler bound');
  assert(els['auth'].style.display==='block' && els['askbox'].style.display==='none',
         'signed-out: sign-in card shown, Ask box hidden');
  assert(((els['netstat']&&els['netstat'].textContent)||'').includes('machine'),
         'network status (N machines · M minds) shown');

  // 2) sign in
  document.getElementById('handle').value='tester';
  await document.getElementById('signin').onclick();
  await wait(30);
  assert(store['pw_secret']==='SEC123', 'sign-in stored the user secret');
  assert(els['askbox'].style.display!=='none', 'after sign-in: Ask box revealed');
  assert(fetchCalls.includes('POST /users'), 'sign-in POSTed /users');

  // 3) ask the council
  document.getElementById('q').value='Where should a 2-person startup launch first?';
  await document.getElementById('ask').onclick();
  await wait(60);   // let the (immediate) poll resolve
  assert(fetchCalls.includes('POST /jobs'), 'Ask POSTed /jobs');
  assert(fetchCalls.includes('GET /jobs/JOB1'), 'polled the job');
  assert(els['answer'].innerHTML.includes('Asia is the pick'), 'answer panel rendered the TL;DR');
  assert(els['answer'].innerHTML.includes('mobile-first growth'), 'agree/disagree (consensus) rendered');
  assert(els['answer'].innerHTML.includes('Asia answer text') && els['answer'].innerHTML.includes('Europe answer text'),
         'each node individual answer rendered');
  assert(els['answer'].innerHTML.includes('non-tradeable') && els['answer'].innerHTML.includes('tok/s'),
         'credits / what-happened panel rendered');
  assert(els['answer'].innerHTML.includes('Independent single-model answer text'),
         'independent baseline answer rendered in compare card');
  assert(els['answer'].innerHTML.includes('independent') && !els['answer'].innerHTML.includes('best single'),
         'compare card labelled independent (not best-council-mind fallback)');
  assert(typeof els['vc'].onclick==='function', 'vote buttons wired');

  // 4) vote
  await document.getElementById('vc').onclick();
  await wait(20);
  assert(fetchCalls.some(c=>c.includes('feedback')), 'vote POSTed feedback');
  assert(fetchCalls.includes('GET /metrics'), 'win-rate metric fetched after vote');

  console.log('\n🎉 ALL FRONT-END FLOW CHECKS PASSED — sign-in → ask → render → vote works at runtime.');
})();
