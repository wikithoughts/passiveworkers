#!/usr/bin/env bash
# Runs ON the VPS (via: ssh HOST 'bash -s' < scripts/install_systemd.sh).
# Installs systemd units for the coordinator + the Finnish worker so the hub is
# ALWAYS-ON (survives reboot, auto-restarts). Replaces the tmux session.
# Provider-agnostic: units reference the venv + /opt/passiveworkers/.env (relocatable).
set -euo pipefail
DIR=/opt/passiveworkers
PY="$DIR/.venv/bin/python"
# shellcheck disable=SC1091
set -a; source "$DIR/.env"; set +a
URL="http://127.0.0.1:${PW_PORT}"

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
Description=Passive Workers — worker (Helsinki/FI)
After=pw-coordinator.service network-online.target
Wants=pw-coordinator.service
[Service]
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
Environment=PW_COORDINATOR=$URL
Environment=PW_OWNER=helsinki
Environment=PW_NAME=hel
Environment=PW_COUNTRY=FI
Environment=PW_ANSWER_MODEL=llama3.2:latest
Environment=PW_LENS=first_principles
Environment=PW_POLL=2
Environment=PW_WEB_BACKEND=ddgs
Environment=PYTHONUNBUFFERED=1
Environment=PW_OLLAMA_TIMEOUT=480
Environment=PW_RESEARCH_GEN_TIMEOUT=900
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
