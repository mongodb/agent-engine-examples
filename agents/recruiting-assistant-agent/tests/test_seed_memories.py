from __future__ import annotations

from pathlib import Path

from recruiting_assistant_agent import seed_memories


def test_load_environment_prefers_env_dev_over_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(seed_memories, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("RECRUITING_ASSISTANT_ENV_FILE", raising=False)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MDB_AGENTIC_STORE_DB", raising=False)
    monkeypatch.delenv("MEMORY_DATABASE", raising=False)

    (tmp_path / ".env.dev").write_text(
        "MONGODB_URI=mongodb+srv://dev-cluster.example.mongodb.net\n"
        "MDB_AGENTIC_STORE_DB=dev_store\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "MONGODB_URI=mongodb://mongodb:27017\n"
        "MDB_AGENTIC_STORE_DB=default_store\n"
        "MEMORY_DATABASE=legacy_memory\n",
        encoding="utf-8",
    )

    seed_memories._load_environment()
    mongo_uri, memory_db = seed_memories._resolve_mongo_settings()

    assert mongo_uri == "mongodb+srv://dev-cluster.example.mongodb.net"
    assert memory_db == "dev_store"


def test_load_environment_preserves_exported_process_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(seed_memories, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("RECRUITING_ASSISTANT_ENV_FILE", raising=False)
    monkeypatch.setenv("MONGODB_URI", "mongodb+srv://exported-cluster.example.mongodb.net")
    monkeypatch.setenv("MDB_AGENTIC_STORE_DB", "exported_store")

    (tmp_path / ".env.dev").write_text(
        "MONGODB_URI=mongodb+srv://dev-cluster.example.mongodb.net\n"
        "MDB_AGENTIC_STORE_DB=dev_store\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "MONGODB_URI=mongodb://mongodb:27017\nMDB_AGENTIC_STORE_DB=default_store\n",
        encoding="utf-8",
    )

    seed_memories._load_environment()
    mongo_uri, memory_db = seed_memories._resolve_mongo_settings()

    assert mongo_uri == "mongodb+srv://exported-cluster.example.mongodb.net"
    assert memory_db == "exported_store"


def test_seed_records_use_runtime_org_id(monkeypatch) -> None:
    monkeypatch.setenv("ORG_ID", "507f1f77bcf86cd799439099")

    semantic = seed_memories._semantic_records()
    taxonomic = seed_memories._taxonomic_records()

    assert semantic
    assert taxonomic
    assert all(record["org_id"] == "507f1f77bcf86cd799439099" for record in semantic[:3])
    assert all(record["org_id"] == "507f1f77bcf86cd799439099" for record in taxonomic[:3])
