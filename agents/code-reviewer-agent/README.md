# Code Reviewer Agent

Eight-specialist code-reviewer orchestrator built on `App.deep_agent()`. The
parent delegates to focused subagents and synthesizes their prose findings
into a final Markdown review.

## What It Demonstrates

- **Full API surface**: every `app.deep_agent()` parameter visible in one call
- **Subagent fan-out**: eight specialists (`convention_reviewer`,
  `concurrency_reviewer`, `db_reviewer`, `error_handling_reviewer`,
  `security_reviewer`, `test_coverage_reviewer`, `documentation_reviewer`,
  `api_stability_reviewer`), each owning one SKILL.md
- **ToolPod tools**: `read_file`, `write_file`, `execute`, `ls`, `glob`, `grep`,
  `edit_file` routed through the secure OE → ToolPod pipeline
- **Custom tools**: `fetch_pr_diff` / `fetch_pr_files` fetch GitHub PR data via
  `@app.tool()` and become available to the parent through `get_tool_schemas()`
- **Middleware**: `LoggingMiddleware` showing the `middleware=` param
- **Secure routing**: all tool/LLM calls pass through OE for approval and logging
- **Skills**: eight SKILL.md files, one per specialist, progressively disclosed

For the skill-authoring guide (frontmatter rules, reserved tool names,
sandbox pattern), see the SDK's `atlasap-sdklanggraph` README in
[`10gen/atlasap-client-libraries`](https://github.com/10gen/atlasap-client-libraries/tree/main/packages/atlasap-sdklanggraph).

## Architecture

The parent is a pure orchestrator (`skills=None`). Each specialist:

1. Loads its one SKILL.md via the path listed in its Skills System metadata
2. Calls `fetch_pr_diff(url)` to load the unified diff
3. Returns prose findings (Phase A). Phase B will wire structured output via
   `response_format=FindingsList` + `ToolStrategy`

Specialists are registered up front, but they are not launched automatically.
The parent dispatches `task` calls after diff triage. For example, a docs-only
PR currently runs only `documentation_reviewer`; code changes fan out to the
relevant specialists and then get aggregated by the parent.

The parent regex-parses each specialist's response, aggregates into one
flat finding list, sorts by severity, and emits the final Markdown review.

## API Parameters Shown

| Parameter | Value | Notes |
|-----------|-------|-------|
| `llm` | `ChatOpenAI` | Wrapped in `SecureWrappedLLM` automatically |
| `tools` | `app.get_tool_schemas()` | Includes `fetch_pr_diff`, `fetch_pr_files` |
| `subagents` | Eight specialists | One skill each (see `_SPECIALIST_SPECS`) |
| `system_prompt` | Orchestrator instructions | Dispatches specialists, aggregates findings |
| `middleware` | `[LoggingMiddleware()]` | Logs each agent turn |
| `checkpointer` | _(omitted)_ | Uses MongoDB default |
| `store` | `None` | Explicit — no shared store |
| `skills` | `None` | Parent is a pure orchestrator |
| `backend` | _(omitted)_ | Uses `default Tool Pod backend` default |

## Sandbox Note

Specialists declare relative skill paths such as `skills/security-review`.
The SDK resolves those paths from the directory containing `agent.yaml`, and
the ToolPod treats that `skills/` directory as a read-only bundled resource.
`WORKSPACE_DIR` remains the writable scratch workspace and does not need to
point at the skills directory.

## Setup

```bash
cp env.example .env
# Required:
#   OPENAI_API_KEY or (OPENAI_BASE_URL + OPENAI_API_KEY for Grove/Azure APIM)
#   MONGODB_URI
#   GITHUB_TOKEN     — classic PAT with `repo` scope, SSO-authorized for the
#                      target org. Required for fetch_pr_diff / fetch_pr_files.
# Optional:
#   GITHUB_ALLOWED_REPOS — comma-separated `owner/repo` allowlist. When set,
#                          PR-fetch tools reject URLs outside the list.
#                          Recommended for shared/deployed instances.
#   OPENAI_MODEL         — defaults to `gpt-5.4`.
#   OPENAI_REASONING_EFFORT — defaults to `medium`.
```

## Run

From the atlasap repo root:

```bash
agentic dev up --workspace code-reviewer-agent
```

Standalone:
```bash
cd agents/code-reviewer-agent
uv run code-reviewer-agent
```

## Tests

```bash
cd agents/code-reviewer-agent
uv run pytest tests/                              # full suite
uv run pytest tests/test_build_agent.py           # graph construction + tool set
uv run pytest tests/test_pr_fetch.py              # GitHub PR ingestion tools
```
