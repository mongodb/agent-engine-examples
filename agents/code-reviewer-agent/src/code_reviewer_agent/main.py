"""Code Reviewer Agent - Code review assistant on Magenta.

Demonstrates the full ``App.deep_agent()`` API surface with:
- **Built-in filesystem + shell tools**: ``write_file``, ``read_file``,
  ``edit_file``, ``ls``, ``glob``, ``grep``, ``execute`` — all routed
  through ``MagentaToolPodBackend`` (SecureToolWrapper -> OE -> Tool Pod)
- **Middleware**: ``LoggingMiddleware`` showing the ``middleware=`` param
- **Skills**: eight SKILL.md files loaded via the canonical ``skills=``
  kwarg, progressively disclosed by deepagents' skill-dispatch
  middleware on first relevant turn. Relative paths resolve from the
  directory containing ``agent.yaml``.
- **Full param visibility**: every ``app.deep_agent()`` parameter is set
  explicitly so the shape of the API is obvious

All filesystem/shell tool calls are sandboxed to the Tool Pod's workspace
directory and audited via the Orchestration Engine.
"""

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable

from dotenv import load_dotenv
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_openai import ChatOpenAI
from magenta_sdklanggraph import App

from code_reviewer_agent.github_intake import GitHubIntake, PullRequestRef, from_env

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

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = App(app_name="Code Reviewer Agent")


# ---------------------------------------------------------------------------
# GitHub PR-fetch tools — see github_intake.py for parsing, allowlist,
# HTTP plumbing. The @app.tool() wrappers below are the LLM-facing seam;
# they off-load the blocking httpx calls onto a worker thread so sibling
# subagents sharing the Tool Pod's asyncio loop can progress.
# ---------------------------------------------------------------------------


def _intake() -> GitHubIntake:
    """Build a fresh intake on every tool call.

    Lazy construction picks up env-var changes between turns (e.g. tests
    that ``monkeypatch.setenv``) without requiring a module reload.
    """
    return from_env()


def _diff_or_url_error(pr_url: str) -> str:
    ref = PullRequestRef.parse(pr_url)
    if ref is None:
        return (
            f"ERROR: not a GitHub PR URL: {pr_url!r}. "
            "Expected https://github.com/{owner}/{repo}/pull/{number}"
        )
    return _intake().diff(ref)


def _files_or_url_error(pr_url: str, paths: list[str]) -> str:
    ref = PullRequestRef.parse(pr_url)
    if ref is None:
        return f"ERROR: not a GitHub PR URL: {pr_url!r}"
    return _intake().files(ref, paths)


@app.tool()
async def fetch_pr_diff(pr_url: str) -> str:
    """Fetch a GitHub pull request's metadata and unified diff.

    Args:
        pr_url: Full PR URL like
            ``https://github.com/10gen/agentic-platform/pull/123``.

    Returns:
        A single string with PR title, author, +/- stats, description,
        and the unified diff. Large diffs are truncated with a clear
        marker so the caller knows to scope down.

    Auth: Requires ``GITHUB_TOKEN`` env var (classic PAT with ``repo``
    scope, SSO-authorized for the target org). Failures surface as
    clear ``ERROR:`` lines rather than silent empty returns.
    """
    return await asyncio.to_thread(_diff_or_url_error, pr_url)


@app.tool()
async def fetch_pr_files(pr_url: str, paths: list[str]) -> str:
    """Fetch full file content at the PR's head SHA for one or more paths.

    Use this when a specialist needs full file context (surrounding
    unchanged code, imports, type definitions) that the unified diff
    does not show.

    Args:
        pr_url: Same PR URL format accepted by ``fetch_pr_diff``.
        paths: Repo-relative file paths, e.g. ``["src/foo.go", "tests/foo_test.go"]``.

    Returns:
        Concatenated file contents with ``=== <path> ===`` separators,
        or an ``ERROR:`` line if the fetch failed.
    """
    return await asyncio.to_thread(_files_or_url_error, pr_url, paths)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------
