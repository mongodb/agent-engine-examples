"""MongoDB-backed store for long-running workflow runs.

Workflows like the snapshot-restore test can take hours. They run in a
background thread; the main agent turn returns a handle immediately and the
reviewer (or the agent in a later turn) polls via ``check_snapshot_restore_test``.

This module owns the ``workflow_runs`` collection. It is intentionally small:
just create / get / update / list helpers.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    from pymongo import MongoClient  # type: ignore[import-not-found]
    from pymongo.collection import Collection  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - pymongo is a hard dependency in pyproject
    MongoClient = None  # type: ignore[assignment]
    Collection = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

WORKFLOW_RUNS_COLLECTION = "workflow_runs"

_lock = threading.Lock()
_client: Any = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_collection() -> Any:
    """Return the MongoDB collection used for workflow state, or None if unconfigured."""
    if MongoClient is None:
        return None
    mongo_uri = os.environ.get("MONGODB_URI", "").strip()
    db_name = os.environ.get("MONGODB_DATABASE", "atlas_admin_agent").strip() or "atlas_admin_agent"
    if not mongo_uri:
        return None
    global _client
    with _lock:
        if _client is None:
            _client = MongoClient(mongo_uri)
    return _client[db_name][WORKFLOW_RUNS_COLLECTION]


def _fallback_store() -> dict[str, dict[str, Any]]:
    """In-process fallback used when MONGODB_URI is not configured.

    Kept for local testing without a Mongo instance; production/platform
    deployments always provide MONGODB_URI.
    """
    if not hasattr(_fallback_store, "_store"):
        _fallback_store._store = {}  # type: ignore[attr-defined]
    return _fallback_store._store  # type: ignore[attr-defined,return-value]


def create_run(kind: str, *, project_id: str, plan: dict[str, Any]) -> str:
    """Create a new run document and return its handle ID."""
    handle_id = f"WF-{uuid.uuid4().hex[:12].upper()}"
    now = _utcnow_iso()
    doc = {
        "_id": handle_id,
        "kind": kind,
        "project_id": project_id,
        "status": "running",
        "phase": "pending",
        "plan": plan,
        "items": plan.get("items", []),
        "skipped": plan.get("skipped", []),
        "errors": [],
        "started_at": now,
        "updated_at": now,
        "finished_at": None,
    }
    coll = _get_collection()
    if coll is not None:
        coll.insert_one(doc)
    else:
        _fallback_store()[handle_id] = doc
    return handle_id


def get_run(handle_id: str) -> dict[str, Any] | None:
    coll = _get_collection()
    if coll is not None:
        return coll.find_one({"_id": handle_id})
    return _fallback_store().get(handle_id)


def update_run(handle_id: str, updates: dict[str, Any]) -> None:
    updates = {**updates, "updated_at": _utcnow_iso()}
    coll = _get_collection()
    if coll is not None:
        coll.update_one({"_id": handle_id}, {"$set": updates})
    else:
        run = _fallback_store().get(handle_id)
        if run is not None:
            run.update(updates)


def append_error(handle_id: str, message: str) -> None:
    coll = _get_collection()
    if coll is not None:
        coll.update_one(
            {"_id": handle_id},
            {"$push": {"errors": message}, "$set": {"updated_at": _utcnow_iso()}},
        )
        return
    run = _fallback_store().get(handle_id)
    if run is not None:
        run.setdefault("errors", []).append(message)
        run["updated_at"] = _utcnow_iso()


def finish_run(handle_id: str, *, status: str) -> None:
    now = _utcnow_iso()
    update_run(handle_id, {"status": status, "phase": "done", "finished_at": now})
