# Insurance Agent Template (Google ADK)

A starter insurance assistant intended for the future `agentengine create` flow,
built on Google ADK 2.

This template gives you a realistic but compact app that can quote auto
insurance, create policies, file claims, and suspend for human review when a
claim needs manual approval.

**Features:** Memory (wired, off by default) · Human-in-the-loop (suspend/resume)

## What This Template Includes

- A runnable ADK SDK app with policy, quote, and claim tools
- Native ADK human-in-the-loop: a `LongRunningFunctionTool` review wait and a
  `require_confirmation` policy-binding confirmation, suspended and resumed by
  the platform
- Optional long-term memory for customer context
- `agent.yaml`-driven runtime settings for feature flags and tool placement
- A simple MongoDB-backed policy and claim store
- `agent.yaml` metadata for build and deploy workflows, with local-dev settings
  (service ports) in `dev.yaml`

Unlike the LangGraph insurance starter, there is no graph to assemble: the
entrypoint returns a native `google.adk.agents.LlmAgent`, and the platform runs
it through the ADK adapter. Suspend/resume is managed by the platform,
so there is no checkpointer call.

## Human-in-the-Loop Flow

1. When the risk analysis returns `recommendation` = "review_recommended" or
   "manual_review_required" (any claim of $1,000 or more, or any medium/high
   risk assessment), the agent calls `human_review`. The tool is wrapped in a
   native ADK `LongRunningFunctionTool`, so the execution suspends until a human
   answers. The platform records the wait as an interrupt with a stable ID.
2. Reviewers respond through the Playground review queue with the JSON shape
   declared by `HUMAN_REVIEW_RESPONSE_SCHEMA` (`decision` plus optional
   `reviewer_notes`).
3. The same session is resumed by sending the interrupt answer in a
   `resume_map` keyed by the interrupt ID; the agent then resolves the claim
   with `resolve_claim` and notifies the customer.
4. Separately, `create_policy` is wrapped in `FunctionTool(
   require_confirmation=True)`, so the customer confirms a policy bind before
   it is created.

## Quick Start

```bash
cp env.example .env
agentengine dev up
```

Before you start the app, edit `.env` and:

- Leave `MONGODB_URI` empty for `agentengine dev`; set it only when running against
  your own MongoDB deployment
- Set `LLM_API_KEY` for the configured client in `.env`
- Review the LLM builder in `src/insurance_agent/llm.py`. To switch providers,
  update the import, constructor, and `MODEL`; the key remains `LLM_API_KEY`.
- If you enable `features.memory: true` in `agent.yaml`, also provide `VOYAGE_API_KEY`

Once the local stack is running:

- Open the Playground UI at `http://localhost:3000` to chat with the agent
- The playground port is configured in `dev.yaml` under `services.playground.port`

Stop the local stack with:

```bash
agentengine dev down
```

## Register It On The Platform

When you are happy with the local app and want to connect it to the platform:

```bash
agentengine init
agentengine build
agentengine deploy
```

`agentengine init` is the point where org/project selection and workspace
registration should happen. This template keeps secrets in `.env`, while
runtime feature flags live in `agent.yaml`; the model lives in the LLM builder.

## Project Layout

```text
insurance-agent-adk/
├── agent.yaml
├── dev.yaml
├── env.example
├── pyproject.toml
├── pyrightconfig.json
└── src/insurance_agent/
    ├── __init__.py
    ├── llm.py
    ├── main.py
    └── policy_store.py
```
