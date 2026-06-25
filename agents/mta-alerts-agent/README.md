# MTA Alerts Agent

An AI-powered assistant that answers natural language questions about NYC subway service alerts and real-time arrival times using live MTA data.

## What It Does

The agent exposes three tools:

**`get_subway_alerts`** — fetches live service alerts from the MTA's GTFS-RT feed:
- Filters alerts by subway line (e.g. `A`, `1`, `L`, `N`, `Q`)
- Filters by direction (`north`/`uptown` or `south`/`downtown`)
- Optionally returns only currently active alerts

**`search_stops`** — searches for stations by name:
- Case-insensitive substring match against all 496 NYC subway stations
- Returns matching station names, their GTFS stop IDs, and which lines serve each stop

**`get_stop_arrivals`** — fetches real-time arrival predictions for a specific stop and line:
- Takes a GTFS parent station ID (from `search_stops`) and a line
- Optionally filters by direction
- Returns the next N upcoming arrivals with predicted times and minutes until arrival

For arrival queries the agent automatically calls `search_stops` first to resolve the station name, then calls `get_stop_arrivals` — no need to know GTFS stop IDs. If a station name matches exactly one stop, the agent proceeds without asking for clarification.

Users can ask questions like:
- "Are there any delays on the A train heading uptown?"
- "What alerts are active on the 1 and 2 lines right now?"
- "When is the next A train at 14 St-8 Av?"
- "Next uptown 1 train at 72nd St?"

## Project Structure

```
mta-alerts-agent/
├── src/mta_alerts_agent/
│   └── agent.py          # Agent definition and MTA tool implementation
├── .agentic/
│   ├── entrypoint.py     # Production entrypoint
│   ├── dev-entrypoint.py # Dev supervisor with hot-reload
│   ├── Dockerfile
│   ├── Dockerfile.dev
│   └── docker-compose.dev.yml
├── agent.yaml            # Agent name and entrypoint config
├── dev.yaml              # Local-dev service ports (playground pinned to 3000)
└── pyproject.toml        # Dependencies
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) for Python package management
- Docker and Docker Compose
- The `agentic` CLI (from the AtlasAP SDK)
- At least one LLM provider API key (see below)

## Setup

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

### LLM Provider

The agent selects a provider automatically based on which key is set (in priority order):

| Provider | Environment Variable | Default Model |
|----------|---------------------|---------------|
| OpenAI | `OPENAI_API_KEY` | `gpt-5.4-mini` |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| Google Gemini | `GEMINI_API_KEY` | `gemini-2.0-flash` |
| Cerebras | `CEREBRAS_API_KEY` | `llama3.1-8b` |

At least one must be set or the agent will fail to start.

## Running Locally

```bash
agentic dev up
```

This starts:

| Service | Port | Description |
|---------|------|-------------|
| Orchestrator | 8000 | Main agent entrypoint |
| AER | 8001 | Agent Execution Runtime |
| Tool service | 8002 | Tool execution |
| MongoDB | 27017 | State storage |
| Playground UI | 3000 | Chat interface |

Open [http://localhost:3000](http://localhost:3000) to interact with the agent.

The dev supervisor watches for source changes and automatically restarts services.

## How It Works

The agent is a LangGraph `StateGraph` with two nodes:

1. **`agent`** — LLM decides whether to call a tool or respond directly
2. **`tools`** — executes whichever tool(s) the LLM requests

```
user message → agent → (tool call?) → tools → agent → response
```

For arrival queries the agent typically makes two sequential tool calls: `search_stops` to resolve the station, then `get_stop_arrivals` to fetch live predictions.

**`get_subway_alerts`** fetches protobuf from the MTA GTFS-RT subway alerts feed, parses `informed_entity` fields to match lines/directions, and checks `active_period` timestamps.

**`search_stops`** does a case-insensitive substring search against a hardcoded dict of all 496 NYC subway parent stations (derived from MTA static GTFS `stops.txt`), with each stop ID annotated with the lines that serve it (derived from `trips.txt` + `stop_times.txt`).

**`get_stop_arrivals`** fetches the appropriate per-line-group MTA GTFS-RT trip updates feed (e.g. `nyct%2Fgtfs-ace` for A/C/E), scans `StopTimeUpdate` entries for the requested stop and line, and returns upcoming arrivals sorted by predicted time.

## Observability

Execution traces are written to `observability/executions.jsonl` in JSONL format, capturing node execution status, tool inputs/outputs, token counts, and timing.
