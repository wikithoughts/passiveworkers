#!/usr/bin/env bash
# Runs ON the VPS (via: ssh HOST 'bash -s' < scripts/vps_run.sh).
# Starts the coordinator (loopback only) + one local answer worker in a tmux session 'pw'.
# Reuses the host's existing Ollama. Idempotent: restarts the session each call.
set -euo pipefail
DIR=/opt/passiveworkers
cd "$DIR"
set -a; source "$DIR/.env"; set +a
PY="$DIR/.venv/bin/python"
URL="http://127.0.0.1:${PW_PORT}"

tmux kill-session -t pw 2>/dev/null || true

# Coordinator (binds 127.0.0.1:$PW_PORT per .env).
tmux new-session -d -s pw -n coord \
  "cd $DIR && set -a && source .env && set +a && exec $PY -m council.net.coordinator_app >>$DIR/coord.log 2>&1"

for i in $(seq 1 30); do curl -sf "$URL/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
curl -sf "$URL/healthz" >/dev/null 2>&1 && echo "✓ coordinator healthy at $URL" || { echo "✗ coordinator failed"; tail -20 "$DIR/coord.log"; exit 1; }

# One Finnish answer worker on the existing Ollama (small model = light on the box).
tmux new-window -t pw -n worker \
  "cd $DIR && PW_COORDINATOR=$URL PW_TOKEN=$PW_TOKEN PW_OWNER=helsinki PW_NAME=hel \
   PW_COUNTRY=FI PW_ANSWER_MODEL=llama3.2:latest PW_LENS=first_principles PW_CAN_JUDGE=0 PW_POLL=2 \
   exec $PY -m council.net.agent >>$DIR/worker.log 2>&1"

sleep 4
echo "=== /status ==="
curl -s "$URL/status" | $PY -c "import sys,json; d=json.load(sys.stdin); print('online:', [(n['name'],n['country'],n['answer_model'] or 'judge-only') for n in d['online_nodes']]); print('conserved:', d['ledger_conserved'])"
echo "✓ coordinator + VPS worker live in tmux 'pw' (windows: coord, worker)"
