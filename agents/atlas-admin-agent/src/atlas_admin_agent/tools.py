"""Atlas Admin Agent tools.

Three categories:

1. **Convenience reads** (paginated GET wrappers for common resources). They
   exist so the LLM doesn't have to guess endpoint paths and so listing
   endpoints automatically paginate.
2. **Generic escape hatch** — ``atlas_request`` covers the full Atlas Admin
   API v2 surface. GET requests pass through; non-GET requests return a
   :class:`SuspendPayload` that the platform surfaces as a human approval
   panel.
3. **Approved mutation runner** — ``atlas_execute`` performs the mutation
   unconditionally. The system prompt forbids the LLM from calling it except
   immediately after an approval tool message.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from runner_shared.models import SuspendPayload  # type: ignore[import-untyped]

from atlas_admin_agent.atlas_client import (
    ALLOWED_HTTP_METHODS,
    AtlasApiError,
    AtlasClient,
    AtlasConfigError,
    from_env,
)
from atlas_admin_agent.openapi_spec import SpecLookupError, describe_endpoint

logger = logging.getLogger(__name__)

_client: AtlasClient | None = None


def _get_client() -> AtlasClient:
    """Lazy singleton so tool functions don't need dep injection.

    The client is not built at import time — that would require Atlas
    credentials for simply importing the module (breaks tests). Tests
    monkeypatch ``_get_client`` directly.
    """
    global _client
    if _client is None:
        _client = from_env()
    return _client


def _format_json(value: Any) -> str:
    return json.dumps(value, indent=2, default=str, sort_keys=True)


def _parse_json_arg(raw: str, arg_name: str) -> Any:
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON in {arg_name}: {exc}") from exc


def _normalize_path(path: str) -> str:
    if not path:
        raise ValueError("path is required")
    if "://" in path:
        raise ValueError("path must be a path relative to the Atlas API base URL, not a full URL")
    return path if path.startswith("/") else f"/{path}"


def _error_json(kind: str, message: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"status": "error", "error": kind, "message": message}
    payload.update(extra)
    return _format_json(payload)


def _paginated_list(path: str, max_items: int | None = None) -> str:
    try:
        client = _get_client()
        items = list(client.paginate(path, max_items=max_items))
    except AtlasConfigError as exc:
        return _error_json("config_error", str(exc))
    except AtlasApiError as exc:
        return _error_json(
            "atlas_api_error",
            str(exc),
            status_code=exc.status,
            error_code=exc.error_code,
            detail=exc.detail,
            reason=exc.reason,
            parameters=exc.parameters,
            atlas_path=exc.url,
        )
    return _format_json({"path": path, "count": len(items), "results": items})


def _single_get(path: str, params: dict[str, Any] | None = None) -> str:
    try:
        body = _get_client().request("GET", path, params=params)
    except AtlasConfigError as exc:
        return _error_json("config_error", str(exc))
    except AtlasApiError as exc:
        return _error_json(
            "atlas_api_error",
            str(exc),
            status_code=exc.status,
            error_code=exc.error_code,
            detail=exc.detail,
            reason=exc.reason,
            parameters=exc.parameters,
            atlas_path=exc.url,
        )
    return _format_json({"path": path, "body": body})


def register(app: Any) -> dict[str, Any]:
    """Register all Atlas tools on the given atlasap ``App`` instance."""

    @app.tool(is_local=True)
    def list_organizations() -> str:
        """List Atlas organizations accessible to the configured API key."""
        return _paginated_list("/orgs")

    @app.tool(is_local=True)
    def list_projects() -> str:
        """List every Atlas project (group) in the current organization."""
        return _paginated_list("/groups")

    @app.tool(is_local=True)
    def get_project(project_id: str) -> str:
        """Fetch full details for a single Atlas project by ID."""
        return _single_get(f"/groups/{project_id}")

    @app.tool(is_local=True)
    def list_clusters(project_id: str) -> str:
        """List all clusters in a project."""
        return _paginated_list(f"/groups/{project_id}/clusters")

    @app.tool(is_local=True)
    def get_cluster(project_id: str, cluster_name: str) -> str:
        """Fetch full details for a single cluster."""
        return _single_get(f"/groups/{project_id}/clusters/{cluster_name}")

    @app.tool(is_local=True)
    def list_snapshots(project_id: str, cluster_name: str, limit: int = 20) -> str:
        """List cloud backup snapshots for a cluster, most recent first."""
        return _paginated_list(
            f"/groups/{project_id}/clusters/{cluster_name}/backup/snapshots",
            max_items=max(1, int(limit)),
        )

    @app.tool(is_local=True)
    def list_database_users(project_id: str) -> str:
        """List database users (authentication principals) for a project."""
        return _paginated_list(f"/groups/{project_id}/databaseUsers")

    @app.tool(is_local=True)
    def list_network_access_entries(project_id: str) -> str:
        """List IP access-list entries for a project."""
        return _paginated_list(f"/groups/{project_id}/accessList")

    @app.tool(is_local=True)
    def list_alerts(project_id: str) -> str:
        """List open alerts for a project."""
        return _paginated_list(f"/groups/{project_id}/alerts")

    @app.tool(is_local=True)
    def list_backup_restore_jobs(project_id: str, cluster_name: str) -> str:
        """List backup restore jobs for a cluster."""
        return _paginated_list(f"/groups/{project_id}/clusters/{cluster_name}/backup/restoreJobs")

    @app.tool(is_local=True)
    def atlas_request(
        method: str,
        path: str,
        human_description: str,
        body_json: str = "",
        query_params_json: str = "",
    ) -> str:
        """Call any Atlas Admin API v2 endpoint.

        GET requests execute immediately. Any non-GET request (POST, PUT,
        PATCH, DELETE) is suspended for human approval — this tool returns a
        SuspendPayload describing the request, and the platform shows the
        reviewer method/path/body. After approval the model must call
        ``atlas_execute`` with the same arguments to perform the mutation.

        Args:
            method: HTTP method (GET/POST/PUT/PATCH/DELETE).
            path: Atlas API path starting with '/', e.g. '/groups/{id}/clusters'.
            human_description: Required plain-English description of what the
                request will do (e.g. "Delete cluster Cluster0 in project X").
                Shown to the human reviewer.
            body_json: JSON string for the request body (optional).
            query_params_json: JSON string of query-string params (optional).
        """
        method_u = (method or "").upper().strip()
        if not method_u:
            return _error_json("invalid_request", "method is required")
        if method_u not in ALLOWED_HTTP_METHODS:
            return _error_json(
                "invalid_request",
                f"unsupported HTTP method {method_u!r}; allowed: {sorted(ALLOWED_HTTP_METHODS)}",
            )
        if not human_description or not human_description.strip():
            return _error_json(
                "invalid_request",
                "human_description is required so the reviewer can evaluate the request",
            )
        try:
            normalized = _normalize_path(path)
            body = _parse_json_arg(body_json, "body_json")
            query_params = _parse_json_arg(query_params_json, "query_params_json")
        except ValueError as exc:
            return _error_json("invalid_request", str(exc))

        if method_u == "GET":
            params = query_params if isinstance(query_params, dict) else None
            return _single_get(normalized, params=params)

        task_id = f"ATLAS-{uuid.uuid4().hex[:8].upper()}"
        try:
            cfg = _get_client().config
            target_base_url = cfg.base_url
            api_version = cfg.api_version_accept
        except AtlasConfigError as exc:
            return _error_json("config_error", str(exc))

        return SuspendPayload(
            suspend_reason="atlas_mutation_approval",
            suspend_context={
                "task_id": task_id,
                "decision_type": "atlas_api_mutation",
                "method": method_u,
                "path": normalized,
                "query_params": query_params if isinstance(query_params, dict) else {},
                "body": body if body is not None else {},
                "human_description": human_description.strip(),
                "target_base_url": target_base_url,
                "api_version": api_version,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "instructions": (
                    f"Approve or reject {method_u} {normalized}. Review the body before approving."
                ),
            },
        ).to_json()

    @app.tool(is_local=True)
    def atlas_execute(
        method: str,
        path: str,
        body_json: str = "",
        query_params_json: str = "",
    ) -> str:
        """Execute an APPROVED Atlas mutation. Do NOT call without prior approval.

        This tool runs the request unconditionally. The system prompt requires
        it be called only immediately after an approval message for the same
        method/path appears in the transcript. Use the same ``method``,
        ``path``, ``body_json``, and ``query_params_json`` the reviewer
        approved.
        """
        method_u = (method or "").upper().strip()
        if not method_u:
            return _error_json("invalid_request", "method is required")
        if method_u == "GET":
            return _error_json(
                "invalid_request",
                "atlas_execute is for approved mutations only — use atlas_request for GETs",
            )
        if method_u not in ALLOWED_HTTP_METHODS:
            return _error_json(
                "invalid_request",
                f"unsupported HTTP method {method_u!r}; "
                f"allowed: {sorted(ALLOWED_HTTP_METHODS - {'GET'})}",
            )
        try:
            normalized = _normalize_path(path)
            body = _parse_json_arg(body_json, "body_json")
            query_params = _parse_json_arg(query_params_json, "query_params_json")
        except ValueError as exc:
            return _error_json("invalid_request", str(exc))

        params = query_params if isinstance(query_params, dict) else None
        try:
            response_body = _get_client().request(method_u, normalized, params=params, json=body)
        except AtlasConfigError as exc:
            return _error_json("config_error", str(exc))
        except AtlasApiError as exc:
            return _error_json(
                "atlas_api_error",
                str(exc),
                status_code=exc.status,
                error_code=exc.error_code,
                detail=exc.detail,
                reason=exc.reason,
                parameters=exc.parameters,
                atlas_path=exc.url,
            )
        logger.info("atlas_execute %s %s succeeded", method_u, normalized)
        return _format_json(
            {
                "status": "ok",
                "method": method_u,
                "path": normalized,
                "body": response_body,
            }
        )

    @app.tool(is_local=True)
    def atlas_describe_endpoint(method: str, path: str) -> str:
        """Look up the Atlas Admin API v2 request-body schema for an endpoint.

        Call this BEFORE constructing a POST/PUT/PATCH body so you use the
        current field names. The spec is the canonical ``mongodb/openapi``
        file pinned to the exact API version this agent sends on the wire,
        so the schema returned here matches what Atlas will actually
        validate against.

        Args:
            method: HTTP method (GET/POST/PUT/PATCH/DELETE).
            path: Atlas path, with or without the ``/api/atlas/v2`` prefix.
                Either a concrete path (``/groups/64.../clusters``) or the
                template form (``/groups/{groupId}/clusters``) works.

        Returns:
            JSON with ``operationId``, ``summary``, ``method``, ``path``,
            ``resolved_content_type``, ``request_body_schema`` (inlined),
            ``parameters``, and ``spec_version``.
        """
        method_u = (method or "").upper().strip()
        if not method_u:
            return _error_json("invalid_request", "method is required")
        try:
            description = describe_endpoint(method_u, path)
        except SpecLookupError as exc:
            return _error_json("spec_lookup_error", str(exc))
        return _format_json(description)

    return {
        "list_organizations": list_organizations,
        "list_projects": list_projects,
        "get_project": get_project,
        "list_clusters": list_clusters,
        "get_cluster": get_cluster,
        "list_snapshots": list_snapshots,
        "list_database_users": list_database_users,
        "list_network_access_entries": list_network_access_entries,
        "list_alerts": list_alerts,
        "list_backup_restore_jobs": list_backup_restore_jobs,
        "atlas_describe_endpoint": atlas_describe_endpoint,
        "atlas_request": atlas_request,
        "atlas_execute": atlas_execute,
    }
