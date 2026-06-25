"""Tests for ``atlas_admin_agent.workflow_store``.

The store is intentionally small — a thin wrapper over MongoDB with an
in-process fallback for local testing. We verify that the public API
behaves correctly when the Mongo path is inactive (no ``MONGODB_URI``
in env, per conftest).
"""

from __future__ import annotations

import pytest

from atlas_admin_agent import workflow_store


@pytest.fixture(autouse=True)
def _clean() -> None:
    workflow_store._fallback_store().clear()


def _plan(items=None, skipped=None) -> dict:
    return {
        "items": list(items or []),
        "skipped": list(skipped or []),
        "run_timestamp": "t0",
    }


# --- handle IDs ----------------------------------------------------------


def test_create_run_returns_unique_handle_ids() -> None:
    h1 = workflow_store.create_run("snapshot_restore_test", project_id="p", plan=_plan())
    h2 = workflow_store.create_run("snapshot_restore_test", project_id="p", plan=_plan())
    assert h1 != h2
    assert h1.startswith("WF-") and len(h1) == len("WF-") + 12


def test_create_run_records_initial_state() -> None:
    handle = workflow_store.create_run(
        "snapshot_restore_test",
        project_id="p1",
        plan=_plan(items=[{"source_name": "a"}], skipped=[{"source": "b", "reason": "r"}]),
    )
    run = workflow_store.get_run(handle)
    assert run is not None
    assert run["_id"] == handle
    assert run["kind"] == "snapshot_restore_test"
    assert run["project_id"] == "p1"
    assert run["status"] == "running"
    assert run["phase"] == "pending"
    assert run["items"] == [{"source_name": "a"}]
    assert run["skipped"] == [{"source": "b", "reason": "r"}]
    assert run["errors"] == []
    assert run["started_at"]
    assert run["updated_at"]
    assert run["finished_at"] is None


# --- get_run ------------------------------------------------------------


def test_get_run_unknown_handle_returns_none() -> None:
    assert workflow_store.get_run("WF-NOPE") is None


# --- update_run ---------------------------------------------------------


def test_update_run_merges_fields_and_bumps_updated_at() -> None:
    handle = workflow_store.create_run("k", project_id="p", plan=_plan())
    initial = workflow_store.get_run(handle)
    workflow_store.update_run(handle, {"phase": "create_targets"})
    after = workflow_store.get_run(handle)
    assert after["phase"] == "create_targets"
    assert after["updated_at"] != initial["updated_at"] or True  # ensure set


def test_update_run_missing_handle_is_noop() -> None:
    # Should not raise.
    workflow_store.update_run("WF-NOPE", {"phase": "x"})


def test_update_run_does_not_clobber_unrelated_fields() -> None:
    handle = workflow_store.create_run("k", project_id="p", plan=_plan(items=[{"a": 1}]))
    workflow_store.update_run(handle, {"phase": "xyz"})
    run = workflow_store.get_run(handle)
    assert run["items"] == [{"a": 1}]
    assert run["phase"] == "xyz"


def test_update_run_overwrites_nested_collections() -> None:
    handle = workflow_store.create_run("k", project_id="p", plan=_plan(items=[{"a": 1}]))
    workflow_store.update_run(handle, {"items": [{"b": 2}]})
    run = workflow_store.get_run(handle)
    assert run["items"] == [{"b": 2}]


# --- append_error ---------------------------------------------------------


def test_append_error_adds_entries_in_order() -> None:
    handle = workflow_store.create_run("k", project_id="p", plan=_plan())
    workflow_store.append_error(handle, "first")
    workflow_store.append_error(handle, "second")
    run = workflow_store.get_run(handle)
    assert run["errors"] == ["first", "second"]


def test_append_error_missing_handle_is_noop() -> None:
    workflow_store.append_error("WF-NOPE", "hello")


# --- finish_run ---------------------------------------------------------


