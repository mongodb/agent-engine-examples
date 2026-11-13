# Insurance Agent Template (OpenAI Agents SDK)

A starter insurance assistant for the OpenAI Agents SDK.

This template gives you a realistic but compact app that can quote auto
insurance, create policies, file claims, and pause for a human to approve
policy binding and the resolution of any claim that is not low risk.

> **Experimental:** OpenAI Agents SDK support in Agent Engine is experimental.
> Recent `agentengine` CLI versions print a notice when they load this app.

**Features:** Memory (wired, off by default) · Human-in-the-loop (tool approvals)

## What This Template Includes

- A native OpenAI Agents SDK `Agent` built with the OpenAI Agents SDK adapter
  (`agent-engine-sdk-openai-agents`), with policy, quote, and claim tools
- Human-in-the-loop through native tool approvals (`needs_approval=True`),
  suspended and resumed by the platform
- Optional long-term memory for customer context
- A direct LLM client configured by `agentengine create` or in source
- `agent.yaml`-driven runtime settings for feature flags and tool placement
- A simple MongoDB-backed policy and claim store
- `agent.yaml` metadata for build and deploy workflows, with local-dev settings
  (service ports) in `dev.yaml`

The entrypoint returns a native `agents.Agent`, and the platform runs it through
the OpenAI Agents SDK adapter while the Orchestration Engine owns durable
execution, so there is no checkpointer call.

## Human-in-the-Loop Flow

Two tools are registered with `@app.tool(needs_approval=True)`:

- `create_policy`, so a person approves each policy before it is bound.
- `resolve_reviewed_claim`, which the agent calls when the risk analysis returns
  `recommendation` = "review_recommended" or "manual_review_required" (any claim
  of $1,000 or more, or any medium/high risk assessment). The agent proposes
  "approved" or "denied" with a reason citing the risk analysis.

When the agent calls either tool, the run suspends and the platform records an
approval wait. The Playground shows the pending call with its arguments; the
reviewer approves it, or rejects it with an optional message. The answer is sent
in a `resume_map` keyed by the interrupt ID, and the same session resumes:

- Approved: the tool runs, and the agent reports the result and notifies the
  customer.
- Rejected: the tool does not run; the agent receives the reviewer's message,
  tells the customer, and does not retry the call in that turn. A rejected claim
  stays under review; once the customer supplies what the reviewer asked for, the
  agent can propose a resolution again for another review.

Low-risk claims under $1,000 (`recommendation` = "auto_approve") are resolved
with `approve_low_risk_claim`, without a reviewer. The claim store enforces both
paths: `approve_low_risk_claim` refuses any claim whose analysis asked for
review, `resolve_reviewed_claim` refuses an auto-approve claim, and a resolved
claim cannot be analyzed or resolved again.

## Quick Start

```bash
cp env.example .env
```

Before you start the app, edit `.env` and:

- Set `LLM_API_KEY` for the configured client
- Leave `MONGODB_URI` empty for `agentengine dev`; set it only when running against
  your own MongoDB deployment
- Review the LLM builder in `src/insurance_agent/llm.py`. To use another
  OpenAI-compatible endpoint, pass its `base_url` to `AsyncOpenAI` and change
  `MODEL`; the key remains `LLM_API_KEY`.
- If you enable `features.memory: true` in `agent.yaml`, also provide `VOYAGE_API_KEY`

Then start the local stack:

```bash
agentengine dev up
```

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
insurance-agent-openai/
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
