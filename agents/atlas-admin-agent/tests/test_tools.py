"""Tests for ``atlas_admin_agent.tools``.

Covers:

- Every convenience read wired to the right Atlas path.
- ``atlas_request`` GET passthrough and mutation-suspends-via-SuspendPayload.
- ``atlas_execute`` executes approved mutations and refuses GETs.
- Method validation (typos like ``POSTT`` are rejected before hitting Atlas).
- Argument validation (invalid JSON, full URLs, empty path, blank
  ``human_description``).
- Error surface mirrors the documented Atlas error envelope.
- ``_get_client`` singleton behavior.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from atlas_admin_agent import tools
from atlas_admin_agent.atlas_client import AtlasApiError, AtlasConfigError


class _FakeApp:
    """Captures ``@app.tool(is_local=True)`` registrations."""

    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []

    def tool(self, *, is_local: bool = True):
        def decorator(fn):
            self.registered[fn.__name__] = fn
            self.calls.append({"name": fn.__name__, "is_local": is_local})
            return fn

        return decorator


class _StubClient:
    def __init__(
        self,
        *,
        paginate_results: list[dict] | None = None,
        request_response: Any = None,
        request_error: Exception | None = None,
        base_url: str = "https://atlas.test/api/atlas/v2",
        api_version_accept: str = "application/vnd.atlas.2025-03-12+json",
    ) -> None:
        self.calls: list[dict] = []
        self.paginate_kwargs: list[dict] = []
        self._paginate_results = paginate_results or []
        self._request_response = request_response
        self._request_error = request_error

        class _Cfg:
            pass

        cfg = _Cfg()
        cfg.base_url = base_url
        cfg.api_version_accept = api_version_accept
        self.config = cfg

    def paginate(self, path, *, params=None, max_items=None, page_size=500):
        self.paginate_kwargs.append(
            {"path": path, "params": params, "max_items": max_items, "page_size": page_size}
        )
        self.calls.append({"op": "paginate", "path": path, "max_items": max_items})
        items = list(self._paginate_results)
        if max_items is not None:
            items = items[:max_items]
        yield from items

    def request(self, method, path, *, params=None, json=None):
        self.calls.append(
            {"op": "request", "method": method, "path": path, "params": params, "json": json}
        )
        if self._request_error:
            raise self._request_error
        return self._request_response


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch):
    app = _FakeApp()
    tools.register(app)
    stub = _StubClient()
    monkeypatch.setattr(tools, "_get_client", lambda: stub)
    monkeypatch.setattr(tools, "_client", None)
    return app.registered, stub


# --- registration shape ---------------------------------------------------


def test_register_returns_mapping_of_all_tool_names() -> None:
    app = _FakeApp()
    result = tools.register(app)
    expected = {
        "list_organizations",
        "list_projects",
        "get_project",
        "list_clusters",
        "get_cluster",
        "list_snapshots",
        "list_database_users",
        "list_network_access_entries",
        "list_alerts",
        "list_backup_restore_jobs",
        "atlas_describe_endpoint",
        "atlas_request",
        "atlas_execute",
    }
    assert set(result.keys()) == expected
    assert set(app.registered.keys()) == expected
    for name, fn in result.items():
        assert callable(fn)
        assert fn is app.registered[name]


def test_register_wires_is_local_true_everywhere() -> None:
    app = _FakeApp()
    tools.register(app)
    assert all(c["is_local"] is True for c in app.calls)


# --- convenience read tools route to correct paths ------------------------


@pytest.mark.parametrize(
    "tool_name,call_args,expected_path",
    [
        ("list_organizations", {}, "/orgs"),
        ("list_projects", {}, "/groups"),
        ("list_clusters", {"project_id": "p1"}, "/groups/p1/clusters"),
        (
            "list_database_users",
            {"project_id": "p1"},
            "/groups/p1/databaseUsers",
        ),
        (
            "list_network_access_entries",
            {"project_id": "p1"},
            "/groups/p1/accessList",
        ),
        ("list_alerts", {"project_id": "p1"}, "/groups/p1/alerts"),
        (
            "list_backup_restore_jobs",
            {"project_id": "p1", "cluster_name": "Cluster0"},
            "/groups/p1/clusters/Cluster0/backup/restoreJobs",
        ),
    ],
)
def test_list_tools_paginate_to_correct_path(
    registered, tool_name: str, call_args: dict, expected_path: str
) -> None:
    regs, stub = registered
    stub._paginate_results = [{"id": "x"}]
    result = regs[tool_name](**call_args)
    parsed = json.loads(result)
    assert parsed["path"] == expected_path
    assert parsed["count"] == 1
    assert stub.calls[0] == {"op": "paginate", "path": expected_path, "max_items": None}


def test_list_snapshots_caps_results_at_limit(registered) -> None:
    regs, stub = registered
    stub._paginate_results = [{"id": f"s{i}"} for i in range(50)]
    result = regs["list_snapshots"](project_id="p1", cluster_name="c1", limit=3)
    parsed = json.loads(result)
    assert parsed["count"] == 3
    assert stub.calls[0]["max_items"] == 3
    assert stub.calls[0]["path"] == "/groups/p1/clusters/c1/backup/snapshots"


def test_list_snapshots_floor_on_limit() -> None:
    # limit <= 0 is floored to 1 to avoid an infinite paginate call.
    stub = _StubClient(paginate_results=[{"id": "s1"}, {"id": "s2"}])

    import atlas_admin_agent.tools as tools_mod

    original = tools_mod._get_client
    tools_mod._get_client = lambda: stub
    try:
        app = _FakeApp()
        tools_mod.register(app)
        result = app.registered["list_snapshots"](project_id="p", cluster_name="c", limit=0)
    finally:
        tools_mod._get_client = original
    parsed = json.loads(result)
    assert parsed["count"] == 1


def test_get_project_single_get(registered) -> None:
    regs, stub = registered
    stub._request_response = {"id": "p1", "name": "Alpha"}
    result = regs["get_project"](project_id="p1")
    parsed = json.loads(result)
    assert parsed["path"] == "/groups/p1"
    assert parsed["body"]["id"] == "p1"
    assert stub.calls[0] == {
        "op": "request",
        "method": "GET",
        "path": "/groups/p1",
        "params": None,
        "json": None,
    }


def test_get_cluster_single_get(registered) -> None:
    regs, stub = registered
    stub._request_response = {"name": "c1", "stateName": "IDLE"}
    result = regs["get_cluster"](project_id="p1", cluster_name="c1")
    parsed = json.loads(result)
    assert parsed["body"]["name"] == "c1"
    assert stub.calls[0]["path"] == "/groups/p1/clusters/c1"


# --- atlas_request: GET passthrough ---------------------------------------


def test_atlas_request_get_passes_through(registered) -> None:
    regs, stub = registered
    stub._request_response = {"results": [{"id": "x"}]}

    result = regs["atlas_request"](
        method="GET",
        path="/groups/p1/clusters",
        human_description="list clusters",
    )
    parsed = json.loads(result)

    assert "body" in parsed
    assert stub.calls == [
        {
            "op": "request",
            "method": "GET",
            "path": "/groups/p1/clusters",
            "params": None,
            "json": None,
        }
    ]


def test_atlas_request_get_with_query_params(registered) -> None:
    regs, stub = registered
    stub._request_response = {"ok": True}

    regs["atlas_request"](
        method="GET",
        path="/groups",
        human_description="list with envelope",
        query_params_json=json.dumps({"envelope": "true", "includeCount": "true"}),
    )

    assert stub.calls[0]["params"] == {"envelope": "true", "includeCount": "true"}


def test_atlas_request_accepts_lowercase_method(registered) -> None:
    regs, stub = registered
    stub._request_response = {"ok": True}
    regs["atlas_request"](method="get", path="/orgs", human_description="list orgs")
    assert stub.calls[0]["method"] == "GET"


# --- atlas_request: mutation suspension -----------------------------------


def test_atlas_request_mutation_returns_suspend_payload(registered) -> None:
    regs, stub = registered

    result = regs["atlas_request"](
        method="DELETE",
        path="/groups/p1/clusters/c1",
        human_description="Delete cluster c1 in project p1",
    )
    parsed = json.loads(result)

    assert parsed["__suspend__"] is True
    assert parsed["suspend_reason"] == "atlas_mutation_approval"
    ctx = parsed["suspend_context"]
    assert ctx["method"] == "DELETE"
    assert ctx["path"] == "/groups/p1/clusters/c1"
    assert ctx["human_description"] == "Delete cluster c1 in project p1"
    assert ctx["decision_type"] == "atlas_api_mutation"
    assert ctx["target_base_url"] == "https://atlas.test/api/atlas/v2"
    assert ctx["api_version"] == "application/vnd.atlas.2025-03-12+json"
    assert ctx["body"] == {}
    assert ctx["query_params"] == {}
    assert ctx["task_id"].startswith("ATLAS-")
    assert len(ctx["task_id"]) == len("ATLAS-") + 8
    # Crucially, the mutation did NOT hit Atlas.
    assert stub.calls == []


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_atlas_request_suspends_for_every_mutation_verb(registered, method: str) -> None:
    regs, stub = registered
    result = regs["atlas_request"](method=method, path="/x", human_description="do it")
    parsed = json.loads(result)
    assert parsed["__suspend__"] is True
    assert parsed["suspend_context"]["method"] == method
    assert stub.calls == []


def test_atlas_request_post_includes_body_in_suspend_context(registered) -> None:
    regs, _ = registered
    body = {"name": "NewCluster", "clusterType": "REPLICASET"}

    result = regs["atlas_request"](
        method="POST",
        path="/groups/p1/clusters",
        human_description="Create cluster NewCluster in project p1",
        body_json=json.dumps(body),
    )
    parsed = json.loads(result)

    assert parsed["__suspend__"] is True
    assert parsed["suspend_context"]["body"] == body


def test_atlas_request_patch_with_query_params(registered) -> None:
    regs, _ = registered
    result = regs["atlas_request"](
        method="PATCH",
        path="/groups/p1/clusters/c1",
        human_description="scale up c1",
        body_json='{"clusterType":"REPLICASET"}',
        query_params_json='{"envelope":"true"}',
    )
    parsed = json.loads(result)
    ctx = parsed["suspend_context"]
    assert ctx["query_params"] == {"envelope": "true"}
    assert ctx["body"] == {"clusterType": "REPLICASET"}


def test_atlas_request_created_at_is_iso8601_utc(registered) -> None:
    regs, _ = registered
    result = regs["atlas_request"](method="DELETE", path="/x", human_description="go")
    ctx = json.loads(result)["suspend_context"]
    # ISO 8601 format with UTC offset
    assert "T" in ctx["created_at"]
    assert ctx["created_at"].endswith("+00:00")


# --- atlas_request: validation --------------------------------------------


def test_atlas_request_requires_method(registered) -> None:
    regs, _ = registered
    result = regs["atlas_request"](method="", path="/orgs", human_description="x")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "method" in parsed["message"]


def test_atlas_request_rejects_unknown_method(registered) -> None:
    regs, stub = registered
    result = regs["atlas_request"](method="POSTT", path="/groups", human_description="x")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "POSTT" in parsed["message"]
    assert stub.calls == []


def test_atlas_request_requires_human_description(registered) -> None:
    regs, _ = registered
    result = regs["atlas_request"](
        method="POST",
        path="/groups/p1/clusters",
        human_description="   ",
        body_json='{"name":"c"}',
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "human_description" in parsed["message"]


def test_atlas_request_strips_human_description_whitespace(registered) -> None:
    regs, _ = registered
    result = regs["atlas_request"](
        method="DELETE",
        path="/x",
        human_description="  Delete the thing  ",
    )
    parsed = json.loads(result)
    assert parsed["suspend_context"]["human_description"] == "Delete the thing"


def test_atlas_request_rejects_invalid_json_body(registered) -> None:
    regs, _ = registered
    result = regs["atlas_request"](
        method="POST",
        path="/groups",
        human_description="x",
        body_json="not json",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "body_json" in parsed["message"]


def test_atlas_request_rejects_invalid_query_params_json(registered) -> None:
    regs, _ = registered
    result = regs["atlas_request"](
        method="GET",
        path="/groups",
        human_description="x",
        query_params_json="{broken",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "query_params_json" in parsed["message"]


def test_atlas_request_rejects_full_url(registered) -> None:
    regs, _ = registered
    result = regs["atlas_request"](
        method="GET",
        path="https://cloud.mongodb.com/api/atlas/v2/orgs",
        human_description="list orgs",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"


def test_atlas_request_rejects_empty_path(registered) -> None:
    regs, _ = registered
    result = regs["atlas_request"](method="GET", path="", human_description="x")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "path" in parsed["message"]


def test_atlas_request_normalizes_missing_leading_slash_on_mutation(registered) -> None:
    regs, _ = registered
    result = regs["atlas_request"](method="DELETE", path="groups/p1", human_description="go")
    ctx = json.loads(result)["suspend_context"]
    assert ctx["path"] == "/groups/p1"


# --- atlas_request: GET error surface -------------------------------------


def test_atlas_request_get_error_is_reported_cleanly(registered) -> None:
    regs, stub = registered
    stub._request_error = AtlasApiError(
        404,
        "RESOURCE_NOT_FOUND",
        "no",
        "/groups/x",
        detail="No group x",
        reason="Not Found",
        parameters=["x"],
    )

    result = regs["atlas_request"](
        method="GET",
        path="/groups/x",
        human_description="get missing project",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["error"] == "atlas_api_error"
    assert parsed["status_code"] == 404
    assert parsed["error_code"] == "RESOURCE_NOT_FOUND"
    assert parsed["detail"] == "No group x"
    assert parsed["reason"] == "Not Found"
    assert parsed["parameters"] == ["x"]
    assert parsed["atlas_path"] == "/groups/x"


def test_atlas_request_get_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without credentials configured, the tool surfaces a config_error rather
    # than crashing the agent loop.
    def raise_config_error():
        raise AtlasConfigError("Missing ATLAS_PUBLIC_KEY")

    monkeypatch.setattr(tools, "_get_client", raise_config_error)
    monkeypatch.setattr(tools, "_client", None)
    app = _FakeApp()
    tools.register(app)
    result = app.registered["atlas_request"](method="GET", path="/orgs", human_description="x")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["error"] == "config_error"


def test_atlas_request_mutation_config_error_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_config_error():
        raise AtlasConfigError("missing creds")

    monkeypatch.setattr(tools, "_get_client", raise_config_error)
    monkeypatch.setattr(tools, "_client", None)
    app = _FakeApp()
    tools.register(app)
    result = app.registered["atlas_request"](
        method="POST", path="/groups/p/clusters", human_description="create"
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["error"] == "config_error"


# --- atlas_execute --------------------------------------------------------


def test_atlas_execute_performs_mutation(registered) -> None:
    regs, stub = registered
    stub._request_response = {"status": "ACCEPTED"}

    result = regs["atlas_execute"](
        method="POST",
        path="/groups/p1/clusters",
        body_json='{"name":"c"}',
    )
    parsed = json.loads(result)

    assert parsed["status"] == "ok"
    assert parsed["method"] == "POST"
    assert parsed["path"] == "/groups/p1/clusters"
    assert parsed["body"] == {"status": "ACCEPTED"}
    assert stub.calls == [
        {
            "op": "request",
            "method": "POST",
            "path": "/groups/p1/clusters",
            "params": None,
            "json": {"name": "c"},
        }
    ]


def test_atlas_execute_delete_with_no_body(registered) -> None:
    regs, stub = registered
    stub._request_response = {}
    result = regs["atlas_execute"](method="DELETE", path="/groups/p/clusters/c")
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert stub.calls[0]["json"] is None


def test_atlas_execute_patch_with_query_params(registered) -> None:
    regs, stub = registered
    stub._request_response = {"stateName": "UPDATING"}
    result = regs["atlas_execute"](
        method="PATCH",
        path="/groups/p/clusters/c",
        body_json='{"paused": true}',
        query_params_json='{"envelope":"true"}',
    )
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert stub.calls[0]["params"] == {"envelope": "true"}
    assert stub.calls[0]["json"] == {"paused": True}


def test_atlas_execute_rejects_get() -> None:
    app = _FakeApp()
    tools.register(app)
    result = app.registered["atlas_execute"](method="GET", path="/orgs")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "GET" in parsed["message"]


def test_atlas_execute_rejects_unknown_method() -> None:
    app = _FakeApp()
    tools.register(app)
    result = app.registered["atlas_execute"](method="DELETETHIS", path="/x")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "DELETETHIS" in parsed["message"]


def test_atlas_execute_rejects_empty_method() -> None:
    app = _FakeApp()
    tools.register(app)
    result = app.registered["atlas_execute"](method="", path="/x")
    parsed = json.loads(result)
    assert parsed["status"] == "error"


def test_atlas_execute_surfaces_full_error_envelope(registered) -> None:
    regs, stub = registered
    stub._request_error = AtlasApiError(
        400,
        "CLUSTER_NAME_TOO_LONG",
        "body",
        "/groups/p/clusters",
        detail="name too long",
        reason="Bad Request",
        parameters=["longname", 64],
    )
    result = regs["atlas_execute"](
        method="POST", path="/groups/p/clusters", body_json='{"name":"x"}'
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["status_code"] == 400
    assert parsed["error_code"] == "CLUSTER_NAME_TOO_LONG"
    assert parsed["detail"] == "name too long"
    assert parsed["parameters"] == ["longname", 64]


def test_atlas_execute_rejects_invalid_json() -> None:
    app = _FakeApp()
    tools.register(app)
    result = app.registered["atlas_execute"](method="POST", path="/x", body_json="notjson")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "body_json" in parsed["message"]


def test_atlas_execute_surfaces_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad():
        raise AtlasConfigError("missing creds")

    monkeypatch.setattr(tools, "_get_client", bad)
    monkeypatch.setattr(tools, "_client", None)
    app = _FakeApp()
    tools.register(app)
    result = app.registered["atlas_execute"](
        method="POST", path="/groups/p/clusters", body_json='{"name":"c"}'
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["error"] == "config_error"


# --- convenience read error paths ---------------------------------------


def test_list_projects_reports_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad():
        raise AtlasConfigError("missing")

    monkeypatch.setattr(tools, "_get_client", bad)
    monkeypatch.setattr(tools, "_client", None)
    app = _FakeApp()
    tools.register(app)
    result = app.registered["list_projects"]()
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["error"] == "config_error"


# --- atlas_describe_endpoint --------------------------------------------


def test_atlas_describe_endpoint_returns_spec_for_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_describe(method, path):
        captured["method"] = method
        captured["path"] = path
        return {
            "operationId": "createGroupCluster",
            "method": method,
            "path": "/api/atlas/v2/groups/{groupId}/clusters",
            "resolved_content_type": "application/vnd.atlas.2024-10-23+json",
            "request_body_schema": {"type": "object", "properties": {"name": {}}},
            "parameters": [],
            "spec_version": "2.0",
            "spec_source": "https://example/openapi.json",
        }

    monkeypatch.setattr(tools, "describe_endpoint", fake_describe)
    app = _FakeApp()
    tools.register(app)

    result = app.registered["atlas_describe_endpoint"](
        method="POST", path="/groups/{groupId}/clusters"
    )
    parsed = json.loads(result)
    assert parsed["operationId"] == "createGroupCluster"
    assert parsed["resolved_content_type"] == "application/vnd.atlas.2024-10-23+json"
    assert captured["method"] == "POST"
    assert captured["path"] == "/groups/{groupId}/clusters"


def test_atlas_describe_endpoint_works_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Schema lookup doesn't need Atlas creds — it just fetches the spec
    # file from GitHub. Verify it still works when ATLAS_* env is unset.
    def fake_describe(method, path):
        return {"operationId": "x"}

    monkeypatch.setattr(tools, "describe_endpoint", fake_describe)
    app = _FakeApp()
    tools.register(app)
    result = app.registered["atlas_describe_endpoint"](
        method="POST", path="/groups/{groupId}/clusters"
    )
    parsed = json.loads(result)
    assert parsed["operationId"] == "x"


def test_atlas_describe_endpoint_surfaces_spec_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from atlas_admin_agent.openapi_spec import SpecLookupError

    def bad(method, path):
        raise SpecLookupError("path not in spec")

    monkeypatch.setattr(tools, "describe_endpoint", bad)
    app = _FakeApp()
    tools.register(app)
    result = app.registered["atlas_describe_endpoint"](method="GET", path="/nowhere")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["error"] == "spec_lookup_error"


def test_atlas_describe_endpoint_requires_method() -> None:
    app = _FakeApp()
    tools.register(app)
    result = app.registered["atlas_describe_endpoint"](method="", path="/x")
    parsed = json.loads(result)
    assert parsed["status"] == "error"


def test_list_projects_reports_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubClient()

    def exploding_paginate(*a, **kw):
        raise AtlasApiError(
            403,
            "FORBIDDEN",
            "no",
            "/groups",
            detail="Key lacks role",
            reason="Forbidden",
            parameters=["org-x"],
        )
        yield  # pragma: no cover — make this a generator

    stub.paginate = exploding_paginate  # type: ignore[method-assign]
    monkeypatch.setattr(tools, "_get_client", lambda: stub)
    monkeypatch.setattr(tools, "_client", None)
    app = _FakeApp()
    tools.register(app)

    result = app.registered["list_projects"]()
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["status_code"] == 403
    assert parsed["error_code"] == "FORBIDDEN"
    assert parsed["detail"] == "Key lacks role"
    assert parsed["parameters"] == ["org-x"]


# --- singleton _get_client ------------------------------------------------


def test_get_client_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_from_env():
        calls.append(1)
        stub = _StubClient()
        return stub

    monkeypatch.setattr(tools, "_client", None)
    monkeypatch.setattr(tools, "from_env", fake_from_env)

    c1 = tools._get_client()
    c2 = tools._get_client()

    assert c1 is c2
    assert len(calls) == 1
