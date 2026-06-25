"""Unit tests for the docs-update GitHub REST client."""

from __future__ import annotations

import base64
import json

import httpx

from docs_update_agent.github_client import GitHubClient


def _client(handler) -> GitHubClient:
    transport = httpx.MockTransport(handler)
    return GitHubClient(token="token", client=httpx.Client(transport=transport))


def _json_body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


class TestReadOperations:
    def test_missing_token_gate_prevents_request(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("unexpected network request")

        client = GitHubClient(
            token=None,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        result = client.list_recent_commits("10gen/example")

        assert result.startswith("ERROR: GITHUB_TOKEN env var not set")

    def test_list_recent_commits_uses_requested_branch(self):
        seen_sha = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen_sha
            seen_sha = request.url.params["sha"]
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": "abcdef1234567890",
                        "commit": {
                            "message": "Update API",
                            "author": {"name": "Dev", "date": "2026-05-18T00:00:00Z"},
                        },
                    }
                ],
            )

        result = _client(handler).list_recent_commits("10gen/example", 3, "release")

        assert seen_sha == "release"
        assert "Found 1 non-docs commit(s) on release" in result

    def test_list_recent_commits_includes_source_pr_reference(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": "abcdef1234567890",
                        "commit": {
                            "message": "Add config docs (#42)",
                            "author": {"name": "Dev", "date": "2026-05-18T00:00:00Z"},
                        },
                    }
                ],
            )

        result = _client(handler).list_recent_commits("10gen/example", 3, "main")

        assert "Add config docs (#42)" in result
        assert "source PR: 10gen/example#42" in result

    def test_list_recent_commits_skips_docs_prefixed_commits(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": "1111111111111111",
                        "commit": {
                            "message": "docs: update README (#41)",
                            "author": {"name": "Doc Bot", "date": "2026-05-18T00:00:00Z"},
                        },
                    },
                    {
                        "sha": "2222222222222222",
                        "commit": {
                            "message": "Add config support (#42)",
                            "author": {"name": "Dev", "date": "2026-05-18T01:00:00Z"},
                        },
                    },
                    {
                        "sha": "3333333333333333",
                        "commit": {
                            "message": "  Docs: regenerate API docs (#43)",
                            "author": {"name": "Doc Bot", "date": "2026-05-18T02:00:00Z"},
                        },
                    },
                ],
            )

        result = _client(handler).list_recent_commits("10gen/example", 3, "main")

        assert "Found 1 non-docs commit(s) on main" in result
        assert "Add config support (#42)" in result
        assert "source PR: 10gen/example#42" in result
        assert "docs: update README" not in result
        assert "Docs: regenerate API docs" not in result

    def test_list_recent_commits_reports_when_only_docs_prefixed_commits_exist(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": "1111111111111111",
                        "commit": {
                            "message": "docs: update README",
                            "author": {"name": "Doc Bot", "date": "2026-05-18T00:00:00Z"},
                        },
                    }
                ],
            )

        result = _client(handler).list_recent_commits("10gen/example", 3, "main")

        assert result == "No non-docs commits found in the specified time range."


class TestCreateBranch:
    def test_create_branch_uses_plural_refs_endpoint(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                assert request.url.path == "/repos/10gen/example/git/refs/heads/main"
                return httpx.Response(200, json={"object": {"sha": "abc123def456"}})
            assert request.method == "POST"
            assert request.url.path == "/repos/10gen/example/git/refs"
            assert _json_body(request) == {
                "ref": "refs/heads/docs/update-api",
                "sha": "abc123def456",
            }
            return httpx.Response(201, json={})

        result = _client(handler).create_branch("10gen/example", "docs/update-api")

        assert "Branch 'docs/update-api' created" in result
        assert len(requests) == 2

    def test_create_branch_rejects_non_docs_update_branch(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("unexpected network request")

        result = _client(handler).create_branch("10gen/example", "feature/source-change")

        assert "branch name must start" in result


class TestCreateOrUpdateFile:
    def test_create_documentation_file_on_docs_update_branch(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(404, json={})
            assert request.method == "PUT"
            body = _json_body(request)
            assert body["branch"] == "docs/update-api"
            assert body["message"] == "docs: update api"
            assert base64.b64decode(body["content"]).decode() == "# API\n"
            assert "sha" not in body
            return httpx.Response(201, json={"commit": {"sha": "def456abc123"}})

        result = _client(handler).create_or_update_file(
            "10gen/example",
            "docs/api.md",
            "# API\n",
            "docs/update-api",
            "docs: update api",
        )

        assert "Created docs/api.md" in result
        assert len(requests) == 2

    def test_update_documentation_file_includes_existing_sha(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json={"sha": "old-sha"})
            body = _json_body(request)
            assert body["sha"] == "old-sha"
            return httpx.Response(200, json={"commit": {"sha": "def456abc123"}})

        result = _client(handler).create_or_update_file(
            "10gen/example",
            "README.md",
            "# Project\n",
            "docs/update-readme",
            "docs: update readme",
        )

        assert "Updated README.md" in result

    def test_rejects_source_path_writes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("unexpected network request")

        result = _client(handler).create_or_update_file(
            "10gen/example",
            "src/main.py",
            "print('no')\n",
            "docs/update-readme",
            "docs: update readme",
        )

        assert "only documentation files" in result

    def test_rejects_protected_branch_writes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("unexpected network request")

        result = _client(handler).create_or_update_file(
            "10gen/example",
            "docs/api.md",
            "# API\n",
            "main",
            "docs: update api",
        )

        assert "protected branch" in result

    def test_existing_sha_fetch_http_error_is_reported(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                raise httpx.ConnectError("boom", request=request)
            raise AssertionError("PUT should not run after SHA fetch failure")

        result = _client(handler).create_or_update_file(
            "10gen/example",
            "docs/api.md",
            "# API\n",
            "docs/update-api",
            "docs: update api",
        )

        assert "failed to fetch existing file SHA for docs/api.md" in result
        assert "boom" in result


class TestCreatePullRequest:
    def test_create_pull_request_payload(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path == "/repos/10gen/example/pulls"
            assert _json_body(request) == {
                "title": "docs: update api",
                "body": "Summary",
                "head": "docs/update-api",
                "base": "main",
                "draft": True,
            }
            return httpx.Response(201, json={"number": 7, "html_url": "https://github.com/pr/7"})

        result = _client(handler).create_pull_request(
            "10gen/example",
            "docs: update api",
            "Summary",
            "docs/update-api",
        )

        assert result == "PR #7 created: https://github.com/pr/7"

    def test_rejects_pr_from_protected_head(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("unexpected network request")

        result = _client(handler).create_pull_request(
            "10gen/example",
            "docs: update api",
            "Summary",
            "main",
        )

        assert "protected branch" in result
