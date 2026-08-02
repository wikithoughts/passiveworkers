#!/usr/bin/env bash
# Runs ON the VPS (via: ssh HOST 'bash -s' < scripts/vps_run.sh).
# Starts the coordinator (loopback only) + one local answer worker in a tmux session 'pw'.
# Reuses the host's existing Ollama. Idempotent: restarts the session each call.
# Identity (PW_OWNER/PW_NAME/PW_COUNTRY/PW_ANSWER_MODEL) comes from $DIR/.env — no defaults
# baked in here (R22 review). Set PW_REMOTE_DIR to relocate off /opt/passiveworkers.
set -euo pipefail
DIR=${PW_REMOTE_DIR:-/opt/passiveworkers}
cd "$DIR"
set -a; source "$DIR/.env"; set +a
PY="$DIR/.venv/bin/python"
URL="http://127.0.0.1:${PW_PORT}"

: "${PW_OWNER:?set PW_OWNER in $DIR/.env — the account this worker credits}"
: "${PW_NAME:?set PW_NAME in $DIR/.env — the display name for this node}"
: "${PW_COUNTRY:?set PW_COUNTRY in $DIR/.env — ISO country code (the egress-diversity moat)}"
: "${PW_ANSWER_MODEL:?set PW_ANSWER_MODEL in $DIR/.env — an Ollama model already pulled here}"
PW_LENS="${PW_LENS:-neutral}"
PW_CAN_JUDGE="${PW_CAN_JUDGE:-0}"
PW_POLL="${PW_POLL:-2}"

tmux kill-session -t pw 2>/dev/null || true

# Coordinator (binds 127.0.0.1:$PW_PORT per .env).
tmux new-session -d -s pw -n coord \
  "cd $DIR && set -a && source .env && set +a && exec $PY -m council.net.coordinator_app >>$DIR/coord.log 2>&1"

for i in $(seq 1 30); do curl -sf "$URL/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
curl -sf "$URL/healthz" >/dev/null 2>&1 && echo "✓ coordinator healthy at $URL" || { echo "✗ coordinator failed"; tail -20 "$DIR/coord.log"; exit 1; }

# One answer worker on the existing Ollama, using THIS operator's own identity from .env.
# Sources .env itself (like the coord window above) instead of hand-whitelisting vars —
# a whitelist silently drops any var .env has that this list doesn't (e.g. PW_WEB_BACKEND),
# and a new tmux window only inherits the SERVER's startup-time environment, not this
# script's live one, when an unrelated tmux server is already running under this user.
tmux new-window -t pw -n worker \
  "cd $DIR && set -a && source .env && set +a && PW_COORDINATOR=$URL PW_LENS=$PW_LENS \
   PW_CAN_JUDGE=$PW_CAN_JUDGE PW_POLL=$PW_POLL \
   exec $PY -m council.net.agent >>$DIR/worker.log 2>&1"

sleep 4
echo "=== /status ==="
curl -s "$URL/status" | $PY -c "import sys,json; d=json.load(sys.stdin); print('online:', [(n['name'],n['country'],n['answer_model'] or 'judge-only') for n in d['online_nodes']]); print('conserved:', d['ledger_conserved'])"
echo "✓ coordinator + VPS worker live in tmux 'pw' (windows: coord, worker)"
