# hello-world-agent-langgraph-ts

Atlas Agent Engine is a MongoDB Atlas service for building, running, and deploying AI agents. You write the agent as a LangGraph, Google ADK, or OpenAI Agents SDK app, and the `agentengine` CLI runs it locally and deploys it to the platform.

This example is a minimal TypeScript LangGraph agent named Daily. It returns a short daily inspiration message, calls a tool to look up today's date, and, when memory is enabled, saves and recalls user profile details.

## File Layout

| Path | Purpose |
|---|---|
| `agent.yaml` | App name, Node entrypoint, `framework: langgraph`, `language: typescript`, and runtime feature flags. |
| `dev.yaml` | Local-only settings for `agentengine dev up`: service ports and the local MongoDB container. |
| `env.example` | Environment variable names. Copy to `.env` if missing. |
| `package.json` | Dependencies, including the `@mongodb-js/agent-engine-*` packages, and the build and dev scripts. |
| `pnpm-workspace.yaml` | pnpm build allowlist. |
| `tsconfig.json` | TypeScript build settings. |
| `src/hello_world_agent/main.ts` | Creates the `App`, assembles the LangGraph graph, and connects the LLM and tools. Compiled to `dist/`. |
| `src/hello_world_agent/llm.ts` | LLM provider and model selection. |
| `src/hello_world_agent/state.ts` | LangGraph state annotation for the message list. |
| `src/hello_world_agent/systemMessage.ts` | The system prompt that defines Daily's behavior. |
| `src/hello_world_agent/tools.ts` | The `get_current_date`, `save_user_info`, and `recall_user_info` tools. |
| `src/hello_world_agent/index.ts` | Re-exports the module's public symbols. |
| `README.md` | Setup, local development, and deployment walkthrough. |

## Configuration

These are the settings a user is most likely to change:

- `agent.yaml`: `name` and `entrypoint` identify the app. `features.memory` turns long-term memory on or off, and the user sets it here. `features.durable_workflow` preserves workflow state across runtime restarts. The `sandboxes` lists decide which tools run in the agent sandbox and which run in the tool sandbox.
- `src/hello_world_agent/llm.ts`: `MODEL` and the `ChatOpenAI` constructor select the LLM provider. Every provider reads its key from `LLM_API_KEY`.
- `.env`: `LLM_API_KEY` is required. `VOYAGE_API_KEY` is required when memory is enabled. `MEMORY_SERVER_URL` is required when memory is enabled and `agentengine dev up` is not providing it. Leave `MONGODB_URI` empty for local development. `MDB_AGENTIC_STORE_DB` sets the runtime database for checkpoints and traces. `PORT` sets the local server port.
- `dev.yaml`: the Playground UI port, fixed at `3000`.

## Run Locally

```bash
[ -f .env ] || cp env.example .env
# Set LLM_API_KEY in .env, and VOYAGE_API_KEY when memory is enabled.
agentengine dev up
```

The Playground UI is at `http://localhost:3000`. Stop the stack with `agentengine dev down`. To run the agent without the Playground, use `npm run dev`.

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
