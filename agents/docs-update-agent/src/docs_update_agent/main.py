"""Docs Update Agent — Automated documentation updater on Magenta.

Uses a plain LangGraph ReAct agent with:
- **GitHub read + write tools**: ``list_recent_commits``, ``get_commit_diff``,
  ``get_file_content``, ``list_directory``, ``create_branch``,
  ``create_or_update_file``, ``create_pull_request``
- **System-prompt guidance**: default read-only reporting and explicit write/PR
  mode when requested

The agent creates PRs for documentation updates. It does NOT auto-merge —
a human reviewer approves and merges each PR.
"""

import asyncio
import logging
import os
import sys
from typing import Any, cast

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langgraph.prebuilt import create_react_agent
from magenta_sdklanggraph import App

from docs_update_agent.github_client import GitHubClient, from_env

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
load_dotenv()

DEFAULT_LOOKBACK_DAYS = 1
MAX_COMMITS_TO_ANALYZE = 3
MAX_SPECIALISTS_PER_RUN = 1
MAX_DOC_FILES_PER_SPECIALIST = 2


def _default_repo() -> str:
    return os.environ.get("TARGET_REPO", "10gen/agentic-platform")


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw_value = os.environ.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("Invalid %s=%r; falling back to %s", name, raw_value, default)
        return default
    if value < minimum:
        logger.warning("%s must be >= %s; falling back to %s", name, minimum, default)
        return default
    return value


