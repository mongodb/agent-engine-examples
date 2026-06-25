"""Tests for the GitHub PR-ingestion path.

The previous version monkeypatched ``httpx.get`` with ad-hoc fake response
classes — tests that mostly verified their own scaffolding instead of the
actual contract. These tests drive the same code through ``httpx.MockTransport``
so every request flows through the real ``httpx.Client`` lifecycle (real
``Request`` objects, real headers, real status-code semantics) and we can
assert on what GitHub would actually see.

Layout:

* ``TestPullRequestRef`` — pure URL parsing.
* ``TestGitHubIntakeAccessGates`` — token / allowlist gates that short-circuit
  before any HTTP call. No mock transport involved.
* ``TestGitHubIntakeDiff`` / ``TestGitHubIntakeFiles`` — drive the intake with
  a recording mock transport and assert on requests + result strings.
* ``TestThinToolWrappers`` — sanity-check that the ``@app.tool()`` decorated
  helpers in ``main.py`` actually delegate to the intake (no duplicated logic).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import pytest

from code_reviewer_agent.github_intake import (
    MAX_DIFF_CHARS,
    MAX_FILE_CHARS,
    GitHubIntake,
    PullRequestRef,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default to a clean env so each test starts from a known state."""
    monkeypatch.setenv("RUNNER_MODE", "tool")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-tests")
    monkeypatch.setenv("MONGODB_URI", "mongodb://fake-for-tests:27017")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_ALLOWED_REPOS", raising=False)


# ---------------------------------------------------------------------------
# Recording mock transport
# ---------------------------------------------------------------------------


@dataclass
class _Recorder:
    """Capture every ``httpx.Request`` that flows through the transport."""

    requests: list[httpx.Request] = field(default_factory=list)

    def record(self, handler: Callable[[httpx.Request], httpx.Response]):
        def wrapped(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        return wrapped


def _intake_with(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    token: str | None = "tkn",
    allowed_repos: tuple[str, ...] = (),
) -> tuple[GitHubIntake, _Recorder]:
    """Build a GitHubIntake whose HTTP traffic is captured by *handler*."""
    recorder = _Recorder()
    transport = httpx.MockTransport(recorder.record(handler))
    client = httpx.Client(transport=transport, timeout=5.0)
    intake = GitHubIntake(token=token, allowed_repos=allowed_repos, client=client)
    return intake, recorder


def _meta_response(**overrides: object) -> dict[str, object]:
    """A minimal but realistic GitHub PR metadata payload."""
    payload: dict[str, object] = {
        "title": "T",
        "user": {"login": "u"},
        "state": "open",
        "changed_files": 1,
        "additions": 1,
        "deletions": 0,
        "body": "",
        "html_url": "https://github.com/o/r/pull/1",
        "head": {"sha": "0123456789abcdef0123"},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestPullRequestRef:
    @pytest.mark.parametrize(
        "url, expected",
        [
            (
                "https://github.com/10gen/agentic-platform/pull/1148",
                ("10gen", "agentic-platform", 1148),
            ),
            ("https://github.com/some-org/my-repo-name/pull/42", ("some-org", "my-repo-name", 42)),
            ("https://github.com/o/repo.name/pull/1", ("o", "repo.name", 1)),
            ("https://github.com/o/repo_name/pull/1", ("o", "repo_name", 1)),
            ("https://github.com/o/r/pull/1/files", ("o", "r", 1)),
        ],
    )
    def test_parses_well_formed_urls(self, url: str, expected: tuple[str, str, int]) -> None:
        ref = PullRequestRef.parse(url)
        assert ref is not None
        assert (ref.owner, ref.repo, ref.number) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/not-a-pr",
            # URL-encoded ``%`` in repo would bypass owner/repo allowlist checks.
            "https://github.com/owner/re%70o/pull/1",
            "https://github.com/owner/repo?x=1/pull/1",
            # Path-traversal in repo segment.
            "https://github.com/owner/../etc/pull/1",
            # Owner starting with a hyphen (invalid GitHub username).
            "https://github.com/-bad/repo/pull/1",
            # Whitespace inside owner.
            "https://github.com/own er/repo/pull/1",
            # Empty repo segment.
            "https://github.com/owner//pull/1",
        ],
    )
    def test_rejects_malformed_urls(self, url: str) -> None:
        assert PullRequestRef.parse(url) is None

    def test_slug_and_url_helpers(self) -> None:
        ref = PullRequestRef(owner="o", repo="r", number=42)
        assert ref.slug == "o/r"
        assert ref.metadata_url.endswith("/repos/o/r/pulls/42")
        assert ref.contents_url("src/x.py").endswith("/repos/o/r/contents/src/x.py")


