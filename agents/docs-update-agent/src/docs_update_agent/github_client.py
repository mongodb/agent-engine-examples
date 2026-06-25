"""GitHub REST API client for the docs-update agent.

This module is the single seam through which the agent talks to the
GitHub REST API. ``main.py`` builds a :class:`GitHubClient` from env
vars at import; tests construct one directly with a custom
``httpx.Client`` (typically backed by ``httpx.MockTransport``) so they
exercise the real request lifecycle without hitting the network.
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

import httpx

MAX_DIFF_CHARS = 30_000
MAX_FILE_CHARS = 20_000
_HTTP_TIMEOUT = 30.0
_DOC_UPDATE_BRANCH_PREFIXES = ("docs/update-", "docs/auto-update-")
_PROTECTED_BRANCHES = {"main", "master", "develop", "development", "prod", "production"}
_DOC_PATH_PREFIXES = ("docs/", "documentation/", "adr/", "adrs/", "rfcs/", ".github/")
_DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".adoc", ".txt"}
_DOC_FILENAMES = {
    "architecture.md",
    "changelog.md",
    "contributing.md",
    "readme.md",
    "readme.mdx",
}

_REPO_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100})$"
)
_PR_NUMBER_RE = re.compile(r"\(#(?P<number>\d+)\)")

T = TypeVar("T")


@dataclass(frozen=True)
class RepoRef:
    """A parsed owner/repo reference."""

    owner: str
    repo: str

    @classmethod
    def parse(cls, slug: str) -> RepoRef | None:
        match = _REPO_RE.match(slug.strip())
        if match is None:
            return None
        return cls(owner=match.group("owner"), repo=match.group("repo"))

    def api_url(self, path: str) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}/{path}"


def _looks_unsafe(path: str) -> bool:
    if path.startswith("/"):
        return True
    if ".." in path.split("/"):
        return True
    return any(c in path for c in "?#%")


def _looks_unsafe_ref(ref: str) -> bool:
    stripped = ref.strip()
    return not stripped or stripped.startswith("/") or ".." in stripped.split("/") or any(
        c in stripped for c in "?#% \t\r\n"
    )


def _is_protected_branch(branch: str) -> bool:
    return branch.strip().lower() in _PROTECTED_BRANCHES


def _is_docs_update_branch(branch: str) -> bool:
    return branch.startswith(_DOC_UPDATE_BRANCH_PREFIXES) and not _looks_unsafe_ref(branch)


def _is_documentation_path(path: str) -> bool:
    lower_path = path.lower()
    filename = lower_path.rsplit("/", 1)[-1]
    extension = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""

    if extension not in _DOC_EXTENSIONS:
        return False
    if filename.startswith("readme.") or filename in _DOC_FILENAMES:
        return True
    return lower_path.startswith(_DOC_PATH_PREFIXES)


def _source_pr_ref(ref: RepoRef, message: str) -> str | None:
    match = _PR_NUMBER_RE.search(message)
    if match is None:
        return None
    return f"{ref.owner}/{ref.repo}#{match.group('number')}"


def _is_docs_commit(message: str) -> bool:
    return message.lstrip().lower().startswith("docs:")


class GitHubClient:
    """GitHub REST API client with injectable httpx.Client for testing."""

    def __init__(
        self,
        *,
        token: str | None,
        client: httpx.Client | None = None,
    ) -> None:
        self._token = token
        self._client = client

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "atlasap-docs-update-agent",
        }

    def _with_client(self, op: Callable[[httpx.Client], T]) -> T:
        if self._client is not None:
            return op(self._client)
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            return op(client)

    def _check_token(self) -> str | None:
        if not self._token:
            return (
                "ERROR: GITHUB_TOKEN env var not set. Add a classic PAT "
                "(with 'repo' scope, SSO-authorized for the org) to the "
                "agent's .env file and restart."
            )
        return None

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def list_recent_commits(self, repo: str, since_days: int = 1, branch: str = "main") -> str:
        gate = self._check_token()
        if gate is not None:
            return gate
        ref = RepoRef.parse(repo)
        if ref is None:
            return f"ERROR: invalid repo format: {repo!r}. Expected owner/repo."
        if _looks_unsafe_ref(branch):
            return "ERROR: invalid branch ref."
        return self._with_client(lambda c: self._list_commits(c, ref, since_days, branch))

    def _list_commits(
        self, client: httpx.Client, ref: RepoRef, since_days: int, branch: str
    ) -> str:
        since = (datetime.now(UTC) - timedelta(days=since_days)).isoformat()
        try:
            resp = client.get(
                ref.api_url("commits"),
                headers={**self._auth_headers, "Accept": "application/vnd.github+json"},
                params={"since": since, "sha": branch, "per_page": "100"},
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            return f"ERROR: HTTP fetch failed: {exc}"

        if resp.status_code == 404:
            return f"ERROR: repository {ref.owner}/{ref.repo} not found or no access."
        if resp.status_code != 200:
            return f"ERROR: GitHub returned HTTP {resp.status_code}."

        try:
            commits = resp.json()
        except json.JSONDecodeError as exc:
            return f"ERROR: non-JSON response: {exc}"

        filtered_commits = []
        for c in commits:
            msg = (c.get("commit", {}).get("message", "") or "").split("\n")[0]
            if _is_docs_commit(msg):
                continue
            filtered_commits.append((c, msg))

        if not filtered_commits:
            return "No non-docs commits found in the specified time range."

        parts = [f"Found {len(filtered_commits)} non-docs commit(s) on {branch} since {since}:\n"]
        for c, msg in filtered_commits:
            sha = c.get("sha", "?")[:12]
            author = c.get("commit", {}).get("author", {}).get("name", "?")
            date = c.get("commit", {}).get("author", {}).get("date", "?")
            source_pr = _source_pr_ref(ref, msg)
            suffix = f" [source PR: {source_pr}]" if source_pr else ""
            parts.append(f"- {sha} ({date}) by {author}: {msg}{suffix}")
        return "\n".join(parts)

    def get_commit_diff(self, repo: str, sha: str) -> str:
        gate = self._check_token()
        if gate is not None:
            return gate
        ref = RepoRef.parse(repo)
        if ref is None:
            return f"ERROR: invalid repo format: {repo!r}."
        return self._with_client(lambda c: self._fetch_commit_diff(c, ref, sha))

    def _fetch_commit_diff(self, client: httpx.Client, ref: RepoRef, sha: str) -> str:
        try:
            resp = client.get(
                ref.api_url(f"commits/{sha}"),
                headers={**self._auth_headers, "Accept": "application/vnd.github.v3.diff"},
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            return f"ERROR: HTTP fetch failed: {exc}"

        if resp.status_code == 404:
            return f"ERROR: commit {sha} not found."
        if resp.status_code != 200:
            return f"ERROR: GitHub returned HTTP {resp.status_code}."

        diff_text = resp.text
        truncated = ""
        if len(diff_text) > MAX_DIFF_CHARS:
            diff_text = diff_text[:MAX_DIFF_CHARS]
            truncated = f"\n[...diff truncated at {MAX_DIFF_CHARS:,} chars...]"
        return f"Diff for {sha}:\n{diff_text}{truncated}"

    def get_file_content(self, repo: str, path: str, ref: str = "main") -> str:
        gate = self._check_token()
        if gate is not None:
            return gate
        repo_ref = RepoRef.parse(repo)
        if repo_ref is None:
            return f"ERROR: invalid repo format: {repo!r}."
        if _looks_unsafe(path):
            return "ERROR: invalid path (traversal or URL-control characters not allowed)."
        return self._with_client(lambda c: self._fetch_file(c, repo_ref, path, ref))

    def _fetch_file(self, client: httpx.Client, ref: RepoRef, path: str, git_ref: str) -> str:
        try:
            resp = client.get(
                ref.api_url(f"contents/{path}"),
                headers={**self._auth_headers, "Accept": "application/vnd.github.raw"},
                params={"ref": git_ref},
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            return f"ERROR: HTTP fetch failed: {exc}"

        if resp.status_code == 404:
            return f"ERROR: {path} not found at ref {git_ref}."
        if resp.status_code != 200:
            return f"ERROR: GitHub returned HTTP {resp.status_code}."

        body = resp.text
        truncated = ""
        if len(body) > MAX_FILE_CHARS:
            body = body[:MAX_FILE_CHARS]
            truncated = f"\n[...file truncated at {MAX_FILE_CHARS:,} chars...]"
        return f"=== {path} ===\n{body}{truncated}"

    def list_directory(self, repo: str, path: str = "", ref: str = "main") -> str:
        gate = self._check_token()
        if gate is not None:
            return gate
        repo_ref = RepoRef.parse(repo)
        if repo_ref is None:
            return f"ERROR: invalid repo format: {repo!r}."
        if path and _looks_unsafe(path):
            return "ERROR: invalid path."
        return self._with_client(lambda c: self._list_dir(c, repo_ref, path, ref))

    def _list_dir(self, client: httpx.Client, ref: RepoRef, path: str, git_ref: str) -> str:
        url = ref.api_url(f"contents/{path}") if path else ref.api_url("contents/")
        try:
            resp = client.get(
                url,
                headers={**self._auth_headers, "Accept": "application/vnd.github+json"},
                params={"ref": git_ref},
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            return f"ERROR: HTTP fetch failed: {exc}"

        if resp.status_code == 404:
            return f"ERROR: path {path!r} not found at ref {git_ref}."
        if resp.status_code != 200:
            return f"ERROR: GitHub returned HTTP {resp.status_code}."

        try:
            entries = resp.json()
        except json.JSONDecodeError as exc:
            return f"ERROR: non-JSON response: {exc}"

        if not isinstance(entries, list):
            return "ERROR: path is a file, not a directory."

        parts = [f"Contents of {path or '/'}:"]
        for entry in entries:
            kind = entry.get("type", "?")
            name = entry.get("name", "?")
            icon = "📁" if kind == "dir" else "📄"
            parts.append(f"  {icon} {name} ({kind})")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create_branch(self, repo: str, branch_name: str, from_ref: str = "main") -> str:
        gate = self._check_token()
        if gate is not None:
            return gate
        ref = RepoRef.parse(repo)
        if ref is None:
            return f"ERROR: invalid repo format: {repo!r}."
        if not _is_docs_update_branch(branch_name):
            return (
                "ERROR: branch name must start with "
                f"{_DOC_UPDATE_BRANCH_PREFIXES[0]!r} or {_DOC_UPDATE_BRANCH_PREFIXES[1]!r}."
            )
        if _looks_unsafe_ref(from_ref):
            return "ERROR: invalid base ref."
        return self._with_client(lambda c: self._create_branch(c, ref, branch_name, from_ref))

    def _create_branch(
        self, client: httpx.Client, ref: RepoRef, branch_name: str, from_ref: str
    ) -> str:
        try:
            sha_resp = client.get(
                ref.api_url(f"git/refs/heads/{from_ref}"),
                headers={**self._auth_headers, "Accept": "application/vnd.github+json"},
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            return f"ERROR: failed to resolve {from_ref}: {exc}"

        if sha_resp.status_code != 200:
            return f"ERROR: could not find ref {from_ref} (HTTP {sha_resp.status_code})."

        try:
            sha = sha_resp.json()["object"]["sha"]
        except (json.JSONDecodeError, KeyError) as exc:
            return f"ERROR: unexpected response resolving {from_ref}: {exc}"

        try:
            create_resp = client.post(
                ref.api_url("git/refs"),
                headers={**self._auth_headers, "Accept": "application/vnd.github+json"},
                json={"ref": f"refs/heads/{branch_name}", "sha": sha},
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            return f"ERROR: branch creation failed: {exc}"

        if create_resp.status_code == 422:
            return f"ERROR: branch {branch_name!r} already exists."
        if create_resp.status_code not in (200, 201):
            return f"ERROR: branch creation returned HTTP {create_resp.status_code}."

        return f"Branch {branch_name!r} created from {from_ref} at {sha[:12]}."

    def create_or_update_file(
        self, repo: str, path: str, content: str, branch: str, message: str
    ) -> str:
        gate = self._check_token()
        if gate is not None:
            return gate
        ref = RepoRef.parse(repo)
        if ref is None:
            return f"ERROR: invalid repo format: {repo!r}."
        if _looks_unsafe(path):
            return "ERROR: invalid path."
        if not _is_documentation_path(path):
            return "ERROR: only documentation files may be updated."
        if _is_protected_branch(branch):
            return f"ERROR: refusing to write directly to protected branch {branch!r}."
        if not _is_docs_update_branch(branch):
            return (
                "ERROR: writes must target a documentation update branch "
                f"({_DOC_UPDATE_BRANCH_PREFIXES[0]!r} or {_DOC_UPDATE_BRANCH_PREFIXES[1]!r})."
            )
        return self._with_client(lambda c: self._put_file(c, ref, path, content, branch, message))

    def _put_file(
        self,
        client: httpx.Client,
        ref: RepoRef,
        path: str,
        content: str,
        branch: str,
        message: str,
    ) -> str:
        existing_sha = None
        try:
            check_resp = client.get(
                ref.api_url(f"contents/{path}"),
                headers={**self._auth_headers, "Accept": "application/vnd.github+json"},
                params={"ref": branch},
                timeout=_HTTP_TIMEOUT,
            )
            if check_resp.status_code == 200:
                existing_sha = check_resp.json().get("sha")
        except httpx.HTTPError as exc:
            return f"ERROR: failed to fetch existing file SHA for {path}: {exc}"
        except json.JSONDecodeError:
            pass

        encoded = base64.b64encode(content.encode()).decode()
        body: dict = {
            "message": message,
            "content": encoded,
            "branch": branch,
        }
        if existing_sha:
            body["sha"] = existing_sha

        try:
            resp = client.put(
                ref.api_url(f"contents/{path}"),
                headers={**self._auth_headers, "Accept": "application/vnd.github+json"},
                json=body,
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            return f"ERROR: file update failed: {exc}"

        if resp.status_code not in (200, 201):
            return f"ERROR: file update returned HTTP {resp.status_code}: {resp.text[:500]}"

        try:
            commit_sha = resp.json().get("commit", {}).get("sha", "?")[:12]
        except (json.JSONDecodeError, AttributeError):
            commit_sha = "?"
        action = "Updated" if existing_sha else "Created"
        return f"{action} {path} on branch {branch} (commit {commit_sha})."

    def create_pull_request(
        self, repo: str, title: str, body: str, head: str, base: str = "main", *, draft: bool = True
    ) -> str:
        gate = self._check_token()
        if gate is not None:
            return gate
        ref = RepoRef.parse(repo)
        if ref is None:
            return f"ERROR: invalid repo format: {repo!r}."
        if _looks_unsafe_ref(base):
            return "ERROR: invalid base branch."
        if _is_protected_branch(head):
            return f"ERROR: refusing to open a PR from protected branch {head!r}."
        if not _is_docs_update_branch(head):
            return (
                "ERROR: PR head must be a documentation update branch "
                f"({_DOC_UPDATE_BRANCH_PREFIXES[0]!r} or {_DOC_UPDATE_BRANCH_PREFIXES[1]!r})."
            )
        return self._with_client(
            lambda c: self._create_pr(c, ref, title, body, head, base, draft=draft)
        )

    def _create_pr(
        self,
        client: httpx.Client,
        ref: RepoRef,
        title: str,
        body: str,
        head: str,
        base: str,
        *,
        draft: bool = True,
    ) -> str:
        try:
            resp = client.post(
                ref.api_url("pulls"),
                headers={**self._auth_headers, "Accept": "application/vnd.github+json"},
                json={"title": title, "body": body, "head": head, "base": base, "draft": draft},
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            return f"ERROR: PR creation failed: {exc}"

        if resp.status_code not in (200, 201):
            return f"ERROR: PR creation returned HTTP {resp.status_code}: {resp.text[:500]}"

        try:
            pr_data = resp.json()
            pr_url = pr_data.get("html_url", "?")
            pr_number = pr_data.get("number", "?")
        except (json.JSONDecodeError, AttributeError):
            return "PR created but could not parse response."

        return f"PR #{pr_number} created: {pr_url}"


def from_env() -> GitHubClient:
    """Build a :class:`GitHubClient` from process environment variables."""
    return GitHubClient(token=os.environ.get("GITHUB_TOKEN"))
