"""Named multi-step workflows for the Atlas Admin Agent.

v1 ships a single workflow: the snapshot-restore test, ported from
``atlas-mgmt-examples/test_snapshot_restore_for_project.py``.

Execution model:

1. ``run_snapshot_restore_test`` — reads the project's clusters, filters those
   that can be tested, returns a SuspendPayload describing the planned targets
   and reasons for any skipped clusters.
2. ``execute_snapshot_restore_test`` — called after approval. Creates a run
   document in MongoDB, kicks off a background thread that performs the
   phased port, and returns a handle immediately.
3. ``check_snapshot_restore_test(handle_id)`` — reads the run document.

The phased port (create targets → wait IDLE → start restore → poll completion
→ optional cleanup) and the target-cluster naming rules come directly from
the reference script.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from runner_shared.models import SuspendPayload  # type: ignore[import-untyped]

from atlas_admin_agent import workflow_store
from atlas_admin_agent.atlas_client import (
    AtlasApiError,
    AtlasClient,
    AtlasConfigError,
    from_env,
)

logger = logging.getLogger(__name__)

# Matches atlas-mgmt-examples/test_snapshot_restore_for_project.py constants.
BACKUP_CLUSTER_MARKER = "backup-test-job"
CLUSTER_NAME_MAX_LEN = 64
POLL_INTERVAL_SECONDS = 30.0
CLUSTER_READY_TIMEOUT_SECONDS = 60 * 60  # 1 hour
RESTORE_TIMEOUT_SECONDS = 60 * 60 * 4  # 4 hours
DEFAULT_MAX_RETRIES = 1


# --- naming + inspection helpers (ported from reference script) -----------


def _build_target_cluster_name(source_name: str, timestamp: str) -> str:
    """Derive a target cluster name that fits within Atlas's 64-char limit.

    Atlas names must be <= 64 chars and no two clusters in a project may share
    the first 23 characters — so the timestamp has to sit inside those first
    23 chars to survive repeat runs. Format: ``{source}-{ts}-{marker}``,
    truncating the source segment if the full name would overflow.
    """
    suffix = f"-{timestamp}-{BACKUP_CLUSTER_MARKER}"
    available = CLUSTER_NAME_MAX_LEN - len(suffix)
    trimmed = source_name[:available].rstrip("-") if available > 0 else ""
    return f"{trimmed}{suffix}"


def _is_backup_test_cluster(name: str | None) -> bool:
    if not name:
        return False
    return BACKUP_CLUSTER_MARKER in name or name.startswith("backup-test-job-")


def _build_target_cluster_body(source: dict[str, Any], target_name: str) -> dict[str, Any]:
    body = copy.deepcopy(source)
    for field in (
        "id",
        "groupId",
        "createDate",
        "stateName",
        "mongoDBVersion",
        "mongoDBEmployeeAccessGrant",
        "connectionStrings",
        "paused",
        "links",
        "replicaSetScalingStrategy",
        "featureCompatibilityVersion",
    ):
        body.pop(field, None)
    for spec in body.get("replicationSpecs", []) or []:
        spec.pop("id", None)
    body["name"] = target_name
    return body


def _snapshot_major_version(snapshot: dict[str, Any]) -> str | None:
    raw = snapshot.get("mongodVersion") or snapshot.get("mongoDBVersion")
    if not raw:
        return None
    parts = raw.split(".")
    if len(parts) < 2:
        return None
    return f"{parts[0]}.{parts[1]}"


def _cluster_instance_size(cluster: dict[str, Any]) -> str | None:
    for spec in cluster.get("replicationSpecs", []) or []:
        for region in spec.get("regionConfigs", []) or []:
            for key in ("electableSpecs", "readOnlySpecs", "analyticsSpecs"):
                size = (region.get(key) or {}).get("instanceSize")
                if size:
                    return size
    return None


def _explain_no_backups(cluster: dict[str, Any]) -> str:
    size = _cluster_instance_size(cluster)
    if not size:
        return "could not determine cluster tier"
    size_upper = size.upper()
    if size_upper == "M0":
        return "M0 free tier does not support Cloud Backup"
    if size_upper in ("M2", "M5"):
        return f"shared tier {size} does not support Cloud Backup"
    if size_upper == "FLEX":
        return "Flex clusters use a separate always-on backup system, not Cloud Backup"
    if size_upper == "SERVERLESS":
        return "Serverless instances are deprecated and no longer support backups"
    return f"tier {size} supports Cloud Backup but it is disabled (backupEnabled=false)"


# --- plan construction (runs inside run_snapshot_restore_test) ------------


def _build_plan(
    client: AtlasClient,
    project_id: str,
    max_retries: int,
) -> dict[str, Any]:
    """Inspect a project's clusters and produce the restore plan.

    Returns a dict with ``items`` (clusters that will be restored) and
    ``skipped`` (clusters that won't, with a reason).
    """
    clusters = list(client.paginate(f"/groups/{project_id}/clusters"))
    source_clusters = [c for c in clusters if not _is_backup_test_cluster(c.get("name"))]
    run_timestamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")

    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for cluster in source_clusters:
        name = cluster.get("name", "")
        if not cluster.get("backupEnabled", False):
            skipped.append({"source": name, "reason": _explain_no_backups(cluster)})
            continue

        try:
            snap_page = client.request(
                "GET",
                f"/groups/{project_id}/clusters/{name}/backup/snapshots",
                params={"itemsPerPage": 1, "pageNum": 1},
            )
        except AtlasApiError as exc:
            skipped.append({"source": name, "reason": f"failed to fetch snapshots: {exc}"})
            continue
        results = snap_page.get("results", []) or []
        if not results:
            skipped.append({"source": name, "reason": "backups enabled but no snapshots yet"})
            continue
        snapshot = results[0]

        cluster_version = cluster.get("mongoDBMajorVersion")
        snap_version = _snapshot_major_version(snapshot)
        if cluster_version and snap_version and cluster_version != snap_version:
            skipped.append(
                {
                    "source": name,
                    "reason": (
                        f"version mismatch (cluster {cluster_version}, "
                        f"snapshot {snap_version}); Atlas restore requires matching majors"
                    ),
                }
            )
            continue

        target_name = _build_target_cluster_name(name, run_timestamp)
        items.append(
            {
                "source_name": name,
                "target_name": target_name,
                "snapshot_id": snapshot.get("id"),
                "mongo_version": cluster_version or snap_version,
                "source_cluster": cluster,
                "status": "pending_create",
                "message": "",
                "target_created": False,
                "restore_job_id": None,
                "retries_remaining": max_retries,
            }
        )

    return {
        "run_timestamp": run_timestamp,
        "items": items,
        "skipped": skipped,
    }


# --- background runner ----------------------------------------------------


def _set_error(item: dict[str, Any], message: str) -> None:
    logger.error(message)
    item["status"] = "error"
    item["message"] = message


def _run_background(
    handle_id: str,
    project_id: str,
    cleanup: bool,
    max_retries: int,
    client_factory: Callable[[], AtlasClient] = from_env,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Execute all phases for an approved snapshot-restore test.

    Split out from ``execute_snapshot_restore_test`` so tests can call it
    synchronously with a fake client factory, fake sleep, and fake clock.
    """
    try:
        client = client_factory()
    except AtlasConfigError as exc:
        workflow_store.append_error(handle_id, f"config_error: {exc}")
        workflow_store.finish_run(handle_id, status="failed")
        return

    try:
        _run_phases(client, handle_id, project_id, cleanup, max_retries, sleep, monotonic)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Workflow %s failed", handle_id)
        workflow_store.append_error(handle_id, f"unexpected_error: {exc}")
        workflow_store.finish_run(handle_id, status="failed")
    finally:
        try:
            client.close()
        except Exception:
            logger.debug("Error closing Atlas client for workflow %s", handle_id, exc_info=True)


def _run_phases(
    client: AtlasClient,
    handle_id: str,
    project_id: str,
    cleanup: bool,
    max_retries: int,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> None:
    run = workflow_store.get_run(handle_id)
    if run is None:
        logger.error("Workflow run %s missing from store", handle_id)
        return
    items: list[dict[str, Any]] = list(run.get("items", []))

    # Phases 3-6, with retry on timeout (IDLE or restore).
    while any(i["status"] == "pending_create" for i in items):
        workflow_store.update_run(handle_id, {"phase": "create_targets", "items": items})
        _create_targets(client, items, project_id)
        workflow_store.update_run(handle_id, {"phase": "wait_idle", "items": items})
        idle_timeouts = _wait_for_targets_idle(client, items, project_id, sleep, monotonic)
        if idle_timeouts:
            _recover_timed_out(
                client,
                items,
                idle_timeouts,
                "Target cluster did not reach IDLE before timeout",
                project_id,
            )
            workflow_store.update_run(handle_id, {"items": items})
            continue

        workflow_store.update_run(handle_id, {"phase": "start_restore", "items": items})
        _start_restores(client, items, project_id)
        workflow_store.update_run(handle_id, {"phase": "wait_restore", "items": items})
        restore_timeouts = _poll_restores(client, items, project_id, sleep, monotonic)
        if restore_timeouts:
            _recover_timed_out(
                client,
                items,
                restore_timeouts,
                "Restore job did not finish before timeout",
                project_id,
            )
            workflow_store.update_run(handle_id, {"items": items})

    # Phase 7: optional cleanup of target clusters.
    if cleanup:
        workflow_store.update_run(handle_id, {"phase": "cleanup", "items": items})
        for item in items:
            if not item.get("target_created"):
                continue
            target = item["target_name"]
            try:
                client.request("DELETE", f"/groups/{project_id}/clusters/{target}")
            except AtlasApiError as exc:
                workflow_store.append_error(handle_id, f"cleanup failed for {target}: {exc}")

    successes = sum(1 for i in items if i["status"] == "success")
    errors = sum(1 for i in items if i["status"] == "error")
    status = (
        "succeeded"
        if errors == 0 and successes > 0
        else ("failed" if successes == 0 else "partial")
    )
    workflow_store.update_run(handle_id, {"items": items})
    workflow_store.finish_run(handle_id, status=status)


def _create_targets(client: AtlasClient, items: list[dict[str, Any]], project_id: str) -> None:
    to_create = [i for i in items if i["status"] == "pending_create"]
    for item in to_create:
        body = _build_target_cluster_body(item["source_cluster"], item["target_name"])
        try:
            client.request("POST", f"/groups/{project_id}/clusters", json=body)
        except AtlasApiError as exc:
            _set_error(item, f"failed to create target cluster {item['target_name']}: {exc}")
            continue
        item["target_created"] = True
        item["status"] = "creating"


def _wait_for_targets_idle(
    client: AtlasClient,
    items: list[dict[str, Any]],
    project_id: str,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float] = time.monotonic,
) -> list[dict[str, Any]]:
    waiting = [i for i in items if i["status"] == "creating"]
    deadline = monotonic() + CLUSTER_READY_TIMEOUT_SECONDS
    while waiting and monotonic() < deadline:
        still = []
        for item in waiting:
            try:
                cluster = client.request(
                    "GET", f"/groups/{project_id}/clusters/{item['target_name']}"
                )
            except AtlasApiError as exc:
                _set_error(
                    item,
                    f"failed to fetch state for {item['target_name']}: {exc}",
                )
                continue
            if cluster.get("stateName") == "IDLE":
                item["status"] = "ready_for_restore"
            else:
                still.append(item)
        waiting = still
        if waiting:
            sleep(POLL_INTERVAL_SECONDS)
    return waiting


def _start_restores(client: AtlasClient, items: list[dict[str, Any]], project_id: str) -> None:
    to_restore = [i for i in items if i["status"] == "ready_for_restore"]
    for item in to_restore:
        body = {
            "deliveryType": "automated",
            "snapshotId": item["snapshot_id"],
            "targetGroupId": project_id,
            "targetClusterName": item["target_name"],
        }
        try:
            job = client.request(
                "POST",
                f"/groups/{project_id}/clusters/{item['source_name']}/backup/restoreJobs",
                json=body,
            )
        except AtlasApiError as exc:
            _set_error(
                item,
                f"failed to start restore for {item['source_name']}: {exc}",
            )
            continue
        item["restore_job_id"] = job.get("id")
        item["status"] = "restoring"


def _poll_restores(
    client: AtlasClient,
    items: list[dict[str, Any]],
    project_id: str,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float] = time.monotonic,
) -> list[dict[str, Any]]:
    restoring = [i for i in items if i["status"] == "restoring"]
    deadline = monotonic() + RESTORE_TIMEOUT_SECONDS
    while restoring and monotonic() < deadline:
        still = []
        for item in restoring:
            try:
                job = client.request(
                    "GET",
                    f"/groups/{project_id}/clusters/{item['source_name']}"
                    f"/backup/restoreJobs/{item['restore_job_id']}",
                )
            except AtlasApiError as exc:
                _set_error(
                    item,
                    f"failed to poll restore {item['restore_job_id']}: {exc}",
                )
                continue
            if _restore_terminal(job):
                if _restore_succeeded(job):
                    item["status"] = "success"
                    item["message"] = (
                        f"restore succeeded for {item['source_name']} -> {item['target_name']}"
                    )
                else:
                    _set_error(
                        item,
                        f"restore failed for {item['source_name']}: "
                        f"cancelled={job.get('cancelled')} "
                        f"expired={job.get('expired')} "
                        f"failed={job.get('failed')}",
                    )
                continue
            still.append(item)
        restoring = still
        if restoring:
            sleep(POLL_INTERVAL_SECONDS)
    return restoring


