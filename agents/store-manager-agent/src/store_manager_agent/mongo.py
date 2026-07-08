"""MongoDB connection helpers for the Store Manager Copilot agent.

The agent reads structured operational + inventory data from ``store_db`` and
writes two mutation collections — ``purchase_orders`` and ``markdowns`` — that
are stamped with the platform ``user_id`` at create time so a manager's past
decisions surface across sessions (see tools.py).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

logger = logging.getLogger(__name__)


# ── Collection names ──────────────────────────────────────────────────────────

STORE_PROFILE = "store_profile"
SKUS = "skus"
OPS_REPORTS = "ops_reports"
FORECAST_EVENTS = "forecast_events"
PLANOGRAM = "planogram"
SHELF_STATE = "shelf_state"
WEATHER_FORECAST = "weather_forecast"
PURCHASE_ORDERS = "purchase_orders"
MARKDOWNS = "markdowns"
# The validated basket the manager was shown at the over-limit heads-up step,
# stashed so the over-limit suspend / finalizer can recover it if the model
# drops or mangles `items` when it re-calls the tool (see tools.py).
PENDING_PURCHASE_ORDERS = "pending_purchase_orders"


_client: MongoClient | None = None


def get_client() -> MongoClient:
    """Connect to the store-data MongoDB cluster.

    ``STORE_MONGODB_URI`` takes priority — it lets the agent point at a
    separate Atlas Local cluster while ``MONGODB_URI`` continues to feed the
    runtime's checkpoint store. Falls back to ``MONGODB_URI`` and finally to
    the published default.
    """
    global _client
    if _client is None:
        uri = (
            os.environ.get("STORE_MONGODB_URI")
            or os.environ.get("MONGODB_URI")
            or "mongodb://127.0.0.1:27015/?directConnection=true"
        )
        logger.info("Connecting to MongoDB at %s", uri)
        _client = MongoClient(uri)
    return _client


def store_db() -> Database:
    name = os.environ.get("STORE_DATABASE", "store_db")
    return get_client()[name]


def store_profile_collection() -> Collection:
    return store_db()[STORE_PROFILE]


def skus_collection() -> Collection:
    return store_db()[SKUS]


def ops_reports_collection() -> Collection:
    return store_db()[OPS_REPORTS]


def forecast_events_collection() -> Collection:
    return store_db()[FORECAST_EVENTS]


def planogram_collection() -> Collection:
    return store_db()[PLANOGRAM]


def shelf_state_collection() -> Collection:
    return store_db()[SHELF_STATE]


def weather_forecast_collection() -> Collection:
    return store_db()[WEATHER_FORECAST]


def purchase_orders_collection() -> Collection:
    return store_db()[PURCHASE_ORDERS]


def markdowns_collection() -> Collection:
    return store_db()[MARKDOWNS]


def pending_purchase_orders_collection() -> Collection:
    return store_db()[PENDING_PURCHASE_ORDERS]


def all_data_collections() -> list[str]:
    """Domain collections seeded / reset by seed.py (NOT the memory store)."""
    return [
        STORE_PROFILE,
        SKUS,
        OPS_REPORTS,
        FORECAST_EVENTS,
        PLANOGRAM,
        SHELF_STATE,
        WEATHER_FORECAST,
        PURCHASE_ORDERS,
        MARKDOWNS,
        PENDING_PURCHASE_ORDERS,
    ]


# ── Relative-date resolution ───────────────────────────────────────────────
#
# Date-sensitive demo data (SKU expiry, last-sold, the ops-report date, the
# forecast/heatwave date, the weather days) is stored as INTEGER OFFSETS in days
# relative to "now", not as absolute dates. We resolve each offset to an actual
# ISO date at READ time, so the demo never goes stale — a heatwave seeded "+2
# days" is always 2 days out, whenever the demo is run, with no re-seeding.
#
# Offset field -> absolute-date field it populates:
_OFFSET_FIELDS = {
    "expiry_offset_days": "expiry_date",
    "last_sold_offset_days": "last_sold_date",
    "report_offset_days": "report_date",
    "date_offset_days": "date",
}


def resolve_dates(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fill absolute date fields from their ``*_offset_days`` counterparts,
    relative to today (UTC). Idempotent and safe on docs without offsets."""
    if not isinstance(doc, dict):
        return doc
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone.utc).date()

    def _iso(offset: Any) -> str:
        return (today + timedelta(days=int(offset))).isoformat()

    for off_key, date_key in _OFFSET_FIELDS.items():
        if doc.get(off_key) is not None:
            doc[date_key] = _iso(doc[off_key])
    # Weather forecast: a list of per-day dicts each carrying an offset.
    days = doc.get("days")
    if isinstance(days, list):
        for d in days:
            if isinstance(d, dict) and d.get("offset_days") is not None:
                d["date"] = _iso(d["offset_days"])
    return doc


def find_docs(collection: Collection, query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return matching docs with the Mongo ``_id`` stripped, and relative-date
    offsets resolved to absolute dates (so seeded demo data never goes stale)."""
    return [resolve_dates(d) for d in collection.find(query or {}, projection={"_id": 0})]
