#!/usr/bin/env bash
# Deploy the Council coordinator + worker code to a remote host (isolated, reuses
# the host's existing Ollama). Provider-agnostic: set PW_VPS_HOST to any SSH alias.
# Re-runnable; only syncs code + venv. Does NOT start anything (see vps_run.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

HOST=${PW_VPS_HOST:-wikiclaw-1}
PORT=${PW_PORT:-8791}
REMOTE_DIR=${PW_REMOTE_DIR:-/opt/passiveworkers}

# 1. Local token (persisted, gitignored) — shared secret for the coordinator.
if [ ! -f .vps.env ]; then
  cat > .vps.env <<EOF
PW_TOKEN=$(openssl rand -hex 24)
PW_COORDINATOR=http://127.0.0.1:$PORT
PW_PORT=$PORT
PW_VPS_HOST=$HOST
PW_REMOTE_DIR=$REMOTE_DIR
PW_SSH_KEY=$HOME/.ssh/hetzner_ssh
EOF
  chmod 600 .vps.env
  echo "→ wrote .vps.env (new shared token)"
fi
# shellcheck disable=SC1091
source .vps.env

# Agent-independent SSH (the 1Password SSH agent can re-lock; use the key file directly).
SSHK="${PW_SSH_KEY:-$HOME/.ssh/hetzner_ssh}"
SSH="ssh -o BatchMode=yes -o IdentityAgent=none -o IdentitiesOnly=yes -i $SSHK"

# 2. Sync code (council/ package only).
echo "→ syncing council/ → $HOST:$REMOTE_DIR"
$SSH "$HOST" "mkdir -p $REMOTE_DIR"
rsync -az --delete --exclude __pycache__ -e "$SSH" council/ "$HOST:$REMOTE_DIR/council/"

# 3. venv + deps + remote .env (loopback-only coordinator config).
$SSH "$HOST" "bash -s" <<REMOTE
set -e
cd "$REMOTE_DIR"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip -q install --upgrade pip >/dev/null 2>&1 || true
./.venv/bin/pip -q install "fastapi>=0.115" "uvicorn[standard]>=0.30" requests psutil >/dev/null
umask 077
cat > "$REMOTE_DIR/.env" <<ENV
PW_TOKEN=$PW_TOKEN
PW_HOST=127.0.0.1
PW_PORT=$PORT
PW_DB=$REMOTE_DIR/coordinator.db
PW_FLEET_SIZE=3
PW_NODE_TTL=120
ENV
./.venv/bin/python -c "import fastapi, uvicorn, requests; print('→ deps OK on', __import__('socket').gethostname())"
REMOTE
echo "✓ deploy complete"
