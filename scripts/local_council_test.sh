#!/usr/bin/env bash
# Local end-to-end test of the NETWORKED council: coordinator + 3 answer agents
# (different lenses/countries) + 1 judge-only agent, all on localhost. Proves the
# networked architecture before a second machine (the VPS) joins.
set -uo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
export PW_TOKEN=dev-token
export PW_HOST=127.0.0.1
export PW_COORDINATOR=http://127.0.0.1:8088
export PW_POLL=1
M=${PW_TEST_MODEL:-gemma3:4b}   # fast model for the smoke test
rm -f council_coordinator.db
mkdir -p /tmp/pw
pids=()
cleanup(){ for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

echo "starting coordinator…"
python -m passiveworkers.net.coordinator_app >/tmp/pw/coord.log 2>&1 & pids+=($!)
for i in $(seq 1 40); do curl -sf $PW_COORDINATOR/healthz >/dev/null 2>&1 && break; sleep 0.5; done

start_agent(){ # name owner country model lens can_judge judge_model
  PW_NAME="$1" PW_OWNER="$2" PW_COUNTRY="$3" PW_ANSWER_MODEL="$4" PW_LENS="$5" \
  PW_CAN_JUDGE="$6" PW_JUDGE_MODEL="$7" \
  python -m passiveworkers.net.agent >"/tmp/pw/agent_$1.log" 2>&1 & pids+=($!)
}

echo "starting agents…"
start_agent us    carlos     sim-US "$M" opportunity 0 ""
start_agent de    dora       sim-DE "$M" skeptic     0 ""
start_agent br    bob        sim-BR "$M" practical   0 ""
start_agent judge judge_node sim-US ""   neutral     1 "$M"
sleep 5   # let them register + heartbeat

echo "=== ONLINE NODES ==="
curl -s $PW_COORDINATOR/status | python -c "import sys,json; d=json.load(sys.stdin); [print(' ',n['name'],n['owner'],n['country'],n['answer_model'] or '(judge-only)','judge='+str(n['can_judge'])) for n in d['online_nodes']]"

echo ""
python -m passiveworkers.net.submit --asker alice "What are two practical ways a small team can cut cloud costs, and one risk of each?"

echo ""
echo "=== FINAL STATUS ==="
curl -s $PW_COORDINATOR/status | python -m json.tool
