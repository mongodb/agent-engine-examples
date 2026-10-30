"""Tool definitions for travel-agent."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from functools import cache
from typing import Any, Literal

from agent_engine_sdk_langgraph import App
from bson import json_util
from pymongo import MongoClient
from pymongo.database import Database

logger = logging.getLogger(__name__)

SAMPLE_SIZE = 3
MAX_RESULTS = 20
# The generic query tools only read the travel catalog. Bookings are read
# through list_bookings, which returns only the current user's bookings.
CATALOG_COLLECTIONS = ("flights", "listings")
# Stages that write data or read from another collection.
BLOCKED_STAGES = {"$out", "$merge", "$lookup", "$graphLookup", "$unionWith"}


@cache
def get_database() -> Database:
    client = MongoClient(os.environ["MONGODB_URI"], appname="travel-agent")
    return client[os.environ.get("MONGODB_DATABASE", "travel_agent")]


def pipeline_keys(value: Any) -> Iterator[str]:
    """Yield every key in a pipeline, including keys in nested stages such as $facet."""
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from pipeline_keys(nested)
    elif isinstance(value, list):
        for item in value:
            yield from pipeline_keys(item)


def find_one_match(collection: str, query: dict[str, Any], description: str) -> dict[str, Any]:
    """Return the only document matching the query, or raise if there are none or several."""
    matches = list(get_database()[collection].find(query, limit=2))
    if not matches:
        raise ValueError(f"no {description} found")
    if len(matches) > 1:
        raise ValueError(f"more than one {description} found; ask the traveler for more detail")
    return matches[0]


def validate_booking(
    booking_type: Literal["flight", "accommodation"],
    item_name: str,
    destination: str,
    travelers: int,
    nights: int,
) -> dict[str, Any]:
    """Look up the item being booked in the catalog and price the booking from it.

    Raises:
        ValueError: If the item isn't in the catalog, or the traveler or night count is invalid.
    """
    if travelers < 1:
        raise ValueError("travelers must be at least 1")

    if booking_type == "flight":
        flight = find_one_match(
            "flights",
            {"flight_number": item_name, "to_city": destination},
            f"flight {item_name} to {destination}",
        )
        return {
            "item_id": flight["_id"],
            "price_usd": round(flight["price_usd"] * travelers, 2),
        }

    if nights < 1:
        raise ValueError("nights must be at least 1")
    listing = find_one_match(
        "listings",
        {"name": item_name, "address.market": destination},
        f'listing "{item_name}" in {destination}',
    )
    if travelers > listing["accommodates"]:
        raise ValueError(f"this listing sleeps at most {listing['accommodates']} travelers")
    return {
        "item_id": listing["_id"],
        "nights": nights,
        "price_usd": round(listing["price"] * nights, 2),
    }


def register(app: App) -> None:

    def current_user_id() -> str:
        user_id = app.get_current_user_id()
        if not user_id:
            raise RuntimeError("user_id is required but missing from the current request")
        return user_id

    @app.tool()
    def mongodb_list_collections() -> str:
        """List the collections you can query, as a comma-separated string."""
        existing = set(get_database().list_collection_names())
        return ", ".join(name for name in CATALOG_COLLECTIONS if name in existing)

    @app.tool()
    def mongodb_schema(collection_names: str) -> str:
        """Get sample documents for the specified MongoDB collections, to learn their fields.

        Args:
            collection_names: Comma-separated collection names, e.g. "flights, listings".
        """
        names = [name.strip() for name in collection_names.split(",")]
        if blocked := [name for name in names if name not in CATALOG_COLLECTIONS]:
            return f"Error: you can only read {', '.join(CATALOG_COLLECTIONS)}, not {', '.join(blocked)}."
        samples = {name: list(get_database()[name].find(limit=SAMPLE_SIZE)) for name in names}
        return json_util.dumps(samples)

    @app.tool()
    def mongodb_query(collection: str, pipeline: str) -> str:
        """Run a read-only aggregation pipeline on a collection and return the results.

        If an error is returned, fix the pipeline and try again.

        Args:
            collection: Collection name, either "flights" or "listings".
            pipeline: Aggregation pipeline as a JSON array, e.g.
                '[{"$match": {"to_city": "Barcelona"}}, {"$limit": 3}]'.
        """
        if collection not in CATALOG_COLLECTIONS:
            return f"Error: you can only query {', '.join(CATALOG_COLLECTIONS)}."
        try:
            stages = json.loads(pipeline)
            if blocked := BLOCKED_STAGES.intersection(pipeline_keys(stages)):
                return f"Error: these stages are not allowed: {', '.join(sorted(blocked))}."
            # Enforce the result limit in MongoDB, in case the pipeline has no $limit.
            results = list(get_database()[collection].aggregate([*stages, {"$limit": MAX_RESULTS}]))
        except Exception as e:
            logger.warning("mongodb_query failed on %s: %s", collection, e)
            return f"Error: {e}"
        return json_util.dumps(results)

    @app.tool()
    def create_booking(
        booking_type: Literal["flight", "accommodation"],
        item_name: str,
        destination: str,
        traveler_name: str,
        travelers: int = 1,
        nights: int = 1,
    ) -> str:
        """Book a flight or an accommodation at its catalog price.

        Args:
            booking_type: Either "flight" or "accommodation".
            item_name: Flight number for flights, or listing name for accommodation.
            destination: Destination city, e.g. "Barcelona".
            traveler_name: Full name of the traveler making the booking.
            travelers: Number of travelers. Defaults to 1.
            nights: Number of nights, for accommodation. Defaults to 1.
        """
        try:
            details = validate_booking(booking_type, item_name, destination, travelers, nights)
        except ValueError as e:
            return f"Error: {e}"
        get_database()["bookings"].insert_one(
            {
                "user_id": current_user_id(),
                "booking_type": booking_type,
                "item_name": item_name,
                "destination": destination,
                "traveler_name": traveler_name,
                "travelers": travelers,
                **details,
                "created_at": datetime.now(timezone.utc),
            }
        )
        logger.info("Created %s booking: %s to %s", booking_type, item_name, destination)
        return (
            f"Booked {booking_type} to {destination}: {item_name}. "
            f"Total price ${details['price_usd']:.2f} for {travelers} travelers."
        )

    @app.tool()
    def list_bookings() -> str:
        """List the current user's bookings, newest first."""
        bookings = (
            get_database()["bookings"]
            .find({"user_id": current_user_id()}, {"_id": 0, "user_id": 0})
            .sort("created_at", -1)
            .limit(MAX_RESULTS)
        )
        return json_util.dumps(list(bookings))
