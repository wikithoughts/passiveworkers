#!/usr/bin/env bash
# Ask the live council a question (run while the network is up, e.g. via mac_join.sh).
#   bash scripts/ask.sh "your question here"          (asker defaults to 'you')
#   PW_ASKER=alice bash scripts/ask.sh "..."          (set the asker)
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
# shellcheck disable=SC1091
source .vps.env
export PW_TOKEN PW_COORDINATOR="http://127.0.0.1:${PW_PORT}"
python -m council.net.submit --asker "${PW_ASKER:-you}" "$*"
