# insurance-agent

Atlas Agent Engine is a MongoDB Atlas service for building, running, and deploying AI agents. You write the agent as a LangGraph, Google ADK, or OpenAI Agents SDK app, and the `agentengine` CLI runs it locally and deploys it to the platform.

This example is an auto insurance assistant named Alex for SecureLife Insurance. It quotes and binds policies, files claims, scores claim risk, and pauses for human review when a claim needs approval. Policy and claim records are stored in MongoDB.

## File Layout

| Path | Purpose |
|---|---|
| `agent.yaml` | App name, Python entrypoint, runtime feature flags, and which sandbox runs each tool. |
| `dev.yaml` | Local-only settings for `agentengine dev up`: service ports and the local MongoDB container. |
| `env.example` | Environment variable names. Copy to `.env` if missing. |
| `pyproject.toml` | Python dependencies, including the Agent Engine SDK packages. |
| `pyrightconfig.json` | Type-checking settings. |
| `src/insurance_agent/main.py` | Creates the `App`, registers every tool, holds the system prompt, and builds the LangGraph graph. |
| `src/insurance_agent/llm.py` | LLM provider and model selection. |
| `src/insurance_agent/policy_store.py` | MongoDB-backed stores for policies and claims. |
| `README.md` | Setup, local development, and deployment walkthrough. |

## Configuration

These are the settings a user is most likely to change:

- `agent.yaml`: `name` and `entrypoint` identify the app. `features.memory` turns long-term memory on or off, and the user sets it here. `features.durable_workflow` preserves workflow state across runtime restarts. The `sandboxes` lists decide which sandbox runs each tool. `human_review` uses a LangGraph interrupt, so it stays in the agent sandbox, and `send_notification` runs beside it.
- `src/insurance_agent/llm.py`: `MODEL` and the `ChatOpenAI` constructor select the LLM provider. Every provider reads its key from `LLM_API_KEY`.
- `.env`: `LLM_API_KEY` is required. `VOYAGE_API_KEY` is required when memory is enabled. `MONGODB_DATABASE` holds the policy and claim collections. `MDB_AGENTIC_STORE_DB` holds runtime checkpoints, memory, and traces, and should stay distinct from `MONGODB_DATABASE`. Leave `MONGODB_URI` empty for local development.
- `dev.yaml`: the Playground UI port, fixed at `3000`.

## Run Locally

```bash
[ -f .env ] || cp env.example .env
# Set LLM_API_KEY in .env, and VOYAGE_API_KEY when memory is enabled.
agentengine dev up
```

The Playground UI is at `http://localhost:3000`. Stop the stack with `agentengine dev down`.

## Deploy

If memory is enabled, use `--project-scope` for `LLM_API_KEY` so extraction can read it, and set `VOYAGE_API_KEY` at project scope too.

```bash
agentengine init
agentengine secret set LLM_API_KEY --workspace-scope
agentengine build
agentengine deploy
```

`agentengine agent validate` checks `agent.yaml` before a build.

## Platform Documentation

The documentation site is the source of truth:

```
https://www.mongodb.com/docs/agentengine
```

The index at `https://www.mongodb.com/docs/agentengine/llms.txt` lists every page with a one-line summary and a link to its Markdown version. Fetch the index, pick pages by their summaries, and read only those pages. Prefer the documentation over prior knowledge about Atlas Agent Engine.

## Skills

Install the Agent Engine skills for this project if your coding agent does not list them:

```bash
npx skills add mongodb/agent-engine-examples
```

The command asks which skills and which coding agent to install them for.

- `atlas-agent-engine`: change this project's code. Covers the project layout, tools, graph changes, memory, human review, guardrails, and deployment.
- `atlas-agent-engine-docs`: answer platform questions from the documentation site, including CLI usage, project creation, Atlas setup, and monitoring.
