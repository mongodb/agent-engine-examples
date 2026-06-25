from __future__ import annotations

from data_analyst_agent.data_store import (
    CUSTOMER_COLLECTION,
    DEFAULT_DATABASE,
    DemoDataStore,
    build_cohort_mix_pipeline,
    build_loss_frequency_pipeline,
    generate_customer_records,
)


def test_generated_customer_fixture_matches_demo_collection_shape() -> None:
    records = generate_customer_records(scale=1)
    record = records[0]

    assert records
    assert {record["vehicle"]["pedal_count"] for record in records} == {1, 2, 3}
    assert all(record["book_quarters"] for record in records)
    assert all("narrative_summary" in record for record in records)
    assert all("loss_history" in record for record in records)
    assert "policy_id" in record
    assert "customer" in record
    assert {"ID", "age", "zip"} <= set(record["customer"])
    assert "annual_mileage" in record["vehicle"]
    assert isinstance(record["book_quarters"][0], str)
    assert "telematics" in record
    assert "ltv" in record
    assert "current_factors" in record
    assert record["agent_log"] == []


def test_demo_data_store_returns_loss_frequency_and_mix_rows() -> None:
    store = DemoDataStore(records=generate_customer_records(scale=1))

    loss_rows = store.compare_loss_frequency()
    mix_rows = store.cohort_mix_over_time()

    assert [row["pedal_count"] for row in loss_rows] == [1, 2, 3]
    assert all(
        row["label"].endswith("pedal") or row["label"].endswith("pedals") for row in loss_rows
    )
    assert all("loss_frequency_per_1000" in row for row in loss_rows)
    assert {row["pedal_count"] for row in mix_rows} == {1, 2, 3}
    assert all("share_pct" in row for row in mix_rows)


def test_pipeline_builders_target_customer_collection_fields() -> None:
    loss_pipeline = build_loss_frequency_pipeline()
    mix_pipeline = build_cohort_mix_pipeline()

    assert CUSTOMER_COLLECTION == "Customer"
    assert loss_pipeline[0]["$match"]["vehicle.pedal_count"]["$in"] == [1, 2, 3]
    assert "customer.age" in loss_pipeline[0]["$match"]
    assert "customer.zip" in loss_pipeline[0]["$match"]
    assert "vehicle.annual_mileage" in loss_pipeline[0]["$match"]
    assert any(
        "$unwind" in stage and stage["$unwind"] == "$book_quarters" for stage in mix_pipeline
    )
    assert mix_pipeline[2]["$group"]["_id"]["quarter"] == "$book_quarters"


def test_default_database_matches_local_docs() -> None:
    assert DEFAULT_DATABASE == "data_analyst_agent_local"


def test_write_audit_log_skips_mongo_when_customer_data_is_not_seeded() -> None:
    class UnseededStore(DemoDataStore):
        def _has_mongo_data(self) -> bool:
            return False

        def _collection(self, name: str):  # type: ignore[no-untyped-def]
            raise AssertionError("audit write should not touch MongoDB without Customer data")

    store = UnseededStore(mongodb_uri="mongodb://localhost:27017")

    document = store.write_audit_log({"decision": "approved"})

    assert document["decision"] == "approved"
    assert "_id" not in document


def test_has_mongo_data_logs_connectivity_failures(caplog, monkeypatch) -> None:
    store = DemoDataStore(mongodb_uri="mongodb://localhost:27017")

    def broken_collection(name: str):  # type: ignore[no-untyped-def]
        raise RuntimeError(f"{name} unavailable")

    monkeypatch.setattr(store, "_collection", broken_collection)

    assert store._has_mongo_data() is False
    assert "MongoDB Customer data check failed" in caplog.text