def _default_lookback_days() -> int:
    return _env_int("LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = App(app_name="Docs Update Agent")


# ---------------------------------------------------------------------------
# GitHub tools — see github_client.py for HTTP plumbing. The @app.tool()
# wrappers below are the LLM-facing seam; they off-load the blocking httpx
# calls onto a worker thread so sibling subagents sharing the Tool Pod's
# asyncio loop can progress.
# ---------------------------------------------------------------------------


def _client() -> GitHubClient:
    """Build a fresh client on every tool call.

    Lazy construction picks up env-var changes between turns (e.g. tests
    that ``monkeypatch.setenv``) without requiring a module reload.
    """
    return from_env()


@app.tool()
async def list_recent_commits(
    repo: str, since_days: int | None = None, branch: str = "main"
) -> str:
    """List commits merged to a branch in the last N days for a GitHub repo.

    Args:
        repo: GitHub repo in owner/repo format (e.g., "10gen/agentic-platform").
        since_days: Number of days to look back. Defaults to LOOKBACK_DAYS.
        branch: Branch to inspect. Defaults to "main".

    Returns:
        List of commit SHAs, messages, authors, and dates.
    """
    lookback_days = since_days if since_days is not None else _default_lookback_days()
    return await asyncio.to_thread(_client().list_recent_commits, repo, lookback_days, branch)


@app.tool()
async def get_commit_diff(repo: str, sha: str) -> str:
    """Fetch the unified diff for a specific commit.

    Args:
        repo: GitHub repo in owner/repo format.
        sha: Full or short commit SHA.

    Returns:
        Unified diff text for the commit, or an error message.
    """
    return await asyncio.to_thread(_client().get_commit_diff, repo, sha)


@app.tool()
async def get_file_content(repo: str, path: str, ref: str = "main") -> str:
    """Fetch the content of a file from a GitHub repo at a given ref.

    Args:
        repo: GitHub repo in owner/repo format.
        path: Repo-relative file path (e.g. "docs/README.md").
        ref: Git ref (branch, tag, SHA). Defaults to "main".

    Returns:
        The file content as a string, or an error message.
    """
    return await asyncio.to_thread(_client().get_file_content, repo, path, ref)


@app.tool()
async def list_directory(repo: str, path: str = "", ref: str = "main") -> str:
    """List files and directories at a path in a GitHub repo.

    Args:
        repo: GitHub repo in owner/repo format.
        path: Directory path within the repo. Empty string for root.
        ref: Git ref. Defaults to "main".

    Returns:
        List of file/directory entries with names and types.
    """
    return await asyncio.to_thread(_client().list_directory, repo, path, ref)


@app.tool()
async def create_branch(repo: str, branch_name: str, from_ref: str = "main") -> str:
    """Create a new branch in a GitHub repo.

    Args:
        repo: GitHub repo in owner/repo format.
        branch_name: Name for the new branch.
        from_ref: Base ref to branch from. Defaults to "main".

    Returns:
        Success message with the new branch name, or an error.
    """
    return await asyncio.to_thread(_client().create_branch, repo, branch_name, from_ref)


@app.tool()
async def create_or_update_file(
    repo: str, path: str, content: str, branch: str, message: str
) -> str:
    """Create or update a file in a GitHub repo via the Contents API.

    Args:
        repo: GitHub repo in owner/repo format.
        path: File path in the repo.
        content: The new file content (plain text — will be base64-encoded automatically).
        branch: Branch to commit to.
        message: Commit message.

    Returns:
        Success message with commit SHA, or an error.
    """
    return await asyncio.to_thread(
        _client().create_or_update_file, repo, path, content, branch, message
    )


@app.tool()
async def create_pull_request(
    repo: str, title: str, body: str, head: str, base: str = "main", draft: bool = True
) -> str:
    """Create a draft pull request in a GitHub repo.

    The PR will be created as a draft and NOT merged — a human reviewer
    must approve and merge it.

    Args:
        repo: GitHub repo in owner/repo format.
        title: PR title.
        body: PR description in Markdown.
        head: Branch containing changes.
        base: Target branch. Defaults to "main".
        draft: Whether to open the PR as a draft. Defaults to True.

    Returns:
        The PR URL, or an error.
    """
    return await asyncio.to_thread(
        _client().create_pull_request,
        repo,
        title,
        body,
        head,
        base,
        draft=draft,
    )


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------
PARENT_SYSTEM_PROMPT_TEMPLATE = """You are a Documentation Update ORCHESTRATOR. You do NOT
write documentation yourself — you delegate to four specialist subagents and then
use their findings to create a PR with documentation updates.

## Default Repository

Unless the user specifies a different repository, operate on `{default_repo}`.
When calling any tool that takes a `repo` argument, use this default if the
user has not named a specific repo.

Unless the user specifies a different lookback window, use `{default_lookback_days}`
day(s), from the `LOOKBACK_DAYS` environment variable.

## Default Scope

Keep the default workflow focused and easy to review.

- Analyze at most `{max_commits_to_analyze}` commit(s) per run.
- Dispatch at most `{max_specialists_per_run}` specialist subagent(s) per run.
- Ask each specialist to read at most `{max_doc_files_per_specialist}` documentation
  file(s).
- If the requested scope is broader, summarize what you can determine from the
  focused review and ask the user to narrow the repo, branch, time window,
  commits, or documentation domain.

## Tools

### Read tools
- `list_recent_commits(repo, since_days, branch)` — list commits merged to a
  branch recently. Use FIRST to discover what changed. The branch defaults to
  `main`; pass a different branch if the target repo uses one.
- `get_commit_diff(repo, sha)` — unified diff for a specific commit.
- `get_file_content(repo, path, ref)` — read a file from the repo.
- `list_directory(repo, path, ref)` — list files/dirs at a path.

### Write tools
- `create_branch(repo, branch_name, from_ref)` — create a working branch.
- `create_or_update_file(repo, path, content, branch, message)` — push file changes.
- `create_pull_request(repo, title, body, head, base)` — open a PR for review.

### Dispatch tool
- `task(<subagent_name>, <description>)` — dispatches one specialist.

## The four specialists

1. `readme_updater` — README drift: component READMEs, setup instructions, usage
   examples, file structure sections
2. `api_docs_updater` — API documentation: REST endpoints, gRPC services, SDK
   methods, request/response schemas
3. `config_docs_updater` — configuration documentation: env vars, YAML/JSON config,
   feature flags, defaults
4. `architecture_updater` — architecture documentation: component diagrams, data
   flows, ADRs, dependency docs

Each specialist returns findings in a fixed format:
```
**File:** <path>
**Rule:** DOCS-RULE-ID-*
**What changed:** ...
**Current text:** ...
**Suggested update:** ...
```
or the literal text `No findings.` if nothing needs updating.

## Workflow (MANDATORY)

**Step 0 — Choose mode.**
- Default to FAST REPORT MODE. In this mode, identify likely documentation drift
  and return a concise report. Do NOT call write tools.
- Only use WRITE PR MODE if the user explicitly asks you to apply updates, push
  changes, create a branch, or open a PR. WRITE PR MODE still follows the
  default scope guidance above.

**Step 1 — Discover changes.** Call `list_recent_commits(repo, since_days, branch)`
to find recent commits. If no commits are found, respond to the user saying no
changes were detected and stop. Select at most `{max_commits_to_analyze}` commit(s)
to analyze further; prefer the newest non-merge commits that are most likely to
affect documentation. Skip commits whose first-line commit message starts with
`docs:` because those are documentation-only changes.

**Step 2 — Analyze changes.** Call `get_commit_diff(repo, sha)` for significant
commits (skip `docs:` commits and merge commits that only bump versions or lock
files). Determine which documentation domains are affected based on the files changed:

- README files, setup scripts, file structure → `readme_updater`
- Handler/router files, proto definitions, SDK interfaces → `api_docs_updater`
- Config structs, env var reads, YAML schemas → `config_docs_updater`
- New services, inter-service calls, new dependencies → `architecture_updater`

**Step 3 — Dispatch specialists.** Only dispatch specialists whose domain was
clearly affected by the diffs you analyzed in Step 2. Do NOT dispatch a
specialist speculatively — if no commits touched their domain, skip them.
Guidelines:
- No README/setup/file-structure changes → skip `readme_updater`
- No handler/router/proto/SDK changes → skip `api_docs_updater`
- No config struct/env-var/YAML/feature-flag changes → skip `config_docs_updater`
- No new services/inter-service calls/dependency changes → skip `architecture_updater`

Dispatch at most `{max_specialists_per_run}` specialist(s) by default. If multiple
domains are affected, pick the highest-impact domain and ask the user to run a
follow-up for the others.

Each task description MUST include:
- The repo (owner/repo format)
- A summary of which commits touched their domain
- The commit SHAs so they can fetch diffs themselves

Use this template for EACH task:
```
Review recent changes to {{repo}} for {{specialty}} documentation drift.

Commits to analyze:
{{list of sha + one-line message}}

Your workflow:
1. Use the skill content loaded for your specialist role.
2. For each commit SHA, call get_commit_diff("{{repo}}", "{{sha}}")
3. Call get_file_content / list_directory to read existing documentation
4. Apply your skill's rules to identify drift
5. Return findings in the fixed format, or `No findings.` if clean.

Constraints:
- Only call get_commit_diff for the SHAs listed above — do not discover additional commits.
- Read at most {max_doc_files_per_specialist} documentation files that are
  directly impacted by the changes.
- Report at most 5 findings. If you find more, report only the highest-priority ones.
```

**Step 4 — Aggregate.** Collect specialist findings. If ALL specialists returned
`No findings.`, tell the user no documentation updates are needed and stop.

In FAST REPORT MODE, stop here. Return a concise report with:
- commits analyzed
- specialist dispatched
- findings or `No findings`
- any skipped domains

In WRITE PR MODE, continue only if there are findings with concrete suggested
updates:
a. Call `create_branch(repo, "docs/update-<short-summary>")` to create a
   single working branch. The `<short-summary>` should be a brief slug
   describing which doc areas are affected (e.g., "docs/update-readme-api-config",
   "docs/update-arch-dependencies"). Do NOT use dates in the branch name.
b. For EACH finding with a suggested update, call `create_or_update_file`
   to push the updated documentation to that branch. Read the current file
   content first with `get_file_content` to apply the specialist's
   suggested changes in context.
c. After ALL files have been committed, call `create_pull_request` EXACTLY
   ONCE with:
   - Title: "docs: <concise description of updates>" summarizing which
     documentation areas were updated (e.g., "docs: update API reference
     and config env vars"). Do NOT use dates in the title.
   - Body: a short markdown PR description with exactly these sections:
     ```
     ## Summary
     - <1-2 concise bullets describing why docs were updated>

     ## Documentation changes
     - `<doc path or area>`: <what changed>. Source: <commit SHA>[, <source PR ref if available>]

     Authored by docs-update-agent.
     ```
     Keep the body concise. In `Documentation changes`, list each doc file or
     doc area that changed, and include the source commit SHA plus the source
     source PR reference from `list_recent_commits` when one is available (for
     example `10gen/agentic-platform#123` or a GitHub pull request URL).
     End the PR body with exactly: `Authored by docs-update-agent.`

**CRITICAL: call `create_pull_request` only once per run. Push all file
changes to the branch first, then open a single draft PR covering
everything.**

**Step 5 — Report.** Return the PR URL to the user and summarize what was updated.

## Rules

- Only update documentation files — NEVER modify source code.
- The PR is a draft for human review. Do NOT attempt to merge it.
- If no documentation updates are needed, say so clearly and do not create a PR.
- Do not create a branch, update files, or open a PR unless the user explicitly
  requested WRITE PR MODE.
- ONE branch, ONE PR per run — never call `create_pull_request` more than once.
- Include a "Changes detected" section in the PR body listing which commits
  triggered which doc updates.
- ALWAYS update existing documentation files in-place rather than creating new files.
  Only create a new documentation file when there is genuinely no existing file that
  covers the topic (e.g., a brand-new service with no README at all). Never name
  documentation files with dates (e.g., "updates-2025-01-15.md").
"""


def _parent_system_prompt() -> str:
    return PARENT_SYSTEM_PROMPT_TEMPLATE.format(
        default_repo=_default_repo(),
        default_lookback_days=_default_lookback_days(),
        max_commits_to_analyze=MAX_COMMITS_TO_ANALYZE,
        max_specialists_per_run=MAX_SPECIALISTS_PER_RUN,
        max_doc_files_per_specialist=MAX_DOC_FILES_PER_SPECIALIST,
    )


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------
def _build_llm() -> BaseChatModel:
    """Build an LLM from environment variables (multi-provider)."""
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    openai_base_url = os.environ.get("OPENAI_BASE_URL", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    cerebras_key = os.environ.get("CEREBRAS_API_KEY", "")

    if gemini_key:
        from langchain_google_genai import ChatGoogleGenerativeAI

        model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        logger.info("Using Gemini LLM: %s", model_name)
        gemini_cls: Any = ChatGoogleGenerativeAI
        return cast(
            BaseChatModel,
            gemini_cls(
                api_key=gemini_key,
                model=model_name,
                temperature=0,
                thinking_budget=0,
            ),
        )

    if openai_key:
        from langchain_openai import ChatOpenAI

        model_name = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
        logger.info("Using OpenAI LLM: %s", model_name)
        kwargs: dict[str, Any] = {
            "api_key": openai_key,
            "model": model_name,
            "temperature": 0,
        }
        if openai_base_url:
            if "grove-foundry" in openai_base_url:
                kwargs["base_url"] = openai_base_url.split("/v1")[0] + "/v1"
                kwargs["default_headers"] = {"api-key": openai_key}
            else:
                kwargs["base_url"] = openai_base_url.rstrip("/")
            if "/openai/deployments/" in openai_base_url.lower():
                api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
                kwargs["default_query"] = {"api-version": api_version}
                kwargs["default_headers"] = {"api-key": openai_key}
        openai_cls: Any = ChatOpenAI
        return cast(BaseChatModel, openai_cls(**kwargs))

    if anthropic_key:
        from langchain_anthropic import ChatAnthropic

        model_name = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        logger.info("Using Anthropic LLM: %s", model_name)
        kwargs: dict[str, Any] = {
            "api_key": anthropic_key,
            "model_name": model_name,
            "temperature": 0,
        }
        if anthropic_base_url:
            if "grove-foundry" in anthropic_base_url:
                kwargs["base_url"] = anthropic_base_url.split("/v1")[0].rstrip("/")
                kwargs["default_headers"] = {"api-key": anthropic_key}
            else:
                kwargs["base_url"] = anthropic_base_url.rstrip("/")
        anthropic_cls: Any = ChatAnthropic
        return cast(BaseChatModel, anthropic_cls(**kwargs))

    if cerebras_key:
        from langchain_cerebras import ChatCerebras

        model_name = os.environ.get("CEREBRAS_MODEL", "qwen-3-235b-a22b-instruct-2507")
        logger.info("Using Cerebras LLM: %s", model_name)
        cerebras_cls: Any = ChatCerebras
        return cast(
            BaseChatModel,
            cerebras_cls(
                api_key=cerebras_key,
                model=model_name,
                temperature=0,
            ),
        )

    raise ValueError(
        "No LLM API key configured. Set one of: "
        "GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, CEREBRAS_API_KEY"
    )

# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------
@app.entrypoint
def build_agent():
    """Build the docs-update agent graph."""
    parent_tools = app.get_tool_schemas()
    logger.info(
        "Default scope: lookback_days=%s, max_commits=%s, max_specialists=%s, "
        "max_doc_files_per_specialist=%s",
        _default_lookback_days(),
        MAX_COMMITS_TO_ANALYZE,
        MAX_SPECIALISTS_PER_RUN,
        MAX_DOC_FILES_PER_SPECIALIST,
    )

    graph = create_react_agent(
        model=_build_llm(),
        tools=parent_tools,
        prompt=_parent_system_prompt(),
    )
    return graph


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Starting Docs Update Agent (Magenta SDK)")
    logger.info("=" * 60)
    app.run()


if __name__ == "__main__":
    main()
