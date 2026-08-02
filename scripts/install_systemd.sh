#!/usr/bin/env bash
# Runs ON the VPS (via: ssh HOST 'bash -s' < scripts/install_systemd.sh).
# Installs systemd units for the coordinator + one worker so the hub is ALWAYS-ON (survives
# reboot, auto-restarts). Replaces the tmux session. Provider-agnostic: reads all deployment
# identity (owner/country/model/…) from $DIR/.env — set PW_REMOTE_DIR to relocate, and put your
# own PW_OWNER/PW_NAME/PW_COUNTRY/PW_ANSWER_MODEL in .env before running this (no defaults are
# baked into the unit — R22 review).
set -euo pipefail
DIR=${PW_REMOTE_DIR:-/opt/passiveworkers}
PY="$DIR/.venv/bin/python"
# shellcheck disable=SC1091
set -a; source "$DIR/.env"; set +a
URL="http://127.0.0.1:${PW_PORT}"

# Fail loudly (not with a silently-wrong default) if this operator hasn't configured identity.
: "${PW_OWNER:?set PW_OWNER in $DIR/.env — the account this worker credits}"
: "${PW_NAME:?set PW_NAME in $DIR/.env — the display name for this node}"
: "${PW_COUNTRY:?set PW_COUNTRY in $DIR/.env — ISO country code (the egress-diversity moat)}"
: "${PW_ANSWER_MODEL:?set PW_ANSWER_MODEL in $DIR/.env — an Ollama model already pulled here}"

# Stop the old tmux hub (we're switching to systemd).
tmux kill-session -t pw 2>/dev/null || true
# NOTE: never wipe the DB here — the store migrates its own schema (ALTER TABLE on boot),
# and the ledger/accounts/feedback must survive re-installs.

cat >/etc/systemd/system/pw-coordinator.service <<UNIT
[Unit]
Description=Passive Workers — Council coordinator
After=network-online.target
Wants=network-online.target
[Service]
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY -m council.net.coordinator_app
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT

cat >/etc/systemd/system/pw-worker.service <<UNIT
[Unit]
Description=Passive Workers — worker ($PW_NAME/$PW_COUNTRY)
After=pw-coordinator.service network-online.target
Wants=pw-coordinator.service
[Service]
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
Environment=PW_COORDINATOR=$URL
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY -m council.net.agent
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable pw-coordinator.service pw-worker.service
systemctl restart pw-coordinator.service   # restart (not --now): pick up new code/units when already running
sleep 3
for i in $(seq 1 30); do curl -sf "$URL/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
systemctl restart pw-worker.service
sleep 4

echo "=== systemd status ==="
systemctl is-enabled pw-coordinator.service pw-worker.service
systemctl --no-pager --lines=0 status pw-coordinator.service pw-worker.service | grep -E "Active:" || true
echo "=== health ==="
curl -sf "$URL/healthz" && echo " coordinator OK"
curl -s "$URL/status" | $PY -c "import sys,json;d=json.load(sys.stdin);print('online:',[(n['name'],n['country'],n['answer_model'] or 'judge') for n in d['online_nodes']])"
echo "✓ always-on: pw-coordinator + pw-worker enabled (start on boot, Restart=always)"
