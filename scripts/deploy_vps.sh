#!/usr/bin/env bash
# Deploy the Council coordinator + worker code to a remote host (isolated, reuses
# the host's existing Ollama). Provider-agnostic: set PW_VPS_HOST to any SSH alias, and
# PW_SSH_KEY if you're not using ssh-agent/default identity resolution. Re-runnable; only
# syncs code + venv. Does NOT start anything (see vps_run.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

HOST=${PW_VPS_HOST:?set PW_VPS_HOST to your SSH alias/host before deploying (e.g. export PW_VPS_HOST=myvps)}
PORT=${PW_PORT:-8791}
REMOTE_DIR=${PW_REMOTE_DIR:-/opt/passiveworkers}

# 1. Local token (persisted, gitignored) — shared secret for the coordinator.
if [ ! -f .vps.env ]; then
  {
    echo "PW_TOKEN=$(openssl rand -hex 24)"
    echo "PW_COORDINATOR=http://127.0.0.1:$PORT"
    echo "PW_PORT=$PORT"
    echo "PW_VPS_HOST=$HOST"
    echo "PW_REMOTE_DIR=$REMOTE_DIR"
    [ -n "${PW_SSH_KEY:-}" ] && echo "PW_SSH_KEY=$PW_SSH_KEY"   # only if the caller set one — no
                                                                 # hardcoded identity file (R22)
  } > .vps.env
  chmod 600 .vps.env
  echo "→ wrote .vps.env (new shared token)"
fi
# shellcheck disable=SC1091
source .vps.env

# Agent-independent SSH (an SSH agent can re-lock mid-deploy) IF a key was configured;
# otherwise fall back to normal SSH identity resolution (agent / ~/.ssh/config) — no hardcoded
# key path (R22 review).
if [ -n "${PW_SSH_KEY:-}" ]; then
  SSH="ssh -o BatchMode=yes -o IdentityAgent=none -o IdentitiesOnly=yes -i $PW_SSH_KEY"
else
  SSH="ssh -o BatchMode=yes"
fi

# 2. Sync the packaging files needed to `pip install .` on the remote, then the passiveworkers/
#    package itself (the same set the Dockerfile COPYs).
echo "→ syncing → $HOST:$REMOTE_DIR"
$SSH "$HOST" "mkdir -p $REMOTE_DIR"
rsync -az -e "$SSH" pyproject.toml README.md LICENSE "$HOST:$REMOTE_DIR/"
rsync -az --delete --exclude __pycache__ -e "$SSH" passiveworkers/ "$HOST:$REMOTE_DIR/passiveworkers/"

# 3. venv + deps (from the SYNCED pyproject.toml — no hand-typed list, R22 review) + remote
#    .env (loopback-only coordinator config + sensible worker defaults an operator can
#    override in this same file — PW_OWNER/PW_NAME/PW_COUNTRY/PW_ANSWER_MODEL are NOT set
#    here; install_systemd.sh/vps_run.sh require the operator to set those explicitly).
$SSH "$HOST" "bash -s" <<REMOTE
set -e
cd "$REMOTE_DIR"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip -q install --upgrade pip >/dev/null 2>&1 || true
# base deps come from pyproject.toml (source of truth); uvicorn[standard] adds uvloop/httptools
# for production perf — worth keeping as one explicit, justified addition, not a hand-typed list.
./.venv/bin/pip -q install . "uvicorn[standard]>=0.30"
umask 077
cat > "$REMOTE_DIR/.env" <<ENV
PW_TOKEN=$PW_TOKEN
PW_HOST=127.0.0.1
PW_PORT=$PORT
PW_DB=$REMOTE_DIR/coordinator.db
PW_FLEET_SIZE=3
PW_NODE_TTL=120
PW_MAX_RUN=600
PW_WEB_BACKEND=ddgs
PW_OLLAMA_TIMEOUT=480
PW_RESEARCH_GEN_TIMEOUT=900
PW_BASELINE_LOCAL_MODEL=${PW_BASELINE_LOCAL_MODEL:-qwen3:14b}
${PW_BASELINE_API_KEY:+PW_BASELINE_API_KEY=$PW_BASELINE_API_KEY}
${PW_BASELINE_MODEL:+PW_BASELINE_MODEL=$PW_BASELINE_MODEL}
${PW_STARTER_CREDITS:+PW_STARTER_CREDITS=$PW_STARTER_CREDITS}
ENV
sed -i '/^\$/d' "$REMOTE_DIR/.env"   # drop blank lines from unset optionals
./.venv/bin/python -c "import fastapi, uvicorn, requests, numpy, pydantic, psutil; \
print('→ deps OK on', __import__('socket').gethostname())"
REMOTE
echo "✓ deploy complete"
