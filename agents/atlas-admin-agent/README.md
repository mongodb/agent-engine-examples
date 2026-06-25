# Atlas Admin Agent

A LangGraph agent that lets users perform management actions against the **MongoDB Atlas Administration API v2** through natural language, with human confirmation on every mutation.

## What It Does

- **Reads freely.** Convenience tools cover common lookups (`list_projects`, `list_clusters`, `get_cluster`, `list_snapshots`, `list_database_users`, `list_network_access_entries`, `list_alerts`, `list_backup_restore_jobs`, `list_organizations`). They paginate automatically.
- **Any mutation, any endpoint.** A generic `atlas_request(method, path, ...)` tool covers the full Atlas Admin API surface. For non-GET requests it suspends execution via `SuspendPayload` so a human reviewer sees the exact request before `atlas_execute` runs it.
- **Always current schemas.** `atlas_describe_endpoint(method, path)` fetches the canonical Atlas Admin API OpenAPI spec from [`mongodb/openapi`](https://github.com/mongodb/openapi) and resolves the request-body schema against the same API version the agent uses on the wire. The system prompt tells the LLM to consult this tool before constructing a mutation body, so schema drift (e.g. the v1 → v2 cluster shape migration) doesn't silently produce `INVALID_ATTRIBUTE` errors.
- **Named workflow: snapshot restore test.** Port of the reference script `test_snapshot_restore_for_project.py`. `run_snapshot_restore_test` fetches clusters, filters qualifying ones, and suspends with a plan. On approval, `execute_snapshot_restore_test` returns a handle immediately and runs the phased restore in the background. `check_snapshot_restore_test(handle_id)` reports progress.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- A MongoDB Atlas programmatic API key with Organization Owner (or narrower) permissions
- One LLM API key (OpenAI, Anthropic, Gemini, or Cerebras)
- A MongoDB instance for agent checkpointing and workflow run state

## Setup

```bash
cd agents/atlas-admin-agent
cp env.example .env
# Edit .env — set ATLAS_PUBLIC_KEY, ATLAS_PRIVATE_KEY, ATLAS_ORG_ID, MONGODB_URI,
# and at least one LLM API key.

make install
```

## Running Locally

```bash
agentic dev up
```

## Architecture

Standard LangGraph ReAct loop:

```
START → agent → should_continue? → tools → agent → ... → END
```

### Approval contract

Every non-GET request flows through `atlas_request` → `SuspendPayload` → human approval → `atlas_execute`. The system prompt forbids the LLM from calling `atlas_execute` outside that round-trip. GETs pass through without approval.

Named workflows follow the same pattern: `run_*` plans and suspends, `execute_*` kicks off the background job on approval, `check_*` reports progress.

### Credentials

For v1 the agent reads Atlas credentials directly from environment variables (`ATLAS_PUBLIC_KEY`, `ATLAS_PRIVATE_KEY`, `ATLAS_ORG_ID`). Identity federation is out of scope.

## Testing

```bash
make test         # run pytest
make test-verbose # verbose
make lint         # ruff + pyright
make ci-tests     # full CI pipeline
```
