from __future__ import annotations

import pytest

from data_analyst_agent import seed_demo


def _stub_app_data_seed(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    calls: dict[str, object] = {}
    records = [{"customer_id": "CUST-1"}, {"customer_id": "CUST-2"}]

    def generate_records(*, scale: int) -> list[dict[str, str]]:
        calls["scale"] = scale
        return records

    class FakeStore:
        def __init__(self, *, mongodb_uri: str, database_name: str, records: list[dict[str, str]]):
            calls["store_init"] = {
                "mongodb_uri": mongodb_uri,
                "database_name": database_name,
                "records": records,
            }

        def seed(self, *, reset: bool, records: list[dict[str, str]]) -> None:
            calls["seed"] = {"reset": reset, "records": records}

        def close(self) -> None:
            calls["closed"] = True

    monkeypatch.setattr(seed_demo, "generate_customer_records", generate_records)
    monkeypatch.setattr(seed_demo, "DemoDataStore", FakeStore)
    return calls


def test_seed_demo_replaces_app_data_and_skips_memories_without_voyage_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://example")
    monkeypatch.setenv("MONGODB_DATABASE", "demo_db")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setattr(seed_demo, "_load_environment", lambda: None)
    calls = _stub_app_data_seed(monkeypatch)

    def fail_memory_seed() -> None:
        raise AssertionError("memory seeding should be skipped without VOYAGE_API_KEY")

    monkeypatch.setattr(seed_demo.seed_memories, "main", fail_memory_seed)

    seed_demo.main(["--scale", "3"])

    assert calls["scale"] == 3
    assert calls["store_init"] == {
        "mongodb_uri": "mongodb://example",
        "database_name": "demo_db",
        "records": [{"customer_id": "CUST-1"}, {"customer_id": "CUST-2"}],
    }
    assert calls["seed"] == {
        "reset": True,
        "records": [{"customer_id": "CUST-1"}, {"customer_id": "CUST-2"}],
    }
    assert calls["closed"] is True


def test_seed_demo_seeds_memories_when_voyage_key_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://example")
    monkeypatch.setenv("MONGOMEM_DB_NAME", "agentic_memory")
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-key")
    monkeypatch.delenv("MONGODB_DATABASE", raising=False)
    monkeypatch.setattr(seed_demo, "_load_environment", lambda: None)
    _stub_app_data_seed(monkeypatch)
    memory_calls: list[str] = []
    monkeypatch.setattr(seed_demo.seed_memories, "main", lambda: memory_calls.append("seeded"))

    seed_demo.main([])

    assert memory_calls == ["seeded"]


def test_seed_demo_requires_memory_key_before_writing_app_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://example")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setattr(seed_demo, "_load_environment", lambda: None)

    def fail_generate_records(*, scale: int) -> list[dict[str, str]]:
        raise AssertionError("app data should not be seeded when required memories cannot seed")

    monkeypatch.setattr(seed_demo, "generate_customer_records", fail_generate_records)

    with pytest.raises(SystemExit, match="VOYAGE_API_KEY is required"):
        seed_demo.main(["--require-memories"])


def test_seed_demo_requires_mongomem_db_name_before_writing_app_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://example")
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-key")
    monkeypatch.delenv("MONGOMEM_DB_NAME", raising=False)
    monkeypatch.setattr(seed_demo, "_load_environment", lambda: None)

    def fail_generate_records(*, scale: int) -> list[dict[str, str]]:
        raise AssertionError("app data should not be seeded when memory DB is not configured")

    monkeypatch.setattr(seed_demo, "generate_customer_records", fail_generate_records)

    with pytest.raises(SystemExit, match="MONGOMEM_DB_NAME is required"):
        seed_demo.main([])
