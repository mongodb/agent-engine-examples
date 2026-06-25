"""Synthetic Customer data and MongoDB access for the data analyst demo."""

from __future__ import annotations

import json
import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CUSTOMER_COLLECTION = "Customer"
AUDIT_COLLECTION = "audit_log"
CATALOGS_COLLECTION = "catalogs"
DEFAULT_DATABASE = "data_analyst_agent_local"
logger = logging.getLogger(__name__)
BOOK_QUARTERS = [
    "2024-Q1",
    "2024-Q2",
    "2024-Q3",
    "2024-Q4",
    "2025-Q1",
    "2025-Q2",
    "2025-Q3",
    "2025-Q4",
]


def pedal_label(pedal_count: int) -> str:
    return f"{pedal_count} pedal" if pedal_count == 1 else f"{pedal_count} pedals"


def generate_customer_records(*, scale: int = 2) -> list[dict[str, Any]]:
    """Generate deterministic Customer records in the old demo's nested shape."""
    scale = max(scale, 1)
    mix_by_quarter = {
        1: [18, 16, 14, 12, 10, 8, 7, 6],
        2: [12, 12, 12, 12, 12, 12, 12, 12],
        3: [6, 8, 10, 12, 14, 16, 17, 18],
    }
    claim_modulus = {1: 18, 2: 14, 3: 10}
    records: list[dict[str, Any]] = []

    for quarter_index, quarter in enumerate(BOOK_QUARTERS):
        for pedal_count, counts in mix_by_quarter.items():
            cohort_size = counts[quarter_index] * scale
            for index in range(cohort_size):
                ordinal = len(records) + 1
                age = 28 + ((index + quarter_index + pedal_count * 3) % 42)
                zip_code = ["10001", "11215", "60614", "94107", "98103"][
                    (index + pedal_count + quarter_index) % 5
                ]
                annual_mileage = 7200 + ((index * 420 + pedal_count * 900) % 14200)
                premium = round(880 + annual_mileage * 0.018 + age * 1.8 + pedal_count * 75, 2)
                has_claim = (index + quarter_index * 2 + pedal_count) % claim_modulus[
                    pedal_count
                ] == 0
                severe_claim = pedal_count == 3 and (index + quarter_index) % 23 == 0
                loss_history = []
                if has_claim or severe_claim:
                    severity = "severe" if severe_claim else "minor"
                    amount = (
                        11250 if severe_claim else 1400 + pedal_count * 250 + quarter_index * 80
                    )
                    loss_history.append(
                        {
                            "claim_id": f"CLM-{quarter}-{pedal_count}-{index:03d}",
                            "date": _quarter_start_date(quarter),
                            "type": "collision",
                            "quarter": quarter,
                            "amount": amount,
                            "severity": severity,
                            "at_fault": pedal_count != 1,
                            "adjuster_narrative": _claim_narrative(pedal_count, severity),
                        }
                    )

                records.append(
                    {
                        "policy_id": f"ALL-AUTO-{ordinal:07d}",
                        "customer": {
                            "ID": f"C{ordinal:06d}",
                            "age": age,
                            "zip": zip_code,
                        },
                        "policy_start_date": _quarter_start_date(quarter),
                        "vehicle": {
                            "pedal_count": pedal_count,
                            "make": _vehicle_make(pedal_count),
                            "model": _vehicle_model(pedal_count),
                            "year": 2018 + ((index + pedal_count) % 7),
                            "transmission": _vehicle_transmission(pedal_count),
                            "annual_mileage": annual_mileage,
                        },
                        "book_quarters": [quarter],
                        "telematics": {
                            "arity_score": 84 - pedal_count * 4 + (index % 7),
                            "hard_brakes_90d": pedal_count + (index % 5),
                            "night_driving_pct": round(0.04 + pedal_count * 0.02, 2),
                            "miles_90d": int(annual_mileage / 4),
                        },
                        "ltv": {
                            "tenure_months": 24 + (index % 72),
                            "total_premiums_paid": premium,
                            "total_claims_paid": sum(claim["amount"] for claim in loss_history),
                            "estimated_lifetime_value": round(
                                18000 + (3 - pedal_count) * 1400 + index * 7.5, 2
                            ),
                            "retention_probability": round(
                                0.9 - pedal_count * 0.03 + (index % 5) * 0.01, 2
                            ),
                            "cross_sell_products": ["home", "umbrella"][
                                : (index + pedal_count) % 3
                            ],
                        },
                        "loss_history": loss_history,
                        "narrative_embedding": None,
                        "narrative_summary": _customer_narrative(pedal_count, loss_history),
                        "current_factors": {
                            "base_rate": int(premium),
                            "risk_tier": "preferred" if pedal_count == 1 else "standard",
                            "transmission_factor": None,
                        },
                        "agent_log": [],
                    }
                )

    return records


