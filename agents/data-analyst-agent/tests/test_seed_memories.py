from __future__ import annotations

from pathlib import Path

from data_analyst_agent import seed_memories

seed_memories._ensure_mongomem_core_import_path()
from mongomem_core.models.memory.procedural import ProceduralStep


def test_seed_memory_loaders_cover_all_memory_types() -> None:
    semantic = seed_memories._semantic_records()
    taxonomic = seed_memories._taxonomic_records()
    episodic = seed_memories._episodic_records()
    procedural = seed_memories._procedural_records()

    assert semantic[0]["label"] == "plan-to-pipeline:cohort-comparison-by-pedal-count"
    assert semantic[0]["user_id"] == "local-dev-user"
    assert any(record["term"] == "pedal count" for record in taxonomic)
    assert episodic == []
    assert procedural[0]["procedure"] == "pedal-cohort-pricing-review"
    assert [step["description"].split(" | ", maxsplit=1)[0] for step in procedural[0]["steps"]] == [
        "handler:analytics_flow",
        "handler:investigation_flow",
        "handler:interpretation_flow",
    ]


def test_memory_env_prefers_data_analyst_specific_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(seed_memories, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("DATA_ANALYST_ENV_FILE", raising=False)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGOMEM_DB_NAME", raising=False)

    (tmp_path / ".env.dev").write_text(
        "MONGODB_URI=mongodb+srv://data-analyst.example.mongodb.net\n"
        "MONGOMEM_DB_NAME=data_analyst_memory\n",
        encoding="utf-8",
    )

    seed_memories._load_environment()

    assert seed_memories._resolve_mongo_settings() == (
        "mongodb+srv://data-analyst.example.mongodb.net",
        "data_analyst_memory",
    )


def test_memory_db_requires_mongomem_server_database_env(monkeypatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.delenv("MONGOMEM_DB_NAME", raising=False)

    try:
        seed_memories._resolve_mongo_settings()
    except SystemExit as exc:
        assert str(exc) == "MONGOMEM_DB_NAME is required to seed demo memories"
    else:
        raise AssertionError("Expected MONGOMEM_DB_NAME to be required")


def test_mongomem_core_import_path_uses_runner_shared_vendored_source(monkeypatch) -> None:
    import sys

    monkeypatch.setattr(
        sys,
        "path",
        [path for path in sys.path if "runner_shared/server/mongomem_impl/src" not in path],
    )

    seed_memories._ensure_mongomem_core_import_path()

    assert "runner_shared/server/mongomem_impl/src" in Path(sys.path[0]).as_posix()


def test_seed_records_override_embedded_identity_with_runtime_identity(monkeypatch) -> None:
    monkeypatch.setenv("ORG_ID", "org-runtime")
    monkeypatch.setenv("PROJECT_ID", "project-runtime")
    monkeypatch.setenv("WORKSPACE_ID", "agent-runtime")
    monkeypatch.setenv("DEFAULT_USER_ID", "user-runtime")

    records = [
        *seed_memories._semantic_records(),
        *seed_memories._taxonomic_records(),
        *seed_memories._episodic_records(),
        *seed_memories._procedural_records(),
    ]

    assert records
    for record in records:
        assert record["org_id"] == "org-runtime"
        assert record["project_id"] == "project-runtime"
        assert record["agent_id"] == "agent-runtime"
        assert record["user_id"] == "user-runtime"


def test_seed_procedural_steps_match_mongomem_input_model() -> None:
    for record in seed_memories._procedural_records():
        for step in record["steps"]:
            ProceduralStep(**step)


def test_memory_reset_scopes_deletes_to_seed_identity(monkeypatch) -> None:
    class DeleteResult:
        deleted_count = 0

    class FakeCollection:
        def __init__(self) -> None:
            self.filters: list[dict[str, object]] = []

        def delete_many(self, filter_spec: dict[str, object]) -> DeleteResult:
            self.filters.append(filter_spec)
            return DeleteResult()

    collections = {
        "memory_semantic": FakeCollection(),
        "memory_taxonomic": FakeCollection(),
        "memory_episodic": FakeCollection(),
        "memory_procedural": FakeCollection(),
    }

    class FakeClient:
        def __getitem__(self, name: str) -> dict[str, FakeCollection]:
            return collections

    monkeypatch.setenv("ORG_ID", "org-1")
    monkeypatch.setenv("PROJECT_ID", "project-1")
    monkeypatch.setenv("WORKSPACE_ID", "agent-1")
    monkeypatch.setenv("DEFAULT_USER_ID", "user-1")

    seed_memories._reset_seed_documents(FakeClient(), "memory-db")  # type: ignore[arg-type]

    identity_filter = {
        "org_id": "org-1",
        "project_id": "project-1",
        "agent_id": "agent-1",
        "user_id": "user-1",
    }
    for collection in collections.values():
        for filter_spec in collection.filters:
            assert identity_filter.items() <= filter_spec.items()


def test_env_example_documents_memory_identity_variables() -> None:
    env_example = Path(__file__).resolve().parent.parent / "env.example"
    content = env_example.read_text(encoding="utf-8")

    for variable in ("ORG_ID=", "PROJECT_ID=", "WORKSPACE_ID=", "DEFAULT_USER_ID="):
        assert variable in content
