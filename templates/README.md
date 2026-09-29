# Templates

Starter applications that are intended to be copied by `agentengine create`.

Python starters under `templates/` pin the published SDK packages to an exact
version and keep `uv.toml` cooldown policy so this repo's CI and local template
development resolve the same releases. `agentengine create` keeps those pins
when copying a starter into a new project, so scaffolded agents deploy without
manual lock cleanup. Standalone starters use a default client and read
`LLM_API_KEY`.

Ordered simplest first.

| Template | Description | Features |
|----------|-------------|----------|
| [`hello-world-agent/`](hello-world-agent/) | Minimal Daily inspiration assistant with date and optional memory (LangGraph) | Memory |
| [`hello-world-agent-adk/`](hello-world-agent-adk/) | Minimal Daily inspiration assistant with date and optional memory (Google ADK 2) | Memory |
| [`hello-world-agent-langgraph-ts/`](hello-world-agent-langgraph-ts/) | Minimal Daily inspiration assistant with date and optional memory (TypeScript) | Memory |
| [`insurance-agent/`](insurance-agent/) | Insurance starter with quoting, policy management, claims, and human review (LangGraph) | Memory · Human-in-the-loop (suspend/resume) |
| [`insurance-agent-adk/`](insurance-agent-adk/) | Insurance starter with quoting, policy management, claims, and human review (Google ADK 2) | Memory · Human-in-the-loop (suspend/resume) |
| [`insurance-agent-ts/`](insurance-agent-ts/) | Full-featured insurance starter (TypeScript) with deep agents and a subagent | Memory · Deep agent · Subagents · Human-in-the-loop (suspend/resume) |
| [`chatbot-client/`](chatbot-client/) | Next.js chatbot UI with streaming chat and a human-review queue | Human-in-the-loop client |

## `agentengine create` anchors

The CLI replaces two marked sections with the selected client's import and
constructor, or a manual-setup stub. Generated clients use `LLM_API_KEY` and
contain no provider dispatch table. Older CLIs that rewrite `DEFAULT_PROVIDER`
are no longer supported.

Each builder file has exactly one ordered pair of each marker (use `//` in
TypeScript):

- `# agentic-create: llm-imports:start` / `# agentic-create: llm-imports:end`
- `# agentic-create: llm-builder:start` / `# agentic-create: llm-builder:end`

Keep client imports inside the imports section and the model and constructor
inside the builder section. Both sections are replaced in full; application
code outside them is preserved. Missing or duplicate markers fail scaffolding.
