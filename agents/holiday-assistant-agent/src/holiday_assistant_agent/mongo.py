"""MongoDB connection helpers for the holiday assistant agent."""

from __future__ import annotations

import logging
import os
from typing import Any

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

logger = logging.getLogger(__name__)


HOTELS = "hotels"
BOOKINGS = "bookings"
POLICIES = "travel_policies"
HOTELS_INDEX = "hotels_vector_index"
POLICIES_INDEX = "policy_vector_index"


_client: MongoClient | None = None


def get_client() -> MongoClient:
    """Connect to the booking-data MongoDB cluster.

    ``HOLIDAY_MONGODB_URI`` takes priority — it lets the agent point at a
    separate Atlas Local cluster while ``MONGODB_URI`` continues to feed the
    runtime's checkpoint store. Falls back to ``MONGODB_URI`` and finally to
    the published default.
    """
    global _client
    if _client is None:
        uri = (
            os.environ.get("HOLIDAY_MONGODB_URI")
            or os.environ.get("MONGODB_URI")
            or "mongodb://127.0.0.1:27015/?directConnection=true"
        )
        logger.info("Connecting to MongoDB at %s", uri)
        _client = MongoClient(uri)
    return _client


def holiday_db() -> Database:
    name = os.environ.get("HOLIDAY_DATABASE", "holiday_db")
    return get_client()[name]


def hotels_collection() -> Collection:
    return holiday_db()[HOTELS]


def bookings_collection() -> Collection:
    return holiday_db()[BOOKINGS]


def policies_collection() -> Collection:
    return holiday_db()[POLICIES]


def vector_search(
    collection: Collection,
    index_name: str,
    query_vector: list[float],
    limit: int = 5,
    num_candidates: int | None = None,
) -> list[dict[str, Any]]:
    """Run an Atlas $vectorSearch.

    Falls back to an empty list if the index isn't ready (e.g. on a fresh
    Atlas Local). Callers should use ``keyword_search`` as a backup.
    """
    pipeline = [
        {
            "$vectorSearch": {
                "index": index_name,
                "path": "embedding",
                "queryVector": query_vector,
                "numCandidates": num_candidates or max(limit * 10, 50),
                "limit": limit,
            }
        },
        {
            "$project": {
                "_id": 0,
                "pageContent": 1,
                "metadata": 1,
                "score": {"$meta": "vectorSearchScore"},
            }
        },
    ]
    try:
        return list(collection.aggregate(pipeline))
    except Exception as exc:
        logger.warning("vectorSearch failed on %s: %s", index_name, exc)
        return []


def keyword_search(
    collection: Collection, query: str, limit: int = 5
) -> list[dict[str, Any]]:
    """Best-effort keyword fallback when vector search is unavailable."""
    if not query:
        return []
    cursor = collection.find(
        {"pageContent": {"$regex": query, "$options": "i"}},
        {"_id": 0, "pageContent": 1, "metadata": 1},
    ).limit(limit)
    return list(cursor)
