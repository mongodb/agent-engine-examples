# insurance-agent-ts

A TypeScript insurance agent built on `@magenta/magenta-sdklanggraph-ts`. It is
the TS counterpart of [`agents/insurance-agent`](../insurance-agent) (Python)
and demonstrates two platform capabilities together:

- **Deep agent** — the graph is a deepagents orchestrator (`app.deepAgent`) with
  built-in planning (todo list), a sandboxed Tool-Pod filesystem/shell, and
  **subagent delegation**. A `claims-risk-analyst` subagent handles claim risk
  assessment. Requires `features.deep_agent: true`.
- **Long-term memory** — tools read and write the Memory Server through
  `app.memory` (semantic customer facts, episodic conversation summaries,
  taxonomic knowledge base). Identity (user/session) is resolved from the ambient
  execution context, so memory **persists across turns and sessions** for the
  same user. Requires `features.memory: true` and `VOYAGE_API_KEY`.

## Layout

```
insurance-agent-ts/
├── agent.yaml                         # slug, entrypoint, features (memory + deep_agent), secrets
├── package.json / tsconfig.json       # TypeScript build
├── dev.yaml                           # local `agentic dev` service ports
├── env.example                        # copy to .env
└── src/insurance_agent_ts/
    ├── main.ts                        # App + app.deepAgent(...) with the risk-analyst subagent
    ├── tools.ts                       # policy/claim tools + memory tools
    ├── policyStore.ts                 # MongoDB-backed policy & claim stores
    ├── riskAnalysis.ts                # pure claim risk-scoring logic
    ├── systemMessage.ts               # agent system prompt
    └── llm.ts                         # provider selection (anthropic/openai/gemini/cerebras)
```

## Configure

Copy `env.example` to `.env` and set:

- An LLM provider key matching `config.provider` in `agent.yaml` (default `anthropic`).
- `VOYAGE_API_KEY` — required for memory embeddings.
- `MONGODB_URI` — leave empty for local `agentic dev` (the CLI provides it). When
  empty and running outside the platform, policy/claim tools report "not
  configured" but memory tools still work.

## Run locally

```bash
agentic dev up          # brings up OE + AER + Tool Pod + memory-server + MongoDB
```

Then open the playground (http://localhost:3000) and talk to the agent.

## What to try

- **Memory across sessions:** In one session tell the agent your name and that
  you have a *clean driving record, no accidents*. Start a **new session as the
  same user** and ask for a quote — it recalls you as a returning customer and
  applies the loyalty + good-driver discounts.
- **Deep-agent delegation:** File a high-value claim (e.g. $15,000 collision).
  The agent files it, delegates to the `claims-risk-analyst` subagent for
  assessment, and — on a high/medium result — calls `human_review`, which
  **suspends** the graph until you resume it with a decision.

## Notes

- The `@magenta/*` SDK dependencies are pinned to a specific
  `magenta-client-libraries` commit that contains the TypeScript memory support
  (AP-1971). Against an older published/checked-out SDK the memory tools will not
  resolve; bump the pinned commit in `package.json` / `pnpm-workspace.yaml` to
  pick up newer SDK changes.
- App-bound memory only works while handling a platform request (it needs the
  execution context's OE URL). It is not reachable from a plain script — use the
  HTTP-direct `Memory` client from `@magenta/agentic-platform-memory-ts` for that.
- The taxonomic knowledge base (`explain_insurance_term`, `get_coverage_options`)
  returns results only once terms have been seeded; `get_coverage_options` falls
  back to built-in descriptions when the knowledge base is empty.
