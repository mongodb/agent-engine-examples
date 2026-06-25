# Docs Update Agent — Automated Documentation Updater

An agent that monitors a GitHub repository for recent code changes and automatically
creates PRs to update stale documentation. Built with `App.deep_agent()` using four
domain-specific specialist subagents.

## What It Does

Analyzes recent commits to a target repository (default: `10gen/agentic-platform`),
identifies documentation that has drifted from the code, and opens a PR with the
necessary updates. The PR requires human approval before merging.

### Specialists

| Specialist | Domain | Skill |
|-----------|--------|-------|
| `readme_updater` | Component READMEs, setup instructions, usage examples | `skills/readme-updater` |
| `api_docs_updater` | REST endpoints, gRPC services, SDK methods | `skills/api-docs` |
| `config_docs_updater` | Env vars, YAML/JSON config, feature flags | `skills/config-docs` |
| `architecture_updater` | Component diagrams, data flows, ADRs | `skills/architecture-docs` |

## File Structure

```
agents/docs-update-agent/
├── src/docs_update_agent/
│   ├── __init__.py           # Package docstring
│   ├── main.py               # App + @app.entrypoint deep agent definition
│   └── github_client.py      # GitHub REST API client (httpx)
├── skills/
│   ├── readme-updater/SKILL.md
│   ├── api-docs/SKILL.md
│   ├── config-docs/SKILL.md
│   └── architecture-docs/SKILL.md
├── tests/
│   └── test_build_agent.py   # Graph compilation + skills wiring tests
├── pyproject.toml            # Dependencies & build config
├── agent.yaml                # Agent descriptor for magenta
├── dev.yaml                  # Local-dev service ports (playground pinned to 3000)
├── env.example               # Environment variable template
└── README.md                 # This file
```

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- One LLM API key (OpenAI, Anthropic, Gemini, or Cerebras)
- GitHub PAT with `repo` scope (SSO-authorized for 10gen)
- MongoDB instance (for checkpointing)

## Setup

```bash
cd agents/docs-update-agent

# Create .env from template
cp env.example .env
# Edit .env — set GITHUB_TOKEN and at least one LLM API key

# Install dependencies
uv sync
```

## Running Locally

### With agentic CLI (recommended)

```bash
agentic dev up
```

Then open the playground at `http://localhost:3000` and send:

> Check 10gen/agentic-platform for code changes in the last day and update any stale documentation

### Standalone

```bash
uv run docs-update-agent
```

## Testing

```bash
uv run pytest tests/ -v
```

## Architecture

The agent uses `App.deep_agent()` with an orchestrator + specialist pattern:

```
User trigger → Orchestrator
  ├─ list_recent_commits() → analyze changes
  ├─ get_commit_diff() × N → understand what changed
  ├─ task(readme_updater, ...) ─┐
  ├─ task(api_docs_updater, ...) ├─ parallel specialist dispatch
  ├─ task(config_docs_updater, ...) │
  └─ task(architecture_updater, ...) ┘
  │
  ├─ Aggregate findings
  ├─ create_branch() → create working branch
  ├─ create_or_update_file() × K → push doc updates
  └─ create_pull_request() → open PR for human review
```

### Daily Scheduling

The agent is designed to be triggered daily by an external scheduler. It does not
run on a built-in cron — instead, call it via the platform's invoke API or the
playground. Each invocation looks at the last N days of changes (configurable via
`LOOKBACK_DAYS` env var).

### Human-in-the-Loop

The agent creates a PR but does **not** auto-merge. A human reviewer must approve
and merge each documentation update PR.