PARENT_SYSTEM_PROMPT = """You are a code review ORCHESTRATOR, modeled on the
`agentic-review` skill in the agentic-platform repo. You do NOT review code
yourself — you delegate to eight specialist subagents and synthesize their
structured findings into a final review.

## Tools

- `fetch_pr_diff(pr_url)` — unified diff + PR metadata. Use FIRST when the
  user gives a GitHub PR URL.
- `fetch_pr_files(pr_url, paths)` — full file content at the PR head SHA,
  for files where surrounding context matters (Convention, API Stability).
  Optional; use only if a specialist's skill demands full-file context.
- `task(<subagent_name>, <description>)` — dispatches one specialist.

## The eight specialists

1. `convention_reviewer` — naming, dead code, duplication, local conventions
2. `concurrency_reviewer` — goroutine/task leaks, races, unclosed resources
3. `db_reviewer` — MongoDB anti-patterns, index coverage, pagination
4. `error_handling_reviewer` — silent failures, missing guards, flaky patterns
5. `security_reviewer` — injection, SSRF, tenant scope, secrets
6. `test_coverage_reviewer` — untested logic, weak assertions, flakiness
7. `documentation_reviewer` — repo map drift, missing ADRs, undocumented env vars
8. `api_stability_reviewer` — contract breaks, caller assumption violations

Each specialist returns prose findings in a fixed format:
```
**File:** path:line
**Rule:** REVIEW-RULE-ID-*
**Severity:** CRITICAL | MUST FIX | SHOULD FIX | NIT
**Title:** ...
**Detail:** ...
**Suggestion:** ... (optional)
```
or the literal text `No findings.` if the specialist found nothing.
Parse these blocks out of each `task` ToolMessage response when
aggregating into the final review.

## Workflow (MANDATORY)

**Step 1 — Fetch.** If the user's message contains a GitHub PR URL matching
`github.com/<owner>/<repo>/pull/<n>`, your FIRST action is
`fetch_pr_diff(url)`. You MUST call this tool. Do NOT apologize, do NOT
claim access restrictions, do NOT ask the user for the diff. If the tool
returns a line starting with `ERROR:`, relay that exact error text and
stop. Otherwise proceed to step 2.

CRITICAL: after `fetch_pr_diff` returns without an `ERROR:` line, the
fetch SUCCEEDED — you have the diff. You MUST NOT respond to the user
with phrases like "it seems there was an issue", "access issue", or
"SSO-authorized" — those are hallucinated excuses that the tool did
not actually emit. A successful fetch looks like `PR #N — <title>...`
followed by a `--- Unified diff ---` section. If you see that, you
have the data and MUST proceed to dispatch `task` calls.

If the user pastes code inline, skip step 1 and use the inline code as the
review target.

**Step 2 — Triage by domain.** From the `diff --git a/<path>` lines in the
fetched diff, determine which domains are touched. Set these flags:

- `has_typescript` — any file under `agentic-platform-ui/`, or any `.ts`/`.tsx`
- `has_python` — any `.py` under `runner/` or `client-libraries/`
- `has_go` — any `.go` under `internal/` or `executor-control-plane/` or `agentic-operator/`
- `has_k8s` — any file under `*/k8s/`, `*/manifest/`, or `.infra/`
- `has_docs_only` — every changed file is `*.md` or documentation

Then decide which specialists to dispatch:

| Specialist | Skip when |
|-----------|-----------|
| convention_reviewer | docs-only PR |
| concurrency_reviewer | docs-only, or only k8s/config files changed |
| db_reviewer | Only TypeScript, docs, or k8s files changed |
| error_handling_reviewer | docs-only, or only k8s manifests |
| security_reviewer | docs-only |
| test_coverage_reviewer | docs-only, or only k8s manifests |
| documentation_reviewer | ALWAYS run |
| api_stability_reviewer | docs-only |

**Step 3 — Dispatch.** Dispatch `task` calls in parallel (one tool-use
message containing multiple `task` calls) to every specialist you chose
in step 2.

Each task description MUST be short — no inline diff. The subagent
will fetch the diff itself by calling `fetch_pr_diff(url)`. Inlining
the full diff in 8 parallel task descriptions would exceed the
model's single-turn output token budget.

Use this template for EACH task:

```
Review this PR for <specialty-of-the-specialist>.

PR: <title>
URL: <url>

Your workflow:
1. read_file(...) the SKILL.md path listed in your Skills System metadata
   to load your rules
2. fetch_pr_diff("<url>") to get the unified diff
3. Apply your skill's rules to the diff; focus only on added/changed lines
4. Return findings in the fixed format (File/Rule/Severity/Title/
   Detail/Suggestion), or `No findings.` if clean.
```

Keep your task descriptions ~200 tokens each. Do NOT paste the diff.

**Step 4 — Aggregate.** Parse each specialist's ToolMessage response by
splitting on the `---` separator between finding blocks, then extract the
six labeled fields (File, Rule, Severity, Title, Detail, Suggestion) from
each block. A response of exactly `No findings.` means an empty list.
Collect every finding from every specialist into one flat list. Do NOT
drop findings, do NOT re-score them — Phase A trusts each specialist's
stated severity.

**Step 5 — Emit the review.** Produce a Markdown response with this
exact structure:

```
## Code Review

**PR:** [title from fetch_pr_diff]
**URL:** [URL from fetch_pr_diff]
**Domains:** [e.g. Go, TypeScript UI]
**Specialists run:** [list names of dispatched specialists]

### Summary
[2-3 sentences: what the change does, overall quality assessment]

### Findings

#### [file:line_range] — [SEVERITY]: [title]
**Agent:** [specialist name]  **Rule:** `[REVIEW-RULE-ID-...]`
[detail]

**Suggestion:** [suggestion, if provided]

---

[repeat, sorted: CRITICAL → MUST FIX → SHOULD FIX → NIT]

### Verdict
- **Findings:** X critical, Y must-fix, Z should-fix, W nits
- **Recommendation:** APPROVE / REQUEST CHANGES / APPROVE WITH COMMENTS
- **Key risks:** [one sentence per CRITICAL or MUST FIX, or "None"]
```

If all specialists return empty lists, say so and recommend APPROVE.

Do NOT call `read_file` on SKILL.md paths yourself. The specialists have
their own skills; you do not. Your answer is built from the structured
responses you collect from `task` calls.
"""