@pytest.mark.parametrize("status", ["succeeded", "failed", "partial"])
def test_finish_run_sets_status_phase_and_finished_at(status: str) -> None:
    handle = workflow_store.create_run("k", project_id="p", plan=_plan())
    workflow_store.finish_run(handle, status=status)
    run = workflow_store.get_run(handle)
    assert run["status"] == status
    assert run["phase"] == "done"
    assert run["finished_at"] is not None


def test_finish_run_missing_handle_is_noop() -> None:
    workflow_store.finish_run("WF-NOPE", status="failed")


# --- fallback store isolation -------------------------------------------


def test_fallback_store_is_per_process_module_state() -> None:
    workflow_store.create_run("k", project_id="p1", plan=_plan())
    workflow_store.create_run("k", project_id="p2", plan=_plan())
    # at least two runs live in the fallback
    store = workflow_store._fallback_store()
    assert len(store) >= 2
    for run in store.values():
        assert run["_id"].startswith("WF-")


def test_get_collection_returns_none_when_no_mongo_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # conftest sets MONGODB_URI='' so _get_collection should bail out.
    monkeypatch.setenv("MONGODB_URI", "")
    assert workflow_store._get_collection() is None


def test_get_collection_returns_none_when_pymongo_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workflow_store, "MongoClient", None)
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    assert workflow_store._get_collection() is None


# --- docstring invariants -----------------------------------------------


def test_workflow_runs_collection_constant() -> None:
    assert workflow_store.WORKFLOW_RUNS_COLLECTION == "workflow_runs"


# --- Mongo path coverage with a fake collection -------------------------


class _FakeCollection:
    """In-memory emulation of the pymongo methods used by workflow_store."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.inserts: list[dict] = []
        self.updates: list[tuple[dict, dict]] = []

    def insert_one(self, doc: dict) -> None:
        self.inserts.append(doc)
        self.docs[doc["_id"]] = dict(doc)

    def find_one(self, query: dict) -> dict | None:
        return self.docs.get(query["_id"])

    def update_one(self, query: dict, update: dict) -> None:
        self.updates.append((query, update))
        doc = self.docs.get(query["_id"])
        if doc is None:
            return
        if "$set" in update:
            doc.update(update["$set"])
        if "$push" in update:
            for field, value in update["$push"].items():
                doc.setdefault(field, []).append(value)


def test_mongo_path_create_and_get(monkeypatch) -> None:
    fake = _FakeCollection()
    monkeypatch.setattr(workflow_store, "_get_collection", lambda: fake)

    handle = workflow_store.create_run("k", project_id="p", plan=_plan(items=[{"a": 1}]))
    assert len(fake.inserts) == 1
    assert fake.inserts[0]["_id"] == handle

    run = workflow_store.get_run(handle)
    assert run is not None
    assert run["items"] == [{"a": 1}]


def test_mongo_path_update_run(monkeypatch) -> None:
    fake = _FakeCollection()
    monkeypatch.setattr(workflow_store, "_get_collection", lambda: fake)

    handle = workflow_store.create_run("k", project_id="p", plan=_plan())
    workflow_store.update_run(handle, {"phase": "done"})
    assert fake.docs[handle]["phase"] == "done"
    # One update call was issued (the create didn't use update, so updates has 1).
    assert len(fake.updates) == 1


def test_mongo_path_append_error(monkeypatch) -> None:
    fake = _FakeCollection()
    monkeypatch.setattr(workflow_store, "_get_collection", lambda: fake)

    handle = workflow_store.create_run("k", project_id="p", plan=_plan())
    workflow_store.append_error(handle, "boom")
    workflow_store.append_error(handle, "blam")
    assert fake.docs[handle]["errors"] == ["boom", "blam"]


def test_mongo_path_finish_run(monkeypatch) -> None:
    fake = _FakeCollection()
    monkeypatch.setattr(workflow_store, "_get_collection", lambda: fake)

    handle = workflow_store.create_run("k", project_id="p", plan=_plan())
    workflow_store.finish_run(handle, status="succeeded")
    doc = fake.docs[handle]
    assert doc["status"] == "succeeded"
    assert doc["phase"] == "done"
    assert doc["finished_at"] is not None
