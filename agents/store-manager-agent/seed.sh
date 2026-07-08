#!/usr/bin/env bash
# Seed (or reset) the Store Manager Copilot's operational + inventory data.
#
# Wraps the `store_manager_agent.seed` script so you don't have to remember the
# docker exec incantation. It auto-detects how to run:
#   * if the app container is running, it execs inside it (so it uses the
#     container's MongoDB networking and pinned venv);
#   * otherwise it falls back to running on the host via `uv run`.
#
# Usage:
#   ./seed.sh             # idempotent seed / reset of the Mongo domain data
#   ./seed.sh --drop      # full teardown: drop every store_db collection
#
# Note: this seeds the MongoDB DOMAIN data only. The four memory types are
# seeded through the running agent — send "seed store memory" in the playground,
# or run:  uv run python demo.py --only-section seed
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONTAINER="store-manager-agent-app-1"

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  echo "→ Seeding inside container ${CONTAINER}…"
  docker exec \
    -w /app/agents/store-manager-agent \
    -e PYTHONPATH=/app/agents/store-manager-agent/src \
    "$CONTAINER" \
    /app/.venv-aer-tool/bin/python -m store_manager_agent.seed "$@"
else
  echo "→ App container not running; seeding on the host via uv…"
  uv run store-manager-seed "$@"
fi
