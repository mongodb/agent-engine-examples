# hello-world-agent-openai

Atlas Agent Engine is a MongoDB Atlas service for building, running, and deploying AI agents. You write the agent as a LangGraph, Google ADK, or OpenAI Agents SDK app, and the `agentengine` CLI runs it locally and deploys it to the platform.

This example is a minimal OpenAI Agents SDK agent named Daily. It returns a short daily inspiration message, calls a tool to look up today's date, and, when memory is enabled, saves and recalls user profile details. The entrypoint returns a native `Agent`, and the platform runs it through the OpenAI Agents SDK adapter. OpenAI Agents SDK support in Agent Engine is experimental.

## File Layout

| Path | Purpose |
|---|---|
| `agent.yaml` | App name, Python entrypoint, `framework: openai-agents`, runtime feature flags, and tool placement across sandboxes. |
| `dev.yaml` | Local-only settings for `agentengine dev up`: service ports and the local MongoDB container. |
| `env.example` | Environment variable names. Copy to `.env` if missing. |
| `pyproject.toml` | Python dependencies, including `agent-engine-sdk-openai-agents` and `openai-agents`. |
| `pyrightconfig.json` | Type-checking settings. |
| `src/hello_world_agent/main.py` | Creates the `App` and returns the `Agent`. |
| `src/hello_world_agent/llm.py` | LLM provider and model selection through `OpenAIResponsesModel`. |
| `src/hello_world_agent/tools.py` | The `get_current_date`, `save_user_info`, and `recall_user_info` tools. |
| `src/hello_world_agent/system_message.py` | The system prompt that defines Daily's behavior. |
| `README.md` | Setup, local development, and deployment walkthrough. |

## Configuration

These are the settings a user is most likely to change:

- `agent.yaml`: `name` and `entrypoint` identify the app. `features.memory` turns long-term memory on or off, and the user sets it here. `features.durable_workflow` preserves workflow state across runtime restarts. The `sandboxes` lists decide which tools run in the agent sandbox and which run in the tool sandbox.
- `src/hello_world_agent/llm.py`: `MODEL` and the `OpenAIResponsesModel` constructor select the model and endpoint. The template is configured for OpenAI. Every provider reads its key from `LLM_API_KEY`. To use another OpenAI-compatible endpoint, pass its `base_url` to `AsyncOpenAI`.
- `.env`: `LLM_API_KEY` is required. `VOYAGE_API_KEY` is required when memory is enabled. Leave `MONGODB_URI` empty for local development and set it only when the agent connects to a MongoDB deployment outside the local stack.
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
