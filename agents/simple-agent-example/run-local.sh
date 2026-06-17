#!/usr/bin/env bash
#
# Start the Runner SDK components (OE, AER, Tool Pod) for local testing.
# Usage: ./run-local.sh
# Stop:  Ctrl-C
#

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

APP_SCRIPT="simple-agent"

if [ ! -f .env ]; then
    echo -e "${RED}Error: .env file not found!${NC}"
    echo "  cp env.example .env"
    echo "  Then edit .env with MONGODB_URI and at least one LLM API key"
    exit 1
fi

mkdir -p logs
set -a
source .env
set +a

OE_PORT=${OE_PORT:-8000}
AER_PORT=${AER_PORT:-8001}
TOOL_PORT=${TOOL_PORT:-8002}
GRPC_PORT=${GRPC_PORT:-50051}

cleanup() {
    echo ""
    echo -e "${YELLOW}Stopping ${APP_SCRIPT} processes...${NC}"
    kill "${TOOL_PID:-0}" "${AER_PID:-0}" "${OE_PID:-0}" 2>/dev/null || true
    wait "${TOOL_PID:-0}" "${AER_PID:-0}" "${OE_PID:-0}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo -e "${GREEN}Starting ${APP_SCRIPT} (Runner components)${NC}"
echo "  OE:   http://localhost:$OE_PORT"
echo "  AER:  http://localhost:$AER_PORT"
echo "  Tool: http://localhost:$TOOL_PORT"
echo ""

pkill -f "$APP_SCRIPT" 2>/dev/null || true
sleep 1

echo -e "${YELLOW}Syncing dependencies...${NC}"
uv sync --group dev

echo -e "${YELLOW}Starting Tool Pod...${NC}"
RUNNER_MODE=tool APP_PORT=$TOOL_PORT uv run "$APP_SCRIPT" >> "logs/tool.log" 2>&1 &
TOOL_PID=$!

echo -e "${YELLOW}Starting AER...${NC}"
RUNNER_MODE=aer APP_PORT=$AER_PORT TOOL_URL=http://localhost:$TOOL_PORT uv run "$APP_SCRIPT" >> "logs/aer.log" 2>&1 &
AER_PID=$!

echo -e "${YELLOW}Starting Orchestrator...${NC}"
RUNNER_MODE=orchestrator APP_PORT=$OE_PORT GRPC_PORT=$GRPC_PORT AER_URL=http://localhost:$AER_PORT TOOL_URL=http://localhost:$TOOL_PORT uv run "$APP_SCRIPT" >> "logs/orchestrator.log" 2>&1 &
OE_PID=$!

echo -e "${YELLOW}Waiting for orchestrator...${NC}"
for i in {1..30}; do
    if curl -sf "http://localhost:$OE_PORT/health" > /dev/null 2>&1; then
        echo -e "${GREEN}Orchestrator ready at http://localhost:$OE_PORT${NC}"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${RED}Orchestrator did not become ready. Check logs/orchestrator.log${NC}"
    else
        sleep 1
    fi
done

echo ""
echo "  Test: curl -s -X POST http://localhost:$OE_PORT/invoke -H 'Content-Type: application/json' -d '{\"message\": \"Search for MongoDB account\", \"thread_id\": \"test-1\"}' | python3 -m json.tool"
echo "  Stop: Ctrl-C"
echo ""

wait