def _restore_terminal(job: dict[str, Any]) -> bool:
    return bool(
        job.get("finishedAt") or job.get("failed") or job.get("cancelled") or job.get("expired")
    )


def _restore_succeeded(job: dict[str, Any]) -> bool:
    return (
        bool(job.get("finishedAt"))
        and not job.get("failed")
        and not job.get("cancelled")
        and not job.get("expired")
    )


def _recover_timed_out(
    client: AtlasClient,
    items: list[dict[str, Any]],
    timed_out: list[dict[str, Any]],
    reason: str,
    project_id: str,
) -> None:
    for item in timed_out:
        target = item["target_name"]
        try:
            client.request("DELETE", f"/groups/{project_id}/clusters/{target}")
        except AtlasApiError as exc:
            logger.error("failed to delete stuck target %s: %s", target, exc)
        item["target_created"] = False
        item["restore_job_id"] = None
        if item["retries_remaining"] > 0:
            item["retries_remaining"] -= 1
            item["status"] = "pending_create"
        else:
            _set_error(item, f"{reason} for {item['source_name']}; out of retries")


# --- public tool surface --------------------------------------------------


def _serialize_plan_for_review(plan: dict[str, Any]) -> dict[str, Any]:
    """Strip the bulky ``source_cluster`` payloads before showing a plan to the reviewer."""
    return {
        "items": [
            {
                "source_name": i["source_name"],
                "target_name": i["target_name"],
                "snapshot_id": i["snapshot_id"],
                "mongo_version": i["mongo_version"],
            }
            for i in plan["items"]
        ],
        "skipped": plan["skipped"],
        "run_timestamp": plan["run_timestamp"],
    }