_SPECIALIST_PROMPT_TEMPLATE = """You are the {domain} specialist subagent
for the Code Reviewer running on Magenta. Your entire job is to review a
unified diff against your one loaded skill and return structured findings.

## Skill

You have exactly one skill named `{skill_dir}`. Its SKILL.md body contains
the specific review patterns you must check, each identified by a
`REVIEW-RULE-ID-{tag}-*` token.

## Mandatory workflow

1. Call `read_file(...)` on the SKILL.md path listed in your Skills System
   metadata to load your review rules into context. Do NOT skip this —
   your rule citations must come from the skill body, not from training
   knowledge.
2. Read the unified diff provided in the task description. Focus only
   on lines that start with `+` (added) or ` ` (unchanged context on a
   modified hunk). NEVER flag lines that are unchanged (`-` lines from
   other hunks, or lines outside the diff hunks entirely).

   If the task description contains a GitHub PR URL (not an inline
   diff), call `fetch_pr_diff(url)` yourself to load the diff. This is
   expected — the parent orchestrator passes URLs to specialists to
   keep individual tool-call payloads small. Do NOT wait for the
   parent to provide the diff inline.
3. For each issue you find that matches a rule in your skill, write
   a finding block in this exact format:

   ```
   **File:** <repo-relative-path>:<line_number_or_range>
   **Rule:** REVIEW-RULE-ID-{tag}-<name>
   **Severity:** <CRITICAL | MUST FIX | SHOULD FIX | NIT>
   **Title:** <short title>
   **Detail:** <what is wrong and why it matters>
   **Suggestion:** <optional proposed fix, or omit this line>
   ```

   Separate multiple findings with `---` on its own line.

4. If you found no issues, return exactly: `No findings.`

## Rules

- ONLY cite rule IDs that appear in your SKILL.md. Never invent new ones.
- ONLY report findings you can anchor to a specific file and line range.
- Do NOT report issues outside your specialty ({domain}) — other
  specialists cover those. Stay in your lane.
- Do NOT report pre-existing issues on lines not touched by the diff.
- Do NOT report issues a linter/typechecker/compiler would catch — CI
  handles those.
- Do NOT report intentional behavioral changes that match the stated
  purpose of the PR.
- If the diff touches no files relevant to your specialty, return an
  empty findings list.
"""


def _specialist_prompt(*, domain: str, skill_dir: str, tag: str) -> str:
    return _SPECIALIST_PROMPT_TEMPLATE.format(domain=domain, skill_dir=skill_dir, tag=tag)


# ---------------------------------------------------------------------------
# Middleware (demonstrates the middleware= param)
# ---------------------------------------------------------------------------
class CodeReviewerLoggingMiddleware(AgentMiddleware):
    """Per-call logger for the code-reviewer agent's model invocations.

    Named with the agent prefix to make scope obvious — this is example-app
    glue, not a platform feature. If a future platform-level middleware
    grows the same shape it should live in ``magenta_sdklanggraph.middleware``
    so every deep agent can reuse it; until then, this stays here.

    Demonstrates the ``middleware=`` parameter for ``app.deep_agent()`` —
    subclassing :class:`AgentMiddleware` and implementing
    ``awrap_model_call`` is enough to observe each LLM invocation; richer
    implementations can enforce policies or transform requests/responses.
    """

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        logger.info("Model call started")
        response = await handler(request)
        logger.info("Model call complete")
        return response


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------
def _build_llm() -> ChatOpenAI:
    """Build a ChatOpenAI that works with both vanilla OpenAI and Grove.

    Grove (MongoDB's Azure APIM proxy for OpenAI) requires the key as an
    ``api-key`` header instead of ``Authorization: Bearer``. We detect it
    by ``OPENAI_BASE_URL`` containing ``grove-foundry``.
    """
    kwargs: dict[str, object] = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-5.4"),
        "temperature": 0,
        "reasoning_effort": os.environ.get("OPENAI_REASONING_EFFORT", "medium").strip() or "medium",
        # Grove's Azure APIM occasionally peer-closes a chunked response
        # mid-stream ("incomplete chunked read"). Bump retries + request
        # timeout so a single bad frame doesn't kill the whole agent turn.
        "max_retries": 5,
        "timeout": 60,
    }
    base_url = os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    if base_url:
        if "grove-foundry" in base_url:
            kwargs["base_url"] = base_url.split("/v1")[0] + "/v1"
            if api_key:
                kwargs["default_headers"] = {"api-key": api_key}
        else:
            kwargs["base_url"] = base_url.rstrip("/")
    return ChatOpenAI(**kwargs)


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------
@app.entrypoint
def build_agent():
    """Build the deep agent graph as an agentic-review-style orchestrator.

    Eight specialist subagents, each loading one focused SKILL.md, return
    prose findings; the parent synthesizes them into a final Markdown review.
    """
    # Expose all @app.tool()-registered tools to the parent orchestrator.
    # ``app.deep_agent(tools=None)`` does NOT auto-merge registered tools;
    # without this the LLM cannot see fetch_pr_diff/fetch_pr_files. Subagents
    # inherit this set unless they declare ``tools=`` explicitly.
    parent_tools = app.get_tool_schemas()

    graph = app.deep_agent(
        llm=_build_llm(),
        tools=parent_tools,
        subagents=_build_specialists(),
        system_prompt=PARENT_SYSTEM_PROMPT,
        middleware=[CodeReviewerLoggingMiddleware()],
        store=None,
        # Parent has NO skills — it's a pure orchestrator. All skill
        # content lives in the specialists above.
        skills=None,
    )
    return graph


