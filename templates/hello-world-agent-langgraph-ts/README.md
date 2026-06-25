# hello-world-agent

A minimal starter agent built on `@magenta/magenta-sdklanggraph-ts` — the
TypeScript counterpart of [`templates/hello-world-agent`](../hello-world-agent).

It builds a small daily inspiration assistant named Daily that can look up the
current date and can request human review.

## What This Template Includes

- A compact LangGraph app built on `@magenta/magenta-sdklanggraph-ts`
- Tools for date lookup and optional user-memory read/write
- Runtime LLM selection for Gemini, OpenAI, Anthropic, or Cerebras
- Optional model hints in `agent.yaml`
- `agent.yaml` metadata for local dev and the playground UI port

## SDK Dependencies (Local Source — Pre-Publication)

While `@magenta/magenta-sdklanggraph-ts` is under active development and not yet
published to npm, this template references the SDK packages via `file:` paths
to a local `agentic-platform` checkout. The expected on-disk layout is:

```
<parent-dir>/
├── agentic-platform/                # https://github.com/10gen/agentic-platform
│   └── client-libraries/packages/
│       ├── sdk-core-ts/
│       ├── runner-shared-ts/
│       └── magenta-sdklanggraph-ts/
└── magenta-examples/                # this repo
    └── templates/
        └── hello-world-agent-langgraph-ts/    # this template
```

If your checkout layout differs, update the three `@magenta/*` `file:` paths in
`package.json` accordingly.

## Quick Start

### 1. Build the SDK chain (only required once, or after SDK edits)

```bash
cd /path/to/agentic-platform/client-libraries/packages/sdk-core-ts && npm install && npm run build
cd ../runner-shared-ts && npm install && npm run build
cd ../magenta-sdklanggraph-ts && npm install && npm run build
```

### 2. Set up this agent

```bash
npm install
```

Before you start the app, review `agent.yaml` and `.env` if you need to customize model or provider-specific settings:

- Set one LLM provider key
- Leave `ORG_ID=f2c4f6f50202354ad2257e1e` for local development
- Leave `MONGODB_URI` empty for `agentic dev`; set it only when running against
  your own MongoDB deployment
- If you want to pin a provider or model, set `config.provider` and/or
  `config.model` in `agent.yaml`
- To enable persistent profile memory, set `features.memory: true` in
  `agent.yaml`, provide `VOYAGE_API_KEY`, and run with a reachable
  `MEMORY_SERVER_URL`
- If you use Azure OpenAI, set `OPENAI_BASE_URL` and optionally
  `AZURE_OPENAI_API_VERSION`
- If you use Grove Foundry, set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` plus
  the provider-specific base URL, such as `OPENAI_BASE_URL` or
  `ANTHROPIC_BASE_URL`

### 3. Run the agent

```bash
npm run dev
```

Or with the `agentic` CLI:

```bash
agentic dev up
```

Once the local stack is running:

- Open the Playground UI at `http://localhost:3000` to chat with the agent

Stop the local stack with:

```bash
agentic dev down
```

## Register It On The Platform

```bash
agentic init
agentic build
agentic deploy
```

`agentic init` is where org/project selection and workspace registration happen.
Secrets stay in `.env`; runtime feature flags and model hints stay in
`agent.yaml`.

## Project Layout

```text
hello-world-agent/
├── agent.yaml
├── env.example
├── package.json
├── tsconfig.json
└── src/hello_world_agent/
    ├── index.ts
    ├── main.ts            # Wires App + LangGraph builder, calls app.run()
    ├── llm.ts             # Provider selection
    ├── state.ts           # LangGraph state annotation
    ├── systemMessage.ts   # System prompt
    └── tools.ts           # get_current_date, request_human_review
```

## Notes On The SDK

This template uses the SDK for:

- `App` — entry point, agent registration, HTTP runtime
- `app.llm(...)` — wraps the LangChain LLM for platform-routed inference
- `app.checkpointer()` — MongoDB-backed LangGraph checkpointer
- `app.memory.*` — async semantic / episodic / taxonomic memory facades
- `app.validateLlmResponse(...)` — guardrails validation (async)
- `app.entrypoint(...)` + `app.run()` — graph builder registration and runtime

Tools are defined with LangChain's `tool()` helper (with Zod schemas) and
passed to `ToolNode` directly.
