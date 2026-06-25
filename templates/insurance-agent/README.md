# Insurance Agent Template

A starter insurance assistant intended for the future `agentic create` flow.

This template gives you a realistic but compact app that can quote auto
insurance, create policies, file claims, and suspend for human review when a
claim needs manual approval.

## What This Template Includes

- A runnable Magenta SDK app with policy, quote, and claim tools
- Optional long-term memory for customer context
- `agent.yaml`-driven runtime settings for feature flags and optional model hints
- A simple MongoDB-backed policy and claim store
- `agent.yaml` metadata for build and deploy workflows, with local-dev settings
  (service ports) in `dev.yaml`

## Quick Start

```bash
cp env.example .env
agentic dev up
```

Before you start the app, edit `.env` and:

- Leave `MONGODB_URI` empty for `agentic dev`; set it only when running against
  your own MongoDB deployment
- Set one LLM provider key in `.env`
- If you use Grove Foundry, set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` plus
  the provider-specific base URL, such as `OPENAI_BASE_URL` or
  `ANTHROPIC_BASE_URL`
- If you want to pin a provider or model, set `config.provider` and/or
  `config.model` in `agent.yaml`
- If you enable `features.memory: true` in `agent.yaml`, also provide `VOYAGE_API_KEY`
- If you enable `features.guardrails: true` in `agent.yaml`, set `GUARDRAILS_SERVER_URL`

Once the local stack is running:

- Open the Playground UI at `http://localhost:3000` to chat with the agent
- The playground port is configured in `dev.yaml` under `services.playground.port`

Stop the local stack with:

```bash
agentic dev down
```

## Register It On The Platform

When you are happy with the local app and want to connect it to the platform:

```bash
agentic init
agentic build
agentic deploy
```

`agentic init` is the point where org/project selection and workspace
registration should happen. This template keeps secrets in `.env`, while
runtime feature flags and optional model hints live in `agent.yaml`.

## Project Layout

```text
insurance-agent/
├── agent.yaml
├── dev.yaml
├── env.example
├── pyproject.toml
├── pyrightconfig.json
└── src/insurance_agent/
    ├── __init__.py
    ├── main.py
    └── policy_store.py
```