def register_workflows(app: Any) -> dict[str, Any]:
    """Register workflow tools on the given magenta ``App``."""

    @app.tool(is_local=True)
    def run_snapshot_restore_test(
        project_id: str,
        cleanup: bool = False,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> str:
        """Plan a snapshot-restore test for all eligible clusters in a project.

        Does NOT touch Atlas beyond reads. Returns a SuspendPayload summarizing
        the planned restore targets and any skipped clusters. After approval,
        call ``execute_snapshot_restore_test`` with the same arguments to kick
        off the background run.
        """
        if not project_id:
            return json.dumps({"status": "error", "message": "project_id is required"}, indent=2)

        try:
            client = from_env()
        except AtlasConfigError as exc:
            return json.dumps({"status": "error", "message": str(exc)}, indent=2)

        try:
            plan = _build_plan(client, project_id, max_retries)
        except AtlasApiError as exc:
            return json.dumps(
                {
                    "status": "error",
                    "message": f"failed to build plan: {exc}",
                    "atlas_path": exc.url,
                },
                indent=2,
            )
        finally:
            client.close()

        task_id = f"WFPLAN-{uuid.uuid4().hex[:8].upper()}"
        return SuspendPayload(
            suspend_reason="snapshot_restore_test_approval",
            suspend_context={
                "task_id": task_id,
                "decision_type": "snapshot_restore_workflow",
                "project_id": project_id,
                "cleanup": cleanup,
                "max_retries": max_retries,
                "estimated_runtime_hours": "1-4",
                "plan": _serialize_plan_for_review(plan),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "instructions": (
                    "Review the planned restore targets. Approving kicks off the "
                    "workflow in the background; progress can be checked with "
                    "check_snapshot_restore_test."
                ),
            },
        ).to_json()

    @app.tool(is_local=True)
    def execute_snapshot_restore_test(
        project_id: str,
        cleanup: bool = False,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> str:
        """Kick off an APPROVED snapshot-restore test in the background.

        Returns a handle ID immediately. Use ``check_snapshot_restore_test``
        with that handle to see progress. Do NOT call without approval.
        """
        if not project_id:
            return json.dumps({"status": "error", "message": "project_id is required"}, indent=2)

        try:
            client = from_env()
        except AtlasConfigError as exc:
            return json.dumps({"status": "error", "message": str(exc)}, indent=2)

        try:
            plan = _build_plan(client, project_id, max_retries)
        except AtlasApiError as exc:
            return json.dumps(
                {"status": "error", "message": f"failed to build plan: {exc}"},
                indent=2,
            )
        finally:
            client.close()

        handle_id = workflow_store.create_run(
            "snapshot_restore_test",
            project_id=project_id,
            plan=plan,
        )

        thread = threading.Thread(
            target=_run_background,
            args=(handle_id, project_id, cleanup, max_retries),
            name=f"snapshot-restore-{handle_id}",
            daemon=True,
        )
        thread.start()

        return json.dumps(
            {
                "status": "running",
                "handle_id": handle_id,
                "items_planned": len(plan["items"]),
                "items_skipped": len(plan["skipped"]),
                "message": (
                    "Workflow started in the background. Use check_snapshot_restore_test "
                    "with this handle to poll progress."
                ),
            },
            indent=2,
        )

    @app.tool(is_local=True)
    def check_snapshot_restore_test(handle_id: str) -> str:
        """Return the current state of a snapshot-restore workflow run."""
        run = workflow_store.get_run(handle_id)
        if run is None:
            return json.dumps(
                {"status": "error", "message": f"no workflow run with handle {handle_id}"},
                indent=2,
            )
        items = run.get("items", [])
        summary = {
            "handle_id": handle_id,
            "status": run.get("status"),
            "phase": run.get("phase"),
            "project_id": run.get("project_id"),
            "counts": {
                "total": len(items),
                "success": sum(1 for i in items if i.get("status") == "success"),
                "error": sum(1 for i in items if i.get("status") == "error"),
                "in_progress": sum(
                    1
                    for i in items
                    if i.get("status")
                    in ("pending_create", "creating", "ready_for_restore", "restoring")
                ),
            },
            "items": [
                {
                    "source_name": i.get("source_name"),
                    "target_name": i.get("target_name"),
                    "status": i.get("status"),
                    "message": i.get("message", ""),
                }
                for i in items
            ],
            "skipped": run.get("skipped", []),
            "errors": run.get("errors", []),
            "started_at": run.get("started_at"),
            "updated_at": run.get("updated_at"),
            "finished_at": run.get("finished_at"),
        }
        return json.dumps(summary, indent=2, default=str)

    return {
        "run_snapshot_restore_test": run_snapshot_restore_test,
        "execute_snapshot_restore_test": execute_snapshot_restore_test,
        "check_snapshot_restore_test": check_snapshot_restore_test,
    }


__all__ = [
    "BACKUP_CLUSTER_MARKER",
    "CLUSTER_NAME_MAX_LEN",
    "DEFAULT_MAX_RETRIES",
    "register_workflows",
    "_build_plan",
    "_build_target_cluster_body",
    "_build_target_cluster_name",
    "_is_backup_test_cluster",
    "_run_background",
]
