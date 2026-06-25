#!/usr/bin/env bash
set -e

# Run CI for the workspace
uv sync --group dev
uv run ruff check .
uv run ruff format --check .

# Run CI for each agent
for agent in agents/*; do
  if [ -d "$agent" ]; then
    echo "Running tests for $agent..."
    cd "$agent"
    if [ -f "pyrightconfig.json" ]; then
      uv run pyright
    fi
    if [ -d "tests" ]; then
      uv run pytest tests/ -v --tb=short
    else
      echo "No tests found for $agent."
    fi
    cd - > /dev/null
  fi
done
