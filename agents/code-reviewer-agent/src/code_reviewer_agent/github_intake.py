"""GitHub pull-request intake — parse URLs, gate on the repo allowlist,
and fetch diff and file content.

This module is the single seam through which the code-reviewer agent talks
to the GitHub REST API. ``main.py`` builds a :class:`GitHubIntake` from
env vars at import; tests construct one directly with a custom
``httpx.Client`` (typically backed by ``httpx.MockTransport``) so they
exercise the real request lifecycle instead of monkeypatching the
``httpx.get`` shortcut.

Why a deep module: the previous shape interleaved URL parsing, allowlist
enforcement, token checks, HTTP plumbing, status-code branching, and
truncation across two near-duplicate ``_*_sync`` helpers in ``main.py``.
Pulling them behind one interface concentrates the contract (and its
test surface) in one place — and lets the tests assert on captured
``httpx.Request`` objects instead of synthesized fake response classes.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx

# Owner: GitHub usernames + org names are alphanumeric with single hyphens,
# max 39 chars and must not start with a hyphen. Repo: alphanumeric +
# hyphen + underscore + dot, max 100 chars. Tighter than ``[^/]+`` so URL-
# control bytes (``%``, ``?``, ``#``), traversal (``..``), and embedded
# whitespace are rejected at parse time — before any string interpolation
# in :meth:`GitHubIntake.files` produces a request to a different repo.
_PR_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100})/"
    r"pull/(?P<num>\d+)(?:/.*)?$"
)

# Soft caps. The 30k diff cap is tighter than the FilesystemMiddleware
# eviction threshold (~80k chars) because the parent fans out up to 8
# specialist task() calls in parallel, each carrying the diff in its
# description. 30k × 8 ≈ 240k stays inside the 600s API Gateway stream
# deadline plus context window comfortably; larger PRs should scope to
# one specialist explicitly.
MAX_DIFF_CHARS = 30_000
MAX_BODY_CHARS = 1_000
MAX_FILE_CHARS = 20_000

_HTTP_TIMEOUT = 30.0

T = TypeVar("T")


@dataclass(frozen=True)
class PullRequestRef:
    """A parsed reference to a single GitHub pull request."""

    owner: str
    repo: str
    number: int

    @classmethod
    def parse(cls, url: str) -> PullRequestRef | None:
        """Return a parsed ref, or ``None`` if *url* is not a valid PR URL."""
        match = _PR_URL_RE.match(url.strip())
        if match is None:
            return None
        return cls(
            owner=match.group("owner"),
            repo=match.group("repo"),
            number=int(match.group("num")),
        )

    @property
    def slug(self) -> str:
        """``owner/repo`` for allowlist comparisons and human-readable labels."""
        return f"{self.owner}/{self.repo}"

    @property
    def metadata_url(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}/pulls/{self.number}"

    def contents_url(self, path: str) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}/contents/{path}"


class GitHubIntake:
    """Fetch GitHub pull-request diffs and file content.

    The ``client`` parameter is the test seam: pass an ``httpx.Client``
    backed by ``httpx.MockTransport`` to drive the intake through the
    real request pipeline without hitting the network.
    """

    def __init__(
        self,
        *,
        token: str | None,
        allowed_repos: Iterable[str] = (),
        client: httpx.Client | None = None,
    ) -> None:
        self._token = token
        # Lower-cased frozen set so callers can't mutate the allowlist after
        # construction and ``slug`` lookups are case-insensitive (GitHub
        # treats owner/repo as case-insensitive on access).
        self._allowed_repos = frozenset(
            entry.strip().lower() for entry in allowed_repos if entry.strip()
        )
        # ``None`` means "open a fresh client per call". An injected client
        # is the test seam; production callers pass nothing.
        self._client = client

    # ------------------------------------------------------------------
    # Public API — one method per agent tool
    # ------------------------------------------------------------------

    def diff(self, ref: PullRequestRef) -> str:
        """Return PR metadata + unified diff formatted for the agent."""
        gate = self._check_access(ref)
        if gate is not None:
            return gate
        return self._with_client(lambda c: self._fetch_diff(c, ref))

    def files(self, ref: PullRequestRef, paths: Sequence[str]) -> str:
        """Return the contents of *paths* at the PR head SHA."""
        gate = self._check_access(ref)
        if gate is not None:
            return gate
        if not paths:
            return "ERROR: fetch_pr_files called with no paths."
        return self._with_client(lambda c: self._fetch_files(c, ref, paths))

    # ------------------------------------------------------------------
    # Access gates
    # ------------------------------------------------------------------

    def _check_access(self, ref: PullRequestRef) -> str | None:
        if self._allowed_repos and ref.slug.lower() not in self._allowed_repos:
            return (
                f"ERROR: {ref.slug} is not in GITHUB_ALLOWED_REPOS. "
                "Add it to the allowlist or review a different repository."
            )
        if not self._token:
            return (
                "ERROR: GITHUB_TOKEN env var not set. Add a classic PAT "
                "(with 'repo' scope, SSO-authorized for the org) to the "
                "agent's .env file and restart."
            )
        return None

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    def _with_client(self, op: Callable[[httpx.Client], T]) -> T:
        """Run *op* against an ``httpx.Client``.

        When a client was injected (tests) reuse it; otherwise open a
        short-lived one so production callers don't leak connections.
        """
        if self._client is not None:
            return op(self._client)
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            return op(client)

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "magenta-code-reviewer",
        }

    # ------------------------------------------------------------------
    # Fetch implementations
    # ------------------------------------------------------------------

    def _fetch_diff(self, client: httpx.Client, ref: PullRequestRef) -> str:
        try:
            meta_resp = client.get(
                ref.metadata_url,
                headers={**self._auth_headers, "Accept": "application/vnd.github+json"},
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            return f"ERROR: HTTP fetch failed: {exc}"

        gate = _classify_metadata_status(ref, meta_resp)
        if gate is not None:
            return gate

        try:
            meta_resp.raise_for_status()
            payload = meta_resp.json()
        except httpx.HTTPError as exc:
            return f"ERROR: HTTP fetch failed: {exc}"
        except json.JSONDecodeError as exc:
            return f"ERROR: GitHub returned non-JSON for PR metadata: {exc}"

        try:
            diff_resp = client.get(
                ref.metadata_url,
                headers={**self._auth_headers, "Accept": "application/vnd.github.v3.diff"},
                timeout=_HTTP_TIMEOUT,
            )
            diff_resp.raise_for_status()
        except httpx.HTTPError as exc:
            return f"ERROR: HTTP fetch failed: {exc}"

        return _format_diff_report(ref, payload, diff_resp.text)

    def _fetch_files(self, client: httpx.Client, ref: PullRequestRef, paths: Sequence[str]) -> str:
        try:
            meta_resp = client.get(
                ref.metadata_url,
                headers={**self._auth_headers, "Accept": "application/vnd.github+json"},
                timeout=_HTTP_TIMEOUT,
            )
            meta_resp.raise_for_status()
            head_sha = meta_resp.json().get("head", {}).get("sha")
        except httpx.HTTPError as exc:
            return f"ERROR: failed to resolve head SHA: {exc}"
        except json.JSONDecodeError as exc:
            return f"ERROR: GitHub returned non-JSON for PR metadata: {exc}"
        if not head_sha:
            return "ERROR: could not resolve PR head SHA."

        parts: list[str] = [f"Files at {ref.slug}@{head_sha[:12]}:"]
        for path in paths:
            parts.append(self._fetch_one_file(client, ref, head_sha, path))
        return "\n".join(parts)

    def _fetch_one_file(
        self,
        client: httpx.Client,
        ref: PullRequestRef,
        head_sha: str,
        path: str,
    ) -> str:
        if _looks_unsafe(path):
            return (
                f"\n=== {path} ===\nERROR: invalid path "
                "(traversal or URL-control characters not allowed)"
            )
        try:
            resp = client.get(
                ref.contents_url(path),
                headers={**self._auth_headers, "Accept": "application/vnd.github.raw"},
                params={"ref": head_sha},
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            return f"\n=== {path} ===\nERROR: {exc}"
        if resp.status_code == 404:
            return f"\n=== {path} ===\nERROR: not found at this SHA"
        if resp.status_code != 200:
            return f"\n=== {path} ===\nERROR: HTTP {resp.status_code}"
        body = resp.text
        truncated = ""
        if len(body) > MAX_FILE_CHARS:
            body = body[:MAX_FILE_CHARS]
            truncated = f"\n[...file truncated at {MAX_FILE_CHARS:,} chars...]"
        return f"\n=== {path} ===\n{body}{truncated}"


# ---------------------------------------------------------------------------
# Helpers (private, but module-level so they can be unit-tested directly)
# ---------------------------------------------------------------------------


def _classify_metadata_status(ref: PullRequestRef, resp: httpx.Response) -> str | None:
    """Map known status codes to user-facing ERROR strings.

    ``None`` means "let the caller proceed with the response body".
    """
    if resp.status_code == 401:
        return "ERROR: GitHub 401 — token invalid or expired."
    if resp.status_code == 404:
        return (
            f"ERROR: GitHub 404 for {ref.slug}#{ref.number}. "
            "Either the PR does not exist, the token has no access, "
            "or the token is not SSO-authorized for this org. "
            "Visit your PAT's settings page and click 'Configure SSO' "
            f"→ authorize for {ref.owner}."
        )
    if resp.status_code == 403:
        remaining = resp.headers.get("x-ratelimit-remaining", "?")
        return f"ERROR: GitHub 403 — {remaining} calls remaining this window."
    return None


def _looks_unsafe(path: str) -> bool:
    """True if *path* could escape the ``{owner}/{repo}/contents/`` prefix."""
    if path.startswith("/"):
        return True
    if ".." in path.split("/"):
        return True
    return any(c in path for c in "?#%")


def _format_diff_report(ref: PullRequestRef, meta: dict[str, Any], diff_text: str) -> str:
    body = (meta.get("body") or "").strip()
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n[...body truncated...]"

    diff_truncated = ""
    if len(diff_text) > MAX_DIFF_CHARS:
        diff_text = diff_text[:MAX_DIFF_CHARS]
        diff_truncated = (
            f"\n\n[... diff truncated at {MAX_DIFF_CHARS:,} chars — "
            "the full diff is larger; consider reviewing file-by-file.]"
        )

    return (
        f"PR #{ref.number} — {meta.get('title', '(no title)')}\n"
        f"Author: @{meta.get('user', {}).get('login', '?')}\n"
        f"State: {meta.get('state', '?')}  "
        f"Files: {meta.get('changed_files', '?')}  "
        f"Lines: +{meta.get('additions', '?')} / -{meta.get('deletions', '?')}\n"
        f"URL: {meta.get('html_url', '')}\n"
        f"\n--- PR description ---\n{body or '(empty)'}\n"
        f"\n--- Unified diff ---\n{diff_text}{diff_truncated}"
    )


# ---------------------------------------------------------------------------
# Module-level factory used by main.py
# ---------------------------------------------------------------------------


def from_env() -> GitHubIntake:
    """Build a :class:`GitHubIntake` from process environment variables.

    Reads ``GITHUB_TOKEN`` and ``GITHUB_ALLOWED_REPOS`` lazily on every
    call so tests that ``monkeypatch.setenv`` after import see the new
    values without reloading the module.
    """
    raw = os.environ.get("GITHUB_ALLOWED_REPOS", "").strip()
    allowed = raw.split(",") if raw else ()
    return GitHubIntake(token=os.environ.get("GITHUB_TOKEN"), allowed_repos=allowed)
