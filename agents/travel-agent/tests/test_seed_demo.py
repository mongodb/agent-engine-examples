from __future__ import annotations

import types

import pytest

from travel_agent import seed_demo

CANONICAL_PROCEDURE = "travel-disruption-reaccommodation-playbook"
LEGACY_SEED_PROCEDURE = "weather-cancellation-priority-reaccommodation"


def test_resolve_mongo_settings_defaults_to_local_memory_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MEMORY_DATABASE", raising=False)
    monkeypatch.delenv("MDB_AGENTIC_STORE_DB", raising=False)
    monkeypatch.delenv("MONGOMEM_DB_NAME", raising=False)

    _, memory_db = seed_demo._resolve_mongo_settings()

    assert memory_db == "agentic_memory"


def test_resolve_mongo_settings_uses_mongomem_database_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONGOMEM_DB_NAME", "platform_memory")

    _, memory_db = seed_demo._resolve_mongo_settings()

    assert memory_db == "platform_memory"


def test_resolve_mongo_settings_ignores_legacy_memory_database_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_DATABASE", "travel_agent_memory")
    monkeypatch.delenv("MONGOMEM_DB_NAME", raising=False)

    _, memory_db = seed_demo._resolve_mongo_settings()

    assert memory_db == "agentic_memory"


def test_load_memory_engine_classes_from_embedded_runner_shared() -> None:
    memory_engine, settings = seed_demo._load_memory_engine_classes()

    assert memory_engine.__name__ == "MemoryEngine"
    assert settings.__name__ == "Settings"


def test_load_memory_engine_classes_requires_embedded_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_import_module(module_name: str) -> object:
        if module_name == "runner_shared.server.mongomem_impl":
            return types.SimpleNamespace(__file__="/runner_shared/server/mongomem_impl.py")
        pytest.fail(f"unexpected import: {module_name}")

    monkeypatch.setattr(seed_demo.importlib, "import_module", fake_import_module)

    with pytest.raises(RuntimeError, match="mongomem_impl must be an embedded package"):
        seed_demo._load_memory_engine_classes()


def test_reset_seed_documents_removes_procedural_records_across_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCollection:
        def __init__(self) -> None:
            self.filters: list[dict[str, object]] = []

        def delete_many(self, query: dict[str, object]) -> types.SimpleNamespace:
            self.filters.append(query)
            return types.SimpleNamespace(deleted_count=1)

    class FakeDb:
        def __init__(self) -> None:
            self.collections: dict[str, FakeCollection] = {}

        def __getitem__(self, collection_name: str) -> FakeCollection:
            return self.collections.setdefault(collection_name, FakeCollection())

    class FakeClient:
        def __init__(self) -> None:
            self.db = FakeDb()

        def __getitem__(self, _db_name: str) -> FakeDb:
            return self.db

    monkeypatch.setattr(seed_demo, "_records", lambda *_args: [])
    monkeypatch.setattr(
        seed_demo,
        "_load_json",
        lambda filename: (
            [{"procedure": CANONICAL_PROCEDURE}]
            if filename == "procedural.json"
            else []
        ),
    )

    client = FakeClient()

    seed_demo._reset_seed_documents(
        client,
        "agentic_memory",
        "org-1",
        "current-project",
        "travel-agent",
    )

    procedural_deletes = client.db["memory_procedural"].filters
    assert procedural_deletes == [
        {
            "org_id": "org-1",
            "agent_id": "travel-agent",
            "procedure": {"$in": [CANONICAL_PROCEDURE]},
        }
    ]


def test_reset_seed_documents_removes_legacy_and_canonical_procedures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCollection:
        def __init__(self) -> None:
            self.filters: list[dict[str, object]] = []

        def delete_many(self, query: dict[str, object]) -> types.SimpleNamespace:
            self.filters.append(query)
            return types.SimpleNamespace(deleted_count=1)

    class FakeDb:
        def __init__(self) -> None:
            self.collections: dict[str, FakeCollection] = {}

        def __getitem__(self, collection_name: str) -> FakeCollection:
            return self.collections.setdefault(collection_name, FakeCollection())

    class FakeClient:
        def __init__(self) -> None:
            self.db = FakeDb()

        def __getitem__(self, _db_name: str) -> FakeDb:
            return self.db

    monkeypatch.setattr(seed_demo, "_records", lambda *_args: [])
    monkeypatch.setattr(
        seed_demo,
        "_load_json",
        lambda filename: (
            [{"procedure": LEGACY_SEED_PROCEDURE}]
            if filename == "procedural.json"
            else []
        ),
    )

    client = FakeClient()

    seed_demo._reset_seed_documents(
        client,
        "agentic_memory",
        "org-1",
        "current-project",
        "travel-agent",
    )

    procedural_filter = client.db["memory_procedural"].filters[0]
    assert procedural_filter == {
        "org_id": "org-1",
        "agent_id": "travel-agent",
        "procedure": {"$in": [CANONICAL_PROCEDURE, LEGACY_SEED_PROCEDURE]},
    }
