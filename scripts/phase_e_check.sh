#!/usr/bin/env bash
# End-to-end Phase-E check: brings the Mac into the live (systemd) Helsinki council,
# signs up a user, asks via the API, prints the council read + baseline, records a
# feedback vote, reads the win-rate metric, and confirms the app (/) is served.
set -uo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
# shellcheck disable=SC1091
source .vps.env
URL="http://127.0.0.1:${PW_PORT}"
SSHK="${PW_SSH_KEY:-$HOME/.ssh/hetzner_ssh}"
export PW_TOKEN PW_COORDINATOR="$URL" PW_POLL=2
mkdir -p /tmp/pw
pids=(); tunnel=""
cleanup(){ for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; [ -n "$tunnel" ] && kill "$tunnel" 2>/dev/null || true; }
trap cleanup EXIT

ssh -N -o ExitOnForwardFailure=yes -o BatchMode=yes -o IdentityAgent=none -o IdentitiesOnly=yes \
  -i "$SSHK" -L "${PW_PORT}:127.0.0.1:${PW_PORT}" "$PW_VPS_HOST" & tunnel=$!
for i in $(seq 1 30); do curl -sf "$URL/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
echo "✓ tunnel up to the live Helsinki hub"

start(){ PW_NAME="$1" PW_OWNER="$2" PW_COUNTRY="$3" PW_ANSWER_MODEL="$4" PW_LENS="$5" \
         PW_CAN_JUDGE="$6" PW_JUDGE_MODEL="$7" python -m council.net.agent >"/tmp/pw/mac_$1.log" 2>&1 & pids+=($!); }
start macA mac_dubai AE gemma3:4b  opportunity 0 ""
start macB mac_dubai AE gemma2:9b  skeptic     0 ""
start judge judge_mac AE ""        neutral     1 qwen2.5:14b
sleep 6

echo "✓ app served at / : $(curl -s "$URL/" | grep -o 'Passive Workers' | head -1) (council map SPA)"

python - "$URL" <<'PY'
import sys, time, json, urllib.request
URL=sys.argv[1]
def req(path, body=None, hdr={}):
    data=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(URL+path, data=data,
        headers={'Content-Type':'application/json', **hdr}, method='POST' if data else 'GET')
    return json.load(urllib.request.urlopen(r, timeout=30))

u=req('/users', {'handle':'founder-'+str(int(time.time()))[-5:]})
sec=u['user_secret']; H={'X-User-Secret':sec}
print('✓ signed up:', u['handle'], '| balance', u['balance'])
j=req('/jobs', {'question':'Where should a two-person startup launch first — Western Europe, the Gulf, or Southeast Asia? One pick, the strongest reason, the biggest risk.'}, H)
jid=j['job_id']; print('✓ asked → job', jid[:8], '| workers:', len(j.get('assigned',[])), '| balance now', j['balance']['balance'])
v={}
for _ in range(180):
    v=req('/jobs/'+jid)
    if v['status'] in ('done','failed'): break
    time.sleep(2)
print('✓ status:', v['status'])
if v['status']=='done':
    co=v.get('council') or {}
    print('   perspectives:', [(a['country'],a['model'],a['status_label'],a['score']) for a in v['answers']])
    print('   baseline(best-single):', [(a['country'],a['model']) for a in v['answers'] if a['is_baseline']])
    print('   TL;DR:', (v['merged'] or '')[:170])
    print('   AGREE :', co.get('consensus'))
    print('   DIFFER:', [d['point'] for d in co.get('disagreements',[])])
    print('   UNIQUE:', [x['point'] for x in co.get('unique',[])])
    req('/jobs/'+jid+'/feedback', {'verdict':'council'}, H)
    print('✓ vote recorded | metrics:', req('/metrics'))
    print('✓ give/take: balance after one ask =', req('/me', None, H)['balance'])
PY