# ---------------------------------------------------------------------------
# Access gates — short-circuit before any HTTP call
# ---------------------------------------------------------------------------


class TestGitHubIntakeAccessGates:
    """The token / allowlist gates must reject before issuing any request.

    A regression that lets these slip past would mean a missing token leaks
    a 401 from GitHub instead of a clear ``GITHUB_TOKEN`` message — and a
    bypassed allowlist would let a prompt-injected agent hit any repo the
    PAT can read.
    """

    def _explode(self, _request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP must not be reached when access is gated")

    def test_missing_token_is_rejected_before_http(self) -> None:
        ref = PullRequestRef(owner="o", repo="r", number=1)
        intake, recorder = _intake_with(self._explode, token=None)
        out = intake.diff(ref)
        assert out.startswith("ERROR:") and "GITHUB_TOKEN" in out
        assert recorder.requests == []

    def test_repo_outside_allowlist_is_rejected_before_http(self) -> None:
        ref = PullRequestRef(owner="other-org", repo="secrets", number=1)
        intake, recorder = _intake_with(self._explode, allowed_repos=("10gen/agentic-platform",))
        out = intake.diff(ref)
        assert "GITHUB_ALLOWED_REPOS" in out
        assert recorder.requests == []

    def test_repo_in_allowlist_passes_gate(self) -> None:
        """Allowlist match (case-insensitive, whitespace-tolerant) lets the call through."""
        ref = PullRequestRef(owner="10gen", repo="agentic-platform", number=1)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers["accept"] == "application/vnd.github+json":
                return httpx.Response(200, json=_meta_response())
            return httpx.Response(200, text="diff body")

        intake, recorder = _intake_with(
            handler, allowed_repos=("10GEN/Agentic-Platform ", " other/repo")
        )
        out = intake.diff(ref)
        assert "PR #1" in out
        assert len(recorder.requests) == 2

    def test_files_with_empty_paths_short_circuits(self) -> None:
        ref = PullRequestRef(owner="o", repo="r", number=1)
        intake, recorder = _intake_with(self._explode)
        out = intake.files(ref, [])
        assert "no paths" in out
        assert recorder.requests == []


# ---------------------------------------------------------------------------
# diff() — happy path, status-code branches, transport failure
# ---------------------------------------------------------------------------


class TestGitHubIntakeDiff:
    REF = PullRequestRef(owner="o", repo="r", number=1)

    def test_returns_metadata_and_diff_with_correct_headers(self) -> None:
        """Both requests go to the same URL with the contract Accept headers."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/repos/o/r/pulls/1"
            assert request.headers["authorization"] == "Bearer tkn"
            assert request.headers["user-agent"] == "atlasap-code-reviewer"
            if request.headers["accept"] == "application/vnd.github+json":
                return httpx.Response(200, json=_meta_response(title="Add feature"))
            assert request.headers["accept"] == "application/vnd.github.v3.diff"
            return httpx.Response(200, text="+++ a/foo\n+new line\n")

        intake, recorder = _intake_with(handler)
        out = intake.diff(self.REF)
        assert "PR #1 — Add feature" in out
        assert "+++ a/foo" in out
        assert len(recorder.requests) == 2

    @pytest.mark.parametrize(
        "status, marker",
        [
            (401, "401 — token invalid"),
            (404, "404"),
            (403, "403"),
        ],
    )
    def test_known_status_codes_short_circuit_with_clear_messages(
        self, status: int, marker: str
    ) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, headers={"x-ratelimit-remaining": "0"})

        intake, recorder = _intake_with(handler)
        out = intake.diff(self.REF)
        assert marker in out
        # 401/403/404 must short-circuit before the diff fetch issues a second
        # request — otherwise we'd burn rate-limit budget on a known bad token.
        assert len(recorder.requests) == 1

    def test_transport_failure_surfaces_as_error(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns")

        intake, _ = _intake_with(handler)
        out = intake.diff(self.REF)
        assert out.startswith("ERROR:")
        assert "HTTP fetch failed" in out

    def test_non_json_metadata_returns_clear_error(self) -> None:
        """A 200 with HTML (e.g. an upstream-cache maintenance page) must not crash."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>upstream maintenance</html>")

        intake, _ = _intake_with(handler)
        out = intake.diff(self.REF)
        assert out.startswith("ERROR:")
        assert "non-JSON" in out

    def test_oversize_diff_is_truncated(self) -> None:
        big = "x" * (MAX_DIFF_CHARS + 1_000)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers["accept"] == "application/vnd.github+json":
                return httpx.Response(200, json=_meta_response())
            return httpx.Response(200, text=big)

        intake, _ = _intake_with(handler)
        out = intake.diff(self.REF)
        assert "diff truncated" in out
        # Truncation cap (with the ``[... diff truncated ...]`` marker) keeps
        # the result strictly smaller than the raw oversize body.
        assert len(out) < len(big)


