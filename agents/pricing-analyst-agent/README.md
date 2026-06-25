# Pricing Analyst Agent (AtlasAP SDK)

A pricing workflow demo built on the AtlasAP SDK. This example packages the
pricing analyst scenario into `atlasap` with self-contained mock tools,
memory seeds, procedural replay, and contract-finalization guardrails.

## What It Shows

- Pricing analysis with deterministic mock Coupa and deal-history tools
- Contract drafting across multiple categories with policy-based review gates
- Background episodic summaries for completed sessions
- Procedural memory extraction and replay across sessions
- Memory-backed citations for account context and benchmark data

## Quick Start

### Prerequisites

- Python 3.11+
- Docker for `agentic dev`
- One LLM API key
- `VOYAGE_API_KEY` for memory embeddings

### Start The Local Stack

```bash
cd agents/pricing-analyst-agent
cp env.example .env
agentic dev up
```

This starts the local app, MongoDB, and playground services. It also creates the
`app` container used by the repo's `.devcontainer` config.

### Install Dependencies On The Host

Do not run `uv sync` inside the dev container. If you need to install or refresh
dependencies, do it from your host shell so the running container can be
restarted cleanly afterward:

```bash
cd agents/pricing-analyst-agent
uv sync --group dev
agentic dev stop
agentic dev up
```

The local dev stack prepares its own runtime environment when it starts. Host-side
`uv sync` is the supported way to refresh dependencies and local tooling for
this example.

### Open The App/Dev Container

After the stack is up, use the dev container or app container for runtime tasks
like memory seeding and debugging. Do not run `uv sync` there.

```bash
docker exec -it pricing-analyst-agent-app-1 bash
```

If the container name differs on your machine, run `docker ps --filter name=pricing-analyst-agent` to find it. If you are using Cursor or VS Code,
you can also attach via `agents/pricing-analyst-agent/.devcontainer/devcontainer.json`,
which targets the same `app` service and opens the workspace at
`/app/agents/pricing-analyst-agent`.

### Seed Demo Memories Inside The Container

The pricing demo relies on seeded account context and benchmark memories:

```bash
cd /app/agents/pricing-analyst-agent
set -a && source .env && set +a
uv run pricing-analyst-seed-memories
```

This loads:

- Semantic memories for MedPoint and Mercy Health account context
- Org-wide benchmark memories for Tier 1 pricing patterns
- Taxonomic memories for pricing terminology and category definitions

The seed script is safe to rerun. It refreshes the demo seed documents before
loading them again.

### Use The Playground

Once `agentic dev up` is running and the memories are seeded, open the local
playground and try the prompts below.

## Troubleshooting And Debugging

Most debugging is easiest from inside the pricing agent DevContainer or the app
container:

```bash
docker exec -it pricing-analyst-agent-app-1 bash
cd /app/agents/pricing-analyst-agent
```

### Check Service Health

From your host machine:

```bash
docker ps --filter name=pricing-analyst-agent
curl http://localhost:3000
curl http://localhost:8000/health
```

Useful containers in this stack:

- `pricing-analyst-agent-app-1`
- `pricing-analyst-agent-mongodb-1`
- `pricing-analyst-agent-ui`

### Verify Required Environment Variables

From inside the DevContainer/app container, check whether the important runtime
variables are present without printing secret values:

```bash
for name in \
  MONGODB_URI \
  ENABLE_MEMORY \
  ENABLE_TRACING \
  ENABLE_GUARDRAILS \
  VOYAGE_API_KEY \
  OPENAI_API_KEY \
  OPENAI_BASE_URL \
  GEMINI_API_KEY \
  CEREBRAS_API_KEY \
  ANTHROPIC_API_KEY \
  ANTHROPIC_BASE_URL
do
  if [ -n "${!name:-}" ]; then
    echo "$name is set"
  else
    echo "$name is missing"
  fi
done
```

If memory seeding fails, verify `MONGODB_URI` and `VOYAGE_API_KEY` first.

### Watch Agent Logs From The DevContainer

The local stack writes agent logs into the repo under `logs/` and observability
artifacts under `observability/`:

```bash
cd /app/agents/pricing-analyst-agent
ls -1 logs
ls -1 observability
tail -f logs/*
tail -f observability/*.jsonl
```

Typical files include:

- `logs/Pricing Analyst Agent-aer-*.log`
- `logs/Pricing Analyst Agent-tool-*.log`
- `observability/executions.jsonl`
- `observability/traces.jsonl`

### Watch Docker Logs From The Host

If the app is not starting cleanly, inspect the container logs directly from
the host:

```bash
docker logs -f pricing-analyst-agent-app-1
docker logs -f pricing-analyst-agent-mongodb-1
docker logs -f pricing-analyst-agent-ui
```

### Re-Run Common Recovery Steps

If you changed dependencies, refresh them from the host and restart the stack:

```bash
cd agents/pricing-analyst-agent
uv sync --group dev
agentic dev stop
agentic dev up
```

Then, from the DevContainer/app container, rerun the seed step if needed:

```bash
cd /app/agents/pricing-analyst-agent
set -a && source .env && set +a
uv run pricing-analyst-seed-memories
```

## Demo Flow

### Session 1

1. `Show me the top 5 product categories by revenue for MedPoint Pharmacies over the last 12 months and plot it for me.`
2. `What's the specialty-by-brand ratio for this account? Compare it to the average across all Tier 1 accounts.`
3. `Let's build a 2-year contract renewal for MedPoint Pharmacies covering their top 3 categories. What should the pricing and margin targets look like?`
4. `Why is the generic analgesic margin only 5.3%? Can we get it to 6%?`

### Session 2

1. Start a new conversation.
2. `I'm starting a deal for Mercy Health covering the same top 3 categories as the MedPoint Pharmacies contract. What can you tell me?`

The intended story is that Session 1 teaches the agent a reusable renewal
procedure, and Session 2 reuses that workflow for a new Tier 1 customer.

### Guardrail Path

Try finalizing a contract with a generic analgesic target margin above the safe
range. The agent should trigger `finalize_contract`, hit the policy review
check, and suspend for human approval instead of auto-finalizing.

## Project Structure

```text
pricing-analyst-agent/
├── src/pricing_analyst_agent/
│   ├── llm.py              # Multi-provider LLM selection
│   ├── main.py             # Agent graph, procedure replay, HITL contract flow
│   ├── seed_memories.py    # Local memory seeding utility
│   └── tools.py            # Pricing math, Coupa lookup, deal history, plotting
├── memory-seeds/
│   ├── semantic.json
│   ├── taxonomic.json
│   └── episodic.json
├── tests/
│   ├── test_graph.py
│   └── test_tools.py
├── agent.yaml
├── dev.yaml
├── env.example
└── pyproject.toml
```

## Testing

```bash
cd agents/pricing-analyst-agent
uv run pytest tests/
uv run ruff check src tests
```