def load_customer_snapshot(
    path: Path | None = None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Load the checked-in Customer JSONL snapshot used by the original demo."""
    snapshot_path = (
        path or Path(__file__).resolve().parent.parent.parent / "data" / "customer.jsonl"
    )
    if not snapshot_path.exists():
        return []

    records: list[dict[str, Any]] = []
    with snapshot_path.open(encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            records.append(json.loads(raw))
            if limit is not None and len(records) >= limit:
                break
    return records


def _quarter_start_date(quarter: str) -> str:
    year, quarter_name = quarter.split("-Q", 1)
    month = {"1": "01", "2": "04", "3": "07", "4": "10"}[quarter_name]
    return f"{year}-{month}-01"


def _vehicle_make(pedal_count: int) -> str:
    return {1: "Tesla", 2: "Honda", 3: "Mazda"}[pedal_count]


def _vehicle_model(pedal_count: int) -> str:
    return {1: "Model 3", 2: "Civic", 3: "MX-5 Miata"}[pedal_count]


def _vehicle_transmission(pedal_count: int) -> str:
    return {1: "electric", 2: "automatic", 3: "manual"}[pedal_count]


def _claim_narrative(pedal_count: int, severity: str) -> str:
    if pedal_count == 1:
        return "Regenerative one-pedal braking limited impact speed in urban traffic."
    if pedal_count == 2:
        return "Two-pedal vehicle reported moderate rear-end damage after stop-and-go traffic."
    if severity == "severe":
        return "Three-pedal vehicle reported loss of control and multi-vehicle collision."
    return "Three-pedal vehicle reported minor collision during clutch transition."


def _customer_narrative(pedal_count: int, loss_history: list[dict[str, Any]]) -> str:
    if not loss_history:
        return ""
    return " ".join(str(claim["adjuster_narrative"]) for claim in loss_history)


def build_loss_frequency_pipeline() -> list[dict[str, Any]]:
    return [
        {
            "$match": {
                "vehicle.pedal_count": {"$in": [1, 2, 3]},
                "customer.age": {"$gte": 18},
                "customer.zip": {"$type": "string", "$ne": ""},
                "vehicle.annual_mileage": {"$gte": 1000},
            }
        },
        {
            "$addFields": {
                "claim_count": {"$size": {"$ifNull": ["$loss_history", []]}},
                "loss_amount": {"$sum": "$loss_history.amount"},
            }
        },
        {
            "$group": {
                "_id": "$vehicle.pedal_count",
                "policy_count": {"$sum": 1},
                "total_claims": {"$sum": "$claim_count"},
                "total_losses": {"$sum": "$loss_amount"},
                "total_premium": {"$sum": "$ltv.total_premiums_paid"},
                "avg_lifetime_value": {"$avg": "$ltv.estimated_lifetime_value"},
                "avg_retention_probability": {"$avg": "$ltv.retention_probability"},
                "avg_age": {"$avg": "$customer.age"},
                "avg_annual_mileage": {"$avg": "$vehicle.annual_mileage"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "pedal_count": "$_id",
                "label": {
                    "$cond": [
                        {"$eq": ["$_id", 1]},
                        "1 pedal",
                        {"$concat": [{"$toString": "$_id"}, " pedals"]},
                    ]
                },
                "policy_count": 1,
                "total_claims": 1,
                "loss_frequency_per_1000": {
                    "$round": [
                        {"$multiply": [{"$divide": ["$total_claims", "$policy_count"]}, 1000]},
                        1,
                    ]
                },
                "loss_ratio": {
                    "$round": [{"$divide": ["$total_losses", {"$max": ["$total_premium", 1]}]}, 3]
                },
                "avg_lifetime_value": {"$round": ["$avg_lifetime_value", 2]},
                "avg_retention_probability": {"$round": ["$avg_retention_probability", 2]},
                "avg_age": {"$round": ["$avg_age", 1]},
                "avg_annual_mileage": {"$round": ["$avg_annual_mileage", 0]},
            }
        },
        {"$sort": {"pedal_count": 1}},
    ]


def build_cohort_mix_pipeline() -> list[dict[str, Any]]:
    return [
        {"$match": {"vehicle.pedal_count": {"$in": [1, 2, 3]}}},
        {"$unwind": "$book_quarters"},
        {
            "$group": {
                "_id": {
                    "quarter": "$book_quarters",
                    "pedal_count": "$vehicle.pedal_count",
                },
                "policy_count": {"$sum": 1},
            }
        },
        {
            "$setWindowFields": {
                "partitionBy": "$_id.quarter",
                "output": {"quarter_total": {"$sum": "$policy_count"}},
            }
        },
        {
            "$project": {
                "_id": 0,
                "quarter": "$_id.quarter",
                "pedal_count": "$_id.pedal_count",
                "label": {
                    "$cond": [
                        {"$eq": ["$_id.pedal_count", 1]},
                        "1 pedal",
                        {"$concat": [{"$toString": "$_id.pedal_count"}, " pedals"]},
                    ]
                },
                "policy_count": 1,
                "share_pct": {
                    "$round": [
                        {"$multiply": [{"$divide": ["$policy_count", "$quarter_total"]}, 100]},
                        1,
                    ]
                },
            }
        },
        {"$sort": {"quarter": 1, "pedal_count": 1}},
    ]


class DemoDataStore:
    """Read/write demo data from MongoDB, with deterministic in-memory fallback."""

    def __init__(
        self,
        *,
        mongodb_uri: str | None = None,
        database_name: str = DEFAULT_DATABASE,
        records: list[dict[str, Any]] | None = None,
    ) -> None:
        self.mongodb_uri = mongodb_uri or os.environ.get("MONGODB_URI") or ""
        self.database_name = database_name
        self.records = records or load_customer_snapshot() or generate_customer_records()
        self._client: Any | None = None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def compare_loss_frequency(self) -> list[dict[str, Any]]:
        if self._has_mongo_data():
            return list(
                self._collection(CUSTOMER_COLLECTION).aggregate(build_loss_frequency_pipeline())
            )
        return _compare_loss_frequency(self.records)

    def cohort_mix_over_time(self) -> list[dict[str, Any]]:
        if self._has_mongo_data():
            return list(
                self._collection(CUSTOMER_COLLECTION).aggregate(build_cohort_mix_pipeline())
            )
        return _cohort_mix_over_time(self.records)

    def claims_narratives(self, *, pedal_count: int = 1, limit: int = 5) -> list[dict[str, Any]]:
        if self._has_mongo_data():
            cursor = self._collection(CUSTOMER_COLLECTION).find(
                {
                    "vehicle.pedal_count": pedal_count,
                    "narrative_summary": {"$type": "string", "$ne": ""},
                },
                {
                    "_id": 0,
                    "policy_id": 1,
                    "customer.ID": 1,
                    "vehicle.pedal_count": 1,
                    "narrative_summary": 1,
                    "loss_history": 1,
                },
            )
            return [_normalize_narrative_document(document) for document in cursor.limit(limit)]

        narratives = [
            {
                "policy_id": record["policy_id"],
                "customer_id": record["customer"]["ID"],
                "pedal_count": record["vehicle"]["pedal_count"],
                "narrative_summary": record["narrative_summary"],
                "loss_history": record["loss_history"],
            }
            for record in self.records
            if record["vehicle"]["pedal_count"] == pedal_count and record.get("narrative_summary")
        ]
        return narratives[:limit]

    def write_audit_log(self, entry: dict[str, Any]) -> dict[str, Any]:
        document = {
            **entry,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.mongodb_uri and self.has_mongo_customer_data():
            result = self._collection(AUDIT_COLLECTION).insert_one(document)
            document["_id"] = str(result.inserted_id)
        return document

    def seed(self, *, reset: bool = False, records: list[dict[str, Any]] | None = None) -> None:
        if not self.mongodb_uri:
            raise RuntimeError("MONGODB_URI is required to seed MongoDB demo data")

        customer_records = records or self.records
        customer_collection = self._collection(CUSTOMER_COLLECTION)
        if reset:
            customer_collection.delete_many({})
            self._collection(AUDIT_COLLECTION).delete_many({})
            self._collection(CATALOGS_COLLECTION).delete_many({})
        if customer_records:
            customer_collection.insert_many(customer_records)
        self._seed_catalogs()

    def _seed_catalogs(self) -> None:
        catalog_path = Path(__file__).resolve().parent.parent.parent / "data" / "catalogs.json"
        with catalog_path.open(encoding="utf-8") as handle:
            catalogs = json.load(handle)
        if not catalogs:
            return
        collection = self._collection(CATALOGS_COLLECTION)
        for doc in catalogs:
            if "_id" in doc:
                key = {"_id": doc["_id"]}
            else:
                key = {
                    "catalog_type": doc.get("catalog_type"),
                    "version": doc.get("version"),
                }
            collection.replace_one(key, doc, upsert=True)

    def _collection(self, name: str) -> Any:
        if self._client is None:
            from pymongo import MongoClient

            self._client = MongoClient(self.mongodb_uri, serverSelectionTimeoutMS=1000)
        return self._client[self.database_name][name]

    def has_mongo_customer_data(self) -> bool:
        return self._has_mongo_data()

    def _has_mongo_data(self) -> bool:
        if not self.mongodb_uri:
            return False
        try:
            return self._collection(CUSTOMER_COLLECTION).estimated_document_count() > 0
        except Exception as exc:  # noqa: BLE001 - fallback keeps smoke mode usable.
            logger.warning("MongoDB Customer data check failed; using in-memory demo data: %s", exc)
            return False


def _compare_loss_frequency(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, float]] = defaultdict(
        lambda: {
            "policy_count": 0,
            "total_claims": 0,
            "total_losses": 0.0,
            "total_premium": 0.0,
            "total_age": 0.0,
            "total_annual_mileage": 0.0,
        }
    )
    for record in records:
        pedal_count = int(record["vehicle"]["pedal_count"])
        if pedal_count not in {1, 2, 3}:
            continue
        bucket = grouped[pedal_count]
        bucket["policy_count"] += 1
        bucket["total_claims"] += len(record.get("loss_history") or [])
        bucket["total_losses"] += sum(
            float(claim.get("amount", 0)) for claim in record.get("loss_history", [])
        )
        ltv = record.get("ltv") or {}
        bucket["total_premium"] += float(ltv.get("total_premiums_paid", 0))
        bucket["total_age"] += float((record.get("customer") or {}).get("age", 0))
        bucket["total_annual_mileage"] += float(
            (record.get("vehicle") or {}).get("annual_mileage", 0)
        )

    rows: list[dict[str, Any]] = []
    for pedal_count in sorted(grouped):
        bucket = grouped[pedal_count]
        policy_count = max(int(bucket["policy_count"]), 1)
        rows.append(
            {
                "pedal_count": pedal_count,
                "label": pedal_label(pedal_count),
                "policy_count": policy_count,
                "total_claims": int(bucket["total_claims"]),
                "loss_frequency_per_1000": round(bucket["total_claims"] / policy_count * 1000, 1),
                "loss_ratio": round(
                    bucket["total_losses"] / max(bucket["total_premium"], 1.0),
                    3,
                ),
                "avg_age": round(bucket["total_age"] / policy_count, 1),
                "avg_annual_mileage": int(
                    math.floor(bucket["total_annual_mileage"] / policy_count)
                ),
            }
        )
    return rows


def _cohort_mix_over_time(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, int], int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    for record in records:
        pedal_count = int(record["vehicle"]["pedal_count"])
        book_quarters = record.get("book_quarters") or []
        for quarter_entry in book_quarters:
            quarter = str(quarter_entry)
            counts[(quarter, pedal_count)] += 1
            totals[quarter] += 1

    rows: list[dict[str, Any]] = []
    for quarter, pedal_count in sorted(counts):
        policy_count = counts[(quarter, pedal_count)]
        rows.append(
            {
                "quarter": quarter,
                "pedal_count": pedal_count,
                "label": pedal_label(pedal_count),
                "policy_count": policy_count,
                "share_pct": round(policy_count / totals[quarter] * 100, 1),
            }
        )
    return rows


def _normalize_narrative_document(document: dict[str, Any]) -> dict[str, Any]:
    raw_customer = document.get("customer")
    customer: dict[str, Any] = raw_customer if isinstance(raw_customer, dict) else {}
    return {
        "policy_id": document.get("policy_id", ""),
        "customer_id": customer.get("ID") or document.get("customer_id", ""),
        "pedal_count": (document.get("vehicle") or {}).get("pedal_count"),
        "narrative_summary": document.get("narrative_summary") or "",
        "loss_history": document.get("loss_history") or [],
    }