# ---------------------------------------------------------------------------
# files()
# ---------------------------------------------------------------------------


class TestGitHubIntakeFiles:
    REF = PullRequestRef(owner="o", repo="r", number=1)

    def test_metadata_then_one_request_per_path(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/pulls/1"):
                return httpx.Response(200, json=_meta_response())
            assert request.url.params["ref"] == "0123456789abcdef0123"
            return httpx.Response(200, text=f"contents of {request.url.path}")

        intake, recorder = _intake_with(handler)
        out = intake.files(self.REF, ["src/a.py", "src/b.py"])
        assert "Files at o/r@" in out
        assert "contents of /repos/o/r/contents/src/a.py" in out
        assert "contents of /repos/o/r/contents/src/b.py" in out
        # 1 metadata + 2 file fetches.
        assert len(recorder.requests) == 3

    def test_missing_head_sha_short_circuits(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"head": {}})

        intake, recorder = _intake_with(handler)
        out = intake.files(self.REF, ["a.py"])
        assert "head SHA" in out
        # No per-path request fired — we never resolved a SHA.
        assert len(recorder.requests) == 1

    def test_per_path_404_does_not_abort_the_batch(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/pulls/1"):
                return httpx.Response(200, json=_meta_response())
            if request.url.path.endswith("missing.py"):
                return httpx.Response(404)
            return httpx.Response(200, text="file body")

        intake, _ = _intake_with(handler)
        out = intake.files(self.REF, ["missing.py", "ok.py"])
        assert "not found at this SHA" in out
        assert "file body" in out

    @pytest.mark.parametrize(
        "path",
        [
            "/abs/path",
            "../escape.py",
            "src/../../escape.py",
            "src/foo?bar.py",
            "src/foo#frag.py",
            "src/foo%20bar.py",
        ],
    )
    def test_path_safety_rejects_before_request(self, path: str) -> None:
        """An attacker-controlled path must never reach the f-string in
        ``contents_url`` — that's how the allowlist gate is preserved.
        """

        def handler(_request: httpx.Request) -> httpx.Response:
            # Metadata request is fine; per-path request must not happen.
            if _request.url.path.endswith("/pulls/1"):
                return httpx.Response(200, json=_meta_response())
            raise AssertionError(f"unsafe path {path!r} reached HTTP")

        intake, recorder = _intake_with(handler)
        out = intake.files(self.REF, [path])
        assert "invalid path" in out
        # Only the metadata fetch ran.
        assert len(recorder.requests) == 1

    def test_oversize_file_is_truncated(self) -> None:
        big = "y" * (MAX_FILE_CHARS + 5_000)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/pulls/1"):
                return httpx.Response(200, json=_meta_response())
            return httpx.Response(200, text=big)

        intake, _ = _intake_with(handler)
        out = intake.files(self.REF, ["big.py"])
        assert "file truncated" in out


# ---------------------------------------------------------------------------
# Thin @app.tool() wrappers in main.py
# ---------------------------------------------------------------------------


class TestThinToolWrappers:
    """Verify the agent-facing ``fetch_pr_diff`` / ``fetch_pr_files`` tools
    are thin wrappers around :class:`GitHubIntake` — no new logic snuck in.
    The wrappers delegate to ``_diff_or_url_error`` / ``_files_or_url_error``
    which we exercise directly here, bypassing the ``asyncio.to_thread`` hop.
    """

    def test_diff_tool_rejects_non_github_url(self) -> None:
        from code_reviewer_agent import main

        out = main._diff_or_url_error("https://example.com/not-a-pr")
        assert out.startswith("ERROR:")
        assert "GitHub PR URL" in out

    def test_files_tool_rejects_non_github_url(self) -> None:
        from code_reviewer_agent import main

        out = main._files_or_url_error("https://example.com/not-a-pr", ["a.py"])
        assert out.startswith("ERROR:")

    def test_diff_tool_picks_up_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``_intake()`` must rebuild from env on every call so token
        rotation between turns does not require a module reload."""
        from code_reviewer_agent import main

        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        out = main._diff_or_url_error("https://github.com/o/r/pull/1")
        assert "GITHUB_TOKEN" in out

        monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "x/y")
        out = main._diff_or_url_error("https://github.com/o/r/pull/1")
        # Allowlist rejection takes precedence over the missing token —
        # but either way we should see an ``ERROR:`` line, never a stack
        # trace from the underlying HTTP layer.
        assert out.startswith("ERROR:")
