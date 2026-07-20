# Magenta Examples

Standalone example agents built on top of
[magenta-client-libraries](https://github.com/10gen/magenta-client-libraries).

Each example agent lives in the `agents/` directory. Every agent is a
**self-contained application** — it has its own `agent.yaml`, dependencies,
tests, and deployment metadata, plus a `dev.yaml` for local-development settings
such as service ports. You can copy any single agent directory out of this repo
and run it on its own.

The repo also contains starter templates in `templates/`. Those directories are
clean scaffold sources intended for `agentic create`.

The repository is structured as a **uv workspace** with a root `agent.yaml`
monorepo manifest so agents can share workspace tooling and still be discovered
as separate deployable apps. The monorepo layout is a convenience, not a
requirement for any individual example.

The examples depend on the SDK via git-based `uv` sources that point at the
`10gen/magenta-client-libraries` repository.

## Contributing

**New to this repository?** Start with the [**RUNBOOK.md**](RUNBOOK.md) — it covers:
- How to create a new agent from templates
- Modifying existing agents
- Testing and deployment
- Contribution workflow
- Troubleshooting and best practices

## Agents

| Agent | Description |
|-------|-------------|
| [`agents/atlas-admin-agent/`](agents/atlas-admin-agent/) | MongoDB Atlas Administration API agent with human-in-the-loop approval |
| [`agents/code-reviewer-agent/`](agents/code-reviewer-agent/) | DeepAgent code-review orchestrator with specialist review skills |
| [`agents/data-analyst-agent/`](agents/data-analyst-agent/) | Data analyst demo with MongoDB query and chart artifacts |
| [`agents/insurance-agent/`](agents/insurance-agent/) | Insurance assistant built on the Runner SDK |
| [`agents/insurance-agent-ts/`](agents/insurance-agent-ts/) | TypeScript insurance assistant (deep agent + long-term memory) |
| [`agents/mta-alerts-agent/`](agents/mta-alerts-agent/) | MTA subway service alerts agent |
| [`agents/recruiting-assistant-agent/`](agents/recruiting-assistant-agent/) | Recruiting assistant with cross-session memory and outreach review |
| [`agents/remote-mcp/`](agents/remote-mcp/) | Remote MCP examples for GitHub, Glean, and Sentry (Python) |
| [`agents/remote-mcp-ts/`](agents/remote-mcp-ts/) | Remote MCP examples for GitHub, Glean, and Sentry (TypeScript; GitHub-only default) |
| [`agents/simple-agent/`](agents/simple-agent/) | Web search assistant migrated onto `magenta_sdklanggraph` |
| [`agents/weather-agent/`](agents/weather-agent/) | Minimal LangGraph weather demo (single workspace) |

## Templates

| Template | Description |
|----------|-------------|
| [`templates/chatbot-client/`](templates/chatbot-client/) | Next.js chatbot UI that streams messages through the MongoDB Agentic Platform API |
| [`templates/hello-world-agent/`](templates/hello-world-agent/) | Minimal Daily inspiration assistant with date and memory tools |
| [`templates/insurance-agent/`](templates/insurance-agent/) | Clean starter version of the insurance assistant for the future `agentic create` flow |

## Getting Started

1. Install [uv](https://docs.astral.sh/uv/).
2. Run `uv sync` at the root to set up the workspace.
3. Use `uv run` inside any agent directory to run it.

To use an agent standalone (outside this monorepo), copy its directory, ensure
the `uv` sources in its `pyproject.toml` resolve correctly, and run `uv sync`
from within that directory.

## Interactive Demo Setup

To set up and run one of the existing demos, use the interactive wizard:

```bash
scripts/setup-agent
```

The wizard updates the `agentic` CLI on a best-effort basis, tries to pull the
latest version of this repo, lets you choose a demo from `agents/`, prompts for
an LLM provider key and optional provider base URL, and can reuse matching
values from existing `.env` files in other demos. For OpenAI-compatible setups,
it also prompts for `AZURE_OPENAI_API_VERSION` when the selected demo supports
that setting. It can enable memory by prompting for or reusing a
`VOYAGE_API_KEY`. It writes the selected demo's `.env`, updates the relevant
`agent.yaml` settings, and can start the demo with `agentic dev up`.

## Archive filtering (`.agenticignore`)

The root [`.agenticignore`](.agenticignore) controls which files `agentic build`
packs into the source archive it uploads. It uses standard `.gitignore` syntax
and `agentic init` seeds it with sensible defaults; edit it to fit your project.

Because this repo is a uv workspace, `agentic build` reads `.agenticignore` from
the **archive root — the workspace root** — not from an individual agent
directory, so a single file at the repo root applies to every agent here.
`.env` / `.env.*` files and the `.git` directory are always excluded and cannot
be re-included.

## Conventions

- The `agents/` directory contains one sub-directory per example agent.
- Each agent owns its own manifests, tests, and deployment metadata.
- The `templates/` directory contains starter scaffolds intended to be copied
  into new projects.
- The root `agent.yaml` lists every deployable agent workspace in the repo.
- The root `uv` workspace ties agents together for convenience but is not a
  hard requirement — agents are designed to work independently.
- Templates are starter scaffolds and are not part of the root `uv` workspace.
- Shared SDK code stays in `magenta-client-libraries/`; app-specific behavior
  belongs here.
