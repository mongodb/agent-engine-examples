# Hello World Agent Template

A minimal Magenta SDK starter adapted from the hello-world blueprint in
`meticulous-dft/magenta-blueprints`.

This template builds a small daily inspiration assistant named Daily. It can
look up the current date and remember lightweight user profile details across
conversations when memory is enabled.

## What This Template Includes

- A compact LangGraph app built with the Magenta SDK
- Remote tools for date lookup and optional user-memory read/write
- Runtime LLM selection for Gemini, OpenAI, Anthropic, or Cerebras
- Optional model hints in `agent.yaml`
- `agent.yaml` deploy metadata, with local-dev settings (service ports) in `dev.yaml`

## Quick Start

```bash
cp env.example .env
```

Before you start the app, edit `.env` and:

- Set one LLM provider key
- Leave `MONGODB_URI` empty for `agentic dev`; set it only when running against
  your own MongoDB deployment
- If you want to pin a provider or model, set `config.provider` and/or
  `config.model` in `agent.yaml`
- To enable persistent profile memory, set `features.memory: true` in
  `agent.yaml` and set `VOYAGE_API_KEY`
- If you use Azure OpenAI, set `OPENAI_BASE_URL` and optionally
  `AZURE_OPENAI_API_VERSION`
- If you use Grove Foundry, set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` plus
  the provider-specific base URL, such as `OPENAI_BASE_URL` or
  `ANTHROPIC_BASE_URL`

Then start the local stack:

```bash
agentic dev up
```

Once the local stack is running:

- Open the Playground UI at `http://localhost:3000` to chat with the agent
- The playground port is configured in `dev.yaml` under
  `services.playground.port`

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
