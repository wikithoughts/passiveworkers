#!/usr/bin/env bash
# Mac side of the cross-country council. Opens an SSH tunnel to the VPS coordinator
# (no public port, no inbound to the Mac), starts 2 Mac perspectives + 1 Mac judge,
# and asks the council ONE question — answered jointly by the Mac (AE) and the VPS (FI).
# Leaves the VPS hub running; tears down only the Mac-side agents + tunnel on exit.
set -uo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
# shellcheck disable=SC1091
source .vps.env                       # PW_TOKEN, PW_PORT, PW_VPS_HOST
URL="http://127.0.0.1:${PW_PORT}"
export PW_TOKEN PW_COORDINATOR="$URL" PW_POLL=2
mkdir -p /tmp/pw
pids=(); tunnel=""
cleanup(){ for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; [ -n "$tunnel" ] && kill "$tunnel" 2>/dev/null || true; }
trap cleanup EXIT

SSHK="${PW_SSH_KEY:-$HOME/.ssh/hetzner_ssh}"
echo "→ opening SSH tunnel  Mac:${PW_PORT} → ${PW_VPS_HOST}:127.0.0.1:${PW_PORT}"
ssh -N -o ExitOnForwardFailure=yes -o BatchMode=yes -o IdentityAgent=none -o IdentitiesOnly=yes \
  -i "$SSHK" -L "${PW_PORT}:127.0.0.1:${PW_PORT}" "$PW_VPS_HOST" & tunnel=$!
for i in $(seq 1 30); do curl -sf "$URL/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
curl -sf "$URL/healthz" >/dev/null 2>&1 || { echo "✗ coordinator unreachable via tunnel"; exit 1; }
echo "✓ coordinator reachable from the Mac through the tunnel"

start(){ PW_NAME="$1" PW_OWNER="$2" PW_COUNTRY="$3" PW_ANSWER_MODEL="$4" PW_LENS="$5" \
         PW_CAN_JUDGE="$6" PW_JUDGE_MODEL="$7" python -m council.net.agent >"/tmp/pw/mac_$1.log" 2>&1 & pids+=($!); }
echo "→ starting Mac perspectives + judge"
start macA mac_dubai AE gemma3:12b opportunity 0 ""
start macB mac_dubai AE gemma2:9b  skeptic     0 ""
start judge judge_mac AE ""        neutral     1 qwen2.5:14b
sleep 6

echo ""; echo "=== ONLINE NODES (cross-country council) ==="
curl -s "$URL/status" | python -c "import sys,json; d=json.load(sys.stdin); [print(f\"   {n['name']:<6} owner={n['owner']:<10} {n['country']:<3} {n['answer_model'] or '(judge)'}\") for n in d['online_nodes']]"

echo ""
python -m council.net.submit --asker alice "Our two-person startup must pick ONE region to launch in first — Western Europe, the Gulf (GCC), or Southeast Asia. Recommend one, with the single strongest reason and the biggest risk."

echo ""; echo "=== LEDGER / TELEMETRY ==="
curl -s "$URL/status" | python -c "import sys,json; d=json.load(sys.stdin); print('conserved:', d['ledger_conserved'], '| total:', d['ledger_total']); print('balances:', d['accounts'])"
