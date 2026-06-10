#!/usr/bin/env bash
# Join this Mac to the live council and STAY up so you can watch the map and ask questions.
# Opens an SSH tunnel + starts 2 Mac perspectives + a judge, then supervises the tunnel.
# Ctrl-C leaves cleanly (kills the Mac-side nodes + tunnel); the Helsinki hub keeps running.
set -uo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
# shellcheck disable=SC1091
source .vps.env
URL="http://127.0.0.1:${PW_PORT}"
export PW_TOKEN PW_COORDINATOR="$URL" PW_POLL=2
SSHK="${PW_SSH_KEY:-$HOME/.ssh/hetzner_ssh}"
mkdir -p /tmp/pw
pids=(); tunnel=""

cleanup(){ for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done
           [ -n "$tunnel" ] && kill "$tunnel" 2>/dev/null || true
           pkill -P $$ 2>/dev/null || true; }   # belt-and-suspenders: kill any remaining child (tunnels, agents)
trap cleanup EXIT                                  # always tidy up on exit
trap 'echo; echo "leaving council…"; exit 130' INT TERM   # Ctrl-C → exit (then EXIT runs cleanup)

open_tunnel(){
  ssh -N -o ExitOnForwardFailure=yes -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -o IdentityAgent=none -o IdentitiesOnly=yes -i "$SSHK" \
    -L "${PW_PORT}:127.0.0.1:${PW_PORT}" "$PW_VPS_HOST" & tunnel=$!
}

open_tunnel
for i in $(seq 1 30); do curl -sf "$URL/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
curl -sf "$URL/healthz" >/dev/null 2>&1 || { echo "✗ coordinator unreachable via tunnel"; exit 1; }

start(){ PW_NAME="$1" PW_OWNER="$2" PW_COUNTRY="$3" PW_ANSWER_MODEL="$4" PW_LENS="$5" \
         PW_CAN_JUDGE="$6" PW_JUDGE_MODEL="$7" python -m council.net.agent >"/tmp/pw/mac_$1.log" 2>&1 & pids+=($!); }
start macA mac_dubai AE gemma3:4b  opportunity 0 ""
start macB mac_dubai AE gemma2:9b  skeptic     0 ""
start judge judge_mac AE ""        neutral     1 qwen2.5:14b
sleep 5

echo "✓ Mac joined the council. Online nodes:"
curl -s "$URL/status" | python -c "import sys,json; d=json.load(sys.stdin); [print('   ',n['name'],n['country'],n['answer_model'] or '(judge)') for n in d['online_nodes']]"
echo ""
echo "🗺  App:   $URL/   ·   operator map:  $URL/dashboard"
echo "💬 Ask:   bash scripts/ask.sh \"your question\"   (or just use the app)"
echo "   Ctrl-C to leave the council (the Helsinki hub stays up)."

# Stay up while the tunnel is alive; Ctrl-C exits instantly via the INT trap.
while kill -0 "$tunnel" 2>/dev/null; do sleep 3; done
echo "tunnel closed — run 'bash scripts/mac_join.sh' again to rejoin."
