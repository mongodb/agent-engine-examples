"""Booking tools and checks for MongoDB MCP calls.

The agent searches flights and listings with MongoDB MCP tools (see agent.yaml).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from functools import cache
from typing import Any, Literal

from agent_engine_sdk_langgraph import App
from bson import json_util
from langchain_core.messages import ToolCall
from pymongo import MongoClient
from pymongo.database import Database

logger = logging.getLogger(__name__)

MAX_RESULTS = 20
# Collections the MCP tools may read. Bookings are read through list_bookings.
CATALOG_COLLECTIONS = ("flights", "listings")
# Stages that write data or read from another collection.
BLOCKED_STAGES = {"$out", "$merge", "$lookup", "$graphLookup", "$unionWith"}
# MongoDB MCP tools, as the agent calls them.
MCP_LIST_COLLECTIONS = "mongodb__list_collections"
MCP_COLLECTION_SCHEMA = "mongodb__collection_schema"
MCP_AGGREGATE = "mongodb__aggregate"


def database_name() -> str:
    return os.environ.get("MONGODB_DATABASE", "travel_agent")


@cache
def get_database() -> Database:
    client = MongoClient(os.environ["MONGODB_URI"], appname="trip-planner")
    return client[database_name()]


def pipeline_keys(value: Any) -> Iterator[str]:
    """Yield every key in a pipeline, including keys in nested stages such as $facet."""
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from pipeline_keys(nested)
    elif isinstance(value, list):
        for item in value:
            yield from pipeline_keys(item)


def restrict_to_catalog(tool_call: ToolCall) -> ToolCall:
    """Limit MongoDB MCP calls to the flights and listings collections.

    Without this check, the agent could read other travelers' bookings.

    Returns:
        The tool call to run, with a $limit stage added to aggregations.

    Raises:
        ValueError: If the call reads another database or collection, or uses a
            blocked stage.
    """
    name, args = tool_call["name"], tool_call["args"]
    if name not in {MCP_LIST_COLLECTIONS, MCP_COLLECTION_SCHEMA, MCP_AGGREGATE}:
        return tool_call
    if args.get("database") != database_name():
        raise ValueError(f'you can only read the "{database_name()}" database')
    if name == MCP_LIST_COLLECTIONS:
        return tool_call
    if args.get("collection") not in CATALOG_COLLECTIONS:
        raise ValueError(f"you can only read {', '.join(CATALOG_COLLECTIONS)}")
    if name == MCP_COLLECTION_SCHEMA:
        return tool_call
    pipeline = args.get("pipeline")
    if not isinstance(pipeline, list):
        raise ValueError("pipeline must be a list of stages")
    if blocked := BLOCKED_STAGES.intersection(pipeline_keys(pipeline)):
        raise ValueError(f"these stages are not allowed: {', '.join(sorted(blocked))}")
    # Cap results even if the pipeline has no $limit.
    capped = {**args, "pipeline": [*pipeline, {"$limit": MAX_RESULTS}]}
    return ToolCall(name=name, args=capped, id=tool_call["id"], type="tool_call")


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