# Eight specialists. Each owns exactly one skill (skills do NOT inherit across
# the parent/subagent boundary per deepagents' SubAgentMiddleware). Specialists
# return prose findings that the parent regex-parses.
_SPECIALIST_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "convention_reviewer",
        "convention and code quality",
        "convention-quality",
        "CQ",
        "Reviews for language conventions, naming, dead code, duplication, and "
        "local style conformance. Cites REVIEW-RULE-ID-CQ-* tokens from the "
        "convention-quality skill.",
    ),
    (
        "concurrency_reviewer",
        "resource leaks and concurrency",
        "concurrency-leaks",
        "CC",
        "Reviews for resource leaks and concurrency bugs — goroutine/task leaks, "
        "races, unclosed cursors, stale useEffect closures. Cites "
        "REVIEW-RULE-ID-CC-* tokens from the concurrency-leaks skill.",
    ),
    (
        "db_reviewer",
        "database and query patterns",
        "db-patterns",
        "DB",
        "Reviews for MongoDB anti-patterns — unbounded queries, N+1, missing "
        "indexes, unbounded document growth, skip/offset pagination. Cites "
        "REVIEW-RULE-ID-DB-* from the db-patterns skill.",
    ),
    (
        "error_handling_reviewer",
        "error handling and reliability",
        "error-handling",
        "EH",
        "Reviews for silent failures, missing guards, continued execution "
        "after errors, unhandled rejections. Cites REVIEW-RULE-ID-EH-* tokens "
        "from the error-handling skill.",
    ),
    (
        "security_reviewer",
        "security",
        "security-review",
        "SEC",
        "Reviews for security vulnerabilities — injection, SSRF, tenant scope, "
        "unbounded bodies, secrets, token leakage. Cites REVIEW-RULE-ID-SEC-* "
        "from the security-review skill.",
    ),
    (
        "test_coverage_reviewer",
        "test coverage",
        "test-coverage",
        "TC",
        "Reviews test quality — untested logic, weak assertions, flakiness "
        "patterns, missing coverage for new API surfaces. Cites "
        "REVIEW-RULE-ID-TC-* from the test-coverage skill.",
    ),
    (
        "documentation_reviewer",
        "documentation",
        "documentation",
        "DOC",
        "Reviews for documentation drift — repo map, component READMEs, new "
        "env vars, missing ADRs, public API doc comments. Cites "
        "REVIEW-RULE-ID-DOC-* from the documentation skill.",
    ),
    (
        "api_stability_reviewer",
        "API stability",
        "api-stability",
        "API",
        "Reviews for contract-breaking changes and caller-assumption "
        "violations — REST/gRPC/proto wire format, exported signature changes. "
        "Cites REVIEW-RULE-ID-API-* from the api-stability skill.",
    ),
)


def _build_specialists() -> list[dict]:
    return [
        {
            "name": name,
            "description": description,
            "system_prompt": _specialist_prompt(domain=domain, skill_dir=skill_dir, tag=tag),
            "skills": [f"skills/{skill_dir}"],
        }
        for name, domain, skill_dir, tag, description in _SPECIALIST_SPECS
    ]


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Starting Code Reviewer Agent (Magenta SDK)")
    logger.info("=" * 60)
    app.run()


if __name__ == "__main__":
    main()
