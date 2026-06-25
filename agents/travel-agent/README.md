# Travel Agent (Runner SDK)

A travel disruption and re-accommodation demo built on the Runner SDK. The agent
showcases planning, policy retrieval, tool use, memory, approval gates, and
action orchestration on top of a synthetic IRROPS dataset.

## What It Shows

- Disruption triage for an IRROPS event
- Passenger prioritization across impacted travelers
- Policy-aware option ranking for a selected PNR
- Supervisor approval for expensive or exception-based recovery
- Action orchestration: hold, reissue, hotel, voucher, notification
- Seeded semantic, episodic, and taxonomic memory plus learned procedural
  replay for follow-on sessions

## Graph Topology

```
START -> discover -> supervisor
                     |-> impact_flow
                     |-> reaccommodation_flow
                     |-> resolution_flow <-> tools
                     |-> direct_response

procedure replay: impact_flow -> reaccommodation_flow -> resolution_flow -> respond
```

## Quick Start

### Prerequisites

- Python 3.11+
- Docker Desktop running
- `agentic` CLI 0.1.26-alpha or newer
- One LLM API key (Cerebras, Gemini, or OpenAI)
- `VOYAGE_API_KEY` for memory embeddings

### 1. Configure `.env`

```bash
cd agents/travel-agent
cp env.example .env
```

Then edit `.env` and set, at minimum:

```env
MONGODB_URI=<atlas connection string OR leave blank for the local mongo container>
VOYAGE_API_KEY=<your voyage key>

# Default agent.yaml uses config.provider=openai
OPENAI_API_KEY=<key>
```

To use Gemini or Cerebras instead, change `config.provider` and `config.model`
in `agent.yaml` and set the matching API key in `.env`.

Do not set `ORG_ID`, `PROJECT_ID`, or `APP_ID` in `.env` for local
`agentic dev`. The generated dev stack injects those runtime identifiers into
the containers so OE, AER, the Memory UI, and the memory server share the same
scope.

The local memory stack defaults to `agentic_memory`; leave memory database
variables unset unless you intentionally need a custom database.

Memory is enabled in `agent.yaml`. Tracing is always enabled by the SDK, so
this example does not use `ENABLE_MEMORY` or `ENABLE_TRACING` in `.env`.

### 2. Sync host dependencies (once)

Run this on your host so editors, type-checkers, and pytest work locally. Do
NOT run `uv sync` inside the dev container.

```bash
cd agents/travel-agent
uv sync --group dev
```

### 3. Start the local stack

```bash
agentic dev up
```

This starts the app, MongoDB (local container), memory server, OE, and the
Playground UI on `http://localhost:3000`.

Wait until the app container is healthy:

```bash
docker ps --filter name=travel-agent
# travel-agent-app-1   ... Up ... (healthy)
```

### 4. Seed memories

The seed script runs **inside the app container** because that's where the
hot-reload Python environment lives:

```bash
docker exec -it travel-agent-app-1 bash -lc '
cd /app/agents/travel-agent &&
/tmp/agentic-venvs/.venv-aer-tool/bin/python -m travel_agent.seed_demo
'
```

You should see logs like:

```
Seeded travel-agent demo into agentic_memory: 4 semantic, 5 taxonomic, N episodic, 0 procedural
```

`seed_demo` is safe to rerun. It refreshes semantic / episodic / taxonomic
memories scoped to the runtime `ORG_ID` / `PROJECT_ID` / `APP_ID`
provided by the dev stack.

Procedural memory is skipped by default so Session 1 demonstrates the
learn-from-resolution flow. To also seed the canonical playbook (so Session 2
replay works without going through Session 1 first):

```bash
docker exec -it travel-agent-app-1 bash -lc '
cd /app/agents/travel-agent &&
SEED_PROCEDURAL_MEMORY_ON_STARTUP=true \
/tmp/agentic-venvs/.venv-aer-tool/bin/python -m travel_agent.seed_demo
'
```

### 5. Open the Playground

Visit `http://localhost:3000`, choose **Travel Agent**, and drive the demo
flow below. Verify memories appear under the **Memory** tab — you should see
4 semantic, 5 taxonomic, and a handful of episodic entries scoped to the
current local Playground user.

## Demo Flow

### Session 1

1. `Flight TA217 to New York is cancelled. Re-accommodate impacted passengers.`
2. `Handle PNR TRV-00421 first.`
3. `Book the best compliant option.`
4. Approve the exception in the UI when prompted.

After the approved resolution, the agent extracts a reusable procedure from the
session and stores it in procedural memory under the runtime project and
app scope injected by the dev stack.

### Procedure Replay (Session 2)

Open a fresh session and ask:

1. `Flight TA552 to Chicago is cancelled due to weather. Re-accommodate impacted passengers.`

The agent matches the learned playbook from Session 1 and replays disruption
triage → passenger handling → resolution automatically.

## Synthetic Data

The app ships with mock data for:

- 1 flagship weather cancellation (`DISR-1001`)
- 1 replay weather cancellation (`DISR-1004` / `TA552` to Chicago)
- Detailed hero passengers plus generated impacted travelers for both demo
  disruptions
- Multiple recovery options with trade-offs
- Policy rules for hotel, voucher, same-cabin, and manual review cases
