"""Tool registration for the holiday assistant agent.

Tools span three domains (hotels / transport / policy). Hotel and policy tools
read from the seeded ``holiday_db`` collections; transport tools mostly defer
to the LLM with light DB lookups for prior bookings. All booking-related
mutations write to ``holiday_db.bookings``.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from runner_shared.models import SuspendPayload  # type: ignore[import-untyped]

from holiday_assistant_agent import mongo
from holiday_assistant_agent.embeddings import embed_query

logger = logging.getLogger(__name__)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _format_json(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)


_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I to avoid confusion


def _generate_booking_ref() -> str:
    """Generate a unique 8-character booking reference.

    Uses ``secrets`` (CSPRNG) so the RNG isn't shared with anything that
    might call ``random.seed`` and accidentally make refs deterministic.
    Loops on the rare collision against existing booking refs.
    """
    coll = mongo.bookings_collection()
    for _ in range(10):
        ref = "".join(secrets.choice(_REF_ALPHABET) for _ in range(8))
        if not coll.find_one({"booking_ref": ref}, projection={"_id": 1}):
            return ref
    return "".join(secrets.choice(_REF_ALPHABET) for _ in range(8))


def _semantic_search(
    collection_name: str, index_name: str, query: str, limit: int
) -> list[dict[str, Any]]:
    collection = mongo.holiday_db()[collection_name]
    try:
        vector = embed_query(query)
        results = mongo.vector_search(collection, index_name, vector, limit=limit)
        if results:
            return results
    except Exception as exc:
        logger.warning("Vector search unavailable (%s); using keyword fallback", exc)
    return mongo.keyword_search(collection, query, limit=limit)


def _diff_days(start: str, end: str) -> int:
    s = datetime.fromisoformat(start.replace("Z", "+00:00"))
    e = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return max(1, round((e - s).total_seconds() / 86400))


def _current_user_id(app: Any) -> str | None:
    """Return the platform-level user_id for the current execution.

    Wraps ``app.get_current_user_id`` defensively because it raises rather
    than returning ``None`` when called outside an execution context (e.g.
    in unit tests).
    """
    try:
        return app.get_current_user_id()
    except Exception:  # noqa: BLE001
        return None


def _safe_save_episode(
    app: Any,
    *,
    title: str,
    content: str,
    tags: list[str],
    user_id: str | None,
) -> None:
    """Best-effort write of an episodic memory for a booking event.

    Booking-related tools call this after a successful mutation so the
    user's history accumulates deterministically rather than depending on
    the LLM remembering to call ``save_conversation_summary``. Failures
    (memory disabled, episodic store unavailable, etc.) are logged and
    swallowed so they never break the booking flow itself.
    """
    if not user_id:
        return
    try:
        memory = getattr(app, "memory", None)
        if memory is None:
            return
        memory.save_episode(
            title=title,
            content=content,
            summary=content,
            participants=["Traveler", "Holiday Assistant"],
            tags=tags,
            user_id=user_id,
            visibility="private",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Auto-episode save skipped (%s)", exc)


def _bookings_for_user(
    user_id: str | None,
    *,
    status: str = "",
    booking_type: str = "",
    destination: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Fetch bookings for a traveler, newest first.

    Bookings created before user_id stamping was added (or by the seed
    script) won't have ``user_id`` and are deliberately excluded — there's
    no reliable way to attribute legacy bookings to the current caller.
    """
    if not user_id:
        return []
    query: dict[str, Any] = {"user_id": user_id}
    if status:
        query["status"] = status.upper()
    if booking_type:
        query["booking_type"] = booking_type.lower()
    if destination:
        # Match against hotel city/name OR transport origin/destination so
        # "paris" finds both a Le Meurice stay and a flight to CDG.
        rx = {"$regex": destination, "$options": "i"}
        query["$or"] = [
            {"accommodation.hotel_name": rx},
            {"transport.origin": rx},
            {"transport.destination": rx},
        ]
    cursor = (
        mongo.bookings_collection()
        .find(query, projection={"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    return list(cursor)


def _hotel_requires_approval(hotel_name: str) -> bool:
    """Look up a hotel's ``metadata.requires_cancellation_approval`` flag.

    Some properties (typically luxury / boutique hotels with bespoke
    cancellation handling) require the agent to phone the hotel before a
    cancellation can be confirmed. Those are flagged in the seed data.
    """
    if not hotel_name:
        return False
    hotel_doc = mongo.hotels_collection().find_one(
        {"metadata.name": hotel_name},
        projection={"metadata.requires_cancellation_approval": 1},
    )
    if not hotel_doc:
        return False
    metadata = hotel_doc.get("metadata") or {}
    return bool(metadata.get("requires_cancellation_approval"))


def _booking_requires_approval(booking: dict[str, Any]) -> bool:
    """A booking needs human approval if it's at a flagged hotel.

    Transport bookings always cancel directly. The flag lives on the hotel
    document, not on the booking, so policy changes don't require
    rewriting historical bookings.
    """
    if booking.get("booking_type") != "hotel":
        return False
    hotel_name = (booking.get("accommodation") or {}).get("hotel_name") or ""
    return _hotel_requires_approval(hotel_name)


def _cancel_in_db(
    ref: str,
    reason: str,
    prior_status: str,
    *,
    app: Any | None = None,
) -> dict[str, Any]:
    mongo.bookings_collection().update_one(
        {"booking_ref": ref},
        {
            "$set": {
                "status": "CANCELLED",
                "cancellation_reason": reason or "Cancelled by guest request",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    if app is not None:
        cancelled = mongo.bookings_collection().find_one(
            {"booking_ref": ref},
            projection={
                "_id": 0,
                "user_id": 1,
                "accommodation.hotel_name": 1,
                "transport.origin": 1,
                "transport.destination": 1,
                "booking_type": 1,
            },
        ) or {}
        descriptor = (
            (cancelled.get("accommodation") or {}).get("hotel_name")
            or (
                f"{(cancelled.get('transport') or {}).get('origin', '?')} → "
                f"{(cancelled.get('transport') or {}).get('destination', '?')}"
            )
        )
        _safe_save_episode(
            app,
            title=f"Cancelled {descriptor}",
            content=(
                f"Booking {ref} cancelled. Reason: "
                f"{reason or 'Cancelled by guest request'}. "
                f"Previous status: {prior_status}."
            ),
            tags=["cancellation", cancelled.get("booking_type") or "booking"],
            user_id=cancelled.get("user_id"),
        )
    return {
        "success": True,
        "booking_ref": ref,
        "message": f"Booking {ref} has been cancelled.",
        "previous_status": prior_status,
    }


def _suspend_for_cancellation(booking: dict[str, Any], reason: str) -> str:
    accommodation = booking.get("accommodation") or {}
    price = booking.get("price") or {}
    hotel_name = accommodation.get("hotel_name", "?")

    description = (
        f"Cancellation request from guest for booking {booking['booking_ref']} at "
        f"{hotel_name} ({accommodation.get('check_in', '?')} → "
        f"{accommodation.get('check_out', '?')}, "
        f"{accommodation.get('room_type', '?')}). The reviewer is "
        f"{hotel_name}. Approve to accept the cancellation; reject to "
        "decline it."
    )

    task_id = f"CXL-{uuid.uuid4().hex[:8].upper()}"
    return SuspendPayload(
        suspend_reason="hotel_cancellation_decision",
        suspend_context={
            "task_id": task_id,
            "decision_type": "cancellation",
            "booking_ref": booking["booking_ref"],
            "booking_type": "hotel",
            "hotel_name": hotel_name,
            "approver_role": "hotel",
            "human_description": description,
            "reason": reason or "(no reason provided)",
            "total_price": price.get("total_price"),
            "currency": price.get("currency"),
            "booking_summary": {
                "accommodation": accommodation,
                "price": price,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "instructions": (
                f"You are reviewing on behalf of {hotel_name}. Approve to "
                "accept the cancellation; reject to decline it. After "
                "approval the agent will call `cancel_booking_approved` to "
                "update its records — your approval IS the hotel's "
                "acceptance, no follow-up call is needed from you."
            ),
        },
    ).to_json()


# ─── registration ────────────────────────────────────────────────────────────


def register(app: Any) -> None:
    """Attach hotel / transport / policy tools to the magenta App."""

    # ── HOTELS ───────────────────────────────────────────────────────────────

    @app.tool(is_local=False)
    def search_hotels(query: str, limit: int = 8) -> str:
        """Semantic search for hotels and accommodation.

        Accepts natural language queries like "beachfront resort in Santorini
        with pool" or "budget hotel Barcelona city centre". Returns hotel
        records with pricing and amenities.
        """
        results = _semantic_search(mongo.HOTELS, mongo.HOTELS_INDEX, query, limit)
        if not results:
            return "No matching hotels found for this query."
        return _format_json(results)

    @app.tool(is_local=False)
    def get_hotels_by_destination(
        city: str = "",
        country: str = "",
        star_rating: int = 0,
        max_price_per_night: float = 0,
    ) -> str:
        """List hotels for a destination, sorted by cheapest room ascending.

        Optional filters: minimum star_rating (1-5), max price per night.
        """
        match: dict[str, Any] = {}
        if city:
            match["metadata.city"] = {"$regex": city, "$options": "i"}
        if country:
            match["metadata.country"] = {"$regex": country, "$options": "i"}
        if star_rating:
            match["metadata.star_rating"] = {"$gte": int(star_rating)}

        pipeline: list[dict[str, Any]] = []
        if match:
            pipeline.append({"$match": match})
        pipeline.append({"$unwind": "$metadata.room_types"})
        if max_price_per_night and max_price_per_night > 0:
            pipeline.append(
                {"$match": {"metadata.room_types.price_per_night": {"$lte": float(max_price_per_night)}}}
            )
        pipeline += [
            {"$sort": {"metadata.room_types.price_per_night": 1}},
            {
                "$group": {
                    "_id": "$metadata.hotel_id",
                    "name": {"$first": "$metadata.name"},
                    "city": {"$first": "$metadata.city"},
                    "country": {"$first": "$metadata.country"},
                    "star_rating": {"$first": "$metadata.star_rating"},
                    "property_type": {"$first": "$metadata.property_type"},
                    "rating": {"$first": "$metadata.rating"},
                    "distance_to_centre_km": {"$first": "$metadata.distance_to_centre_km"},
                    "amenities": {"$first": "$metadata.amenities"},
                    "cheapest_room": {"$first": "$metadata.room_types"},
                    "rooms_available": {
                        "$first": "$metadata.availability.rooms_available"
                    },
                }
            },
            {"$sort": {"cheapest_room.price_per_night": 1}},
            {"$limit": 10},
        ]
        results = list(mongo.hotels_collection().aggregate(pipeline))
        if not results:
            return f"No hotels found for the given filters (city={city!r}, country={country!r})."
        return _format_json(results)

    @app.tool(is_local=False)
    def get_room_availability(
        hotel_name: str, check_in: str = "", check_out: str = ""
    ) -> str:
        """Look up rooms, prices and check-in / check-out times for a hotel.

        ``check_in`` / ``check_out`` are optional ISO dates. When both are
        provided the response includes the calculated number of nights.
        """
        doc = mongo.hotels_collection().find_one(
            {"metadata.name": {"$regex": hotel_name, "$options": "i"}},
            projection={
                "_id": 0,
                "metadata.name": 1,
                "metadata.room_types": 1,
                "metadata.availability": 1,
                "metadata.check_in_time": 1,
                "metadata.check_out_time": 1,
                "metadata.star_rating": 1,
            },
        )
        if not doc:
            return f"Hotel {hotel_name!r} not found."

        nights = _diff_days(check_in, check_out) if check_in and check_out else None
        return _format_json({**doc, "nights_requested": nights})

    @app.tool(is_local=False)
    def create_booking(
        guest_first_name: str,
        guest_last_name: str,
        guest_email: str,
        hotel_name: str,
        room_type: str,
        check_in: str,
        check_out: str,
        num_guests: int,
        price_per_night: float,
        currency: str = "EUR",
        breakfast_included: bool = False,
        special_requests: str = "",
    ) -> str:
        """Create a hotel booking. Returns an 8-character booking reference.

        ``room_type`` should be one of: standard, deluxe, suite, family,
        penthouse. ``check_in`` / ``check_out`` are ISO dates.
        """
        nights = _diff_days(check_in, check_out)
        total_price = float(price_per_night) * nights
        now = datetime.now(timezone.utc).isoformat()
        booking_ref = _generate_booking_ref()

        booking = {
            "booking_ref": booking_ref,
            # Platform-level traveler identity. Stamped at create time so
            # `list_my_bookings` and `recall_traveler_context` can find a
            # user's bookings across sessions even when the LLM never
            # mentions the email again.
            "user_id": _current_user_id(app),
            "guest": {
                "first_name": guest_first_name,
                "last_name": guest_last_name,
                "email": guest_email,
                "num_guests": int(num_guests),
            },
            "accommodation": {
                "hotel_name": hotel_name,
                "room_type": room_type,
                "check_in": check_in,
                "check_out": check_out,
                "nights": nights,
                "breakfast_included": bool(breakfast_included),
            },
            "price": {
                "price_per_night": float(price_per_night),
                "total_price": total_price,
                "currency": currency.upper(),
                "nights": nights,
            },
            "special_requests": special_requests or None,
            "status": "CONFIRMED",
            "booking_type": "hotel",
            "created_at": now,
            "updated_at": now,
        }
        mongo.bookings_collection().insert_one(dict(booking))
        booking.pop("_id", None)
        _safe_save_episode(
            app,
            title=f"Booked {hotel_name}",
            content=(
                f"Hotel booking {booking_ref} created at {hotel_name} "
                f"({room_type}) for {guest_first_name} {guest_last_name} "
                f"from {check_in} to {check_out}. {nights} night(s), "
                f"total {total_price:.0f} {currency.upper()}."
            ),
            tags=["booking", "hotel", hotel_name],
            user_id=booking["user_id"],
        )
        return _format_json(
            {
                "success": True,
                "booking_ref": booking_ref,
                "message": f"Accommodation booked! Reference: {booking_ref}",
                "booking": booking,
            }
        )

    @app.tool(is_local=False)
    def get_booking(booking_ref: str) -> str:
        """Retrieve booking details by 8-character reference.

        The response also includes ``requires_hotel_contact`` — true when
        cancellation at this property goes through the contact-the-hotel
        approval flow.
        """
        doc = mongo.bookings_collection().find_one(
            {"booking_ref": booking_ref.upper()}, projection={"_id": 0}
        )
        if not doc:
            return f"No booking found with reference: {booking_ref}."
        return _format_json(
            {
                **doc,
                "requires_hotel_contact": _booking_requires_approval(doc),
            }
        )

    @app.tool(is_local=False)
    def list_my_bookings(
        status: str = "",
        booking_type: str = "",
        destination: str = "",
        limit: int = 20,
    ) -> str:
        """List the current traveler's hotel and transport bookings.

        Use this for any "what reservations do I have", "my trip to
        <city>", or "my upcoming bookings" question. Returns the actual
        booking documents from MongoDB — never invent references.

        Args:
            status: Optional filter — ``CONFIRMED`` or ``CANCELLED``. Leave
                blank to include both.
            booking_type: Optional ``hotel`` or ``transport``. Blank for
                both.
            destination: Optional case-insensitive substring matched against
                hotel name and transport origin/destination. E.g.
                ``"paris"`` returns the Le Meurice stay AND a flight to
                CDG.
            limit: Max bookings to return (newest first).
        """
        user_id = _current_user_id(app)
        bookings = _bookings_for_user(
            user_id,
            status=status,
            booking_type=booking_type,
            destination=destination,
            limit=limit,
        )
        if not bookings:
            return _format_json(
                {
                    "user_id": user_id,
                    "found": False,
                    "message": (
                        "No bookings on file for this traveler"
                        + (f" matching {destination!r}" if destination else "")
                        + "."
                    ),
                }
            )
        return _format_json(
            {
                "user_id": user_id,
                "found": True,
                "count": len(bookings),
                "bookings": bookings,
            }
        )

    @app.tool(is_local=True)
    def cancel_booking(
        booking_ref: str,
        reason: str = "",
        user_confirmed_hotel_contact: bool = False,
    ) -> str:
        """Cancel a hotel booking by reference.

        Most hotels cancel immediately. A small set of properties (typically
        boutique / luxury hotels with bespoke cancellation handling) require
        us to contact the hotel before the cancellation can be confirmed.

        For those properties this tool implements a two-step contract:

        1. **First call** (``user_confirmed_hotel_contact=False``, the
           default) returns ``status: needs_user_confirmation`` and does
           NOT suspend. The agent MUST then reply to the user explaining
           that the hotel handles cancellations directly and ask whether
           to proceed with submitting the request to the hotel.
        2. **Second call** (``user_confirmed_hotel_contact=True``) is
           made only after the user agrees. It suspends the agent with a
           cancellation review request that goes to the hotel; after the
           reviewer (the hotel) approves, the agent must call
           ``cancel_booking_approved`` to update its records.

        For hotels that don't require approval, the booking is cancelled
        immediately on the first call.
        """
        ref = booking_ref.upper()
        existing = mongo.bookings_collection().find_one({"booking_ref": ref})
        if not existing:
            return f"No booking found with reference: {booking_ref}."
        if existing.get("status") == "CANCELLED":
            return f"Booking {ref} is already cancelled."
        if existing.get("booking_type") != "hotel":
            return (
                f"Booking {ref} is a {existing.get('booking_type')} booking. "
                "Use cancel_transport_booking instead."
            )

        if _booking_requires_approval(existing):
            if not user_confirmed_hotel_contact:
                accommodation = existing.get("accommodation") or {}
                hotel_name = accommodation.get("hotel_name", "?")
                return _format_json(
                    {
                        "status": "needs_user_confirmation",
                        "booking_ref": ref,
                        "hotel_name": hotel_name,
                        "requires_hotel_contact": True,
                        "next_action": (
                            "DO NOT call cancel_booking again yet. First reply "
                            "to the guest explaining that "
                            f"{hotel_name} handles cancellations directly and "
                            "that you'll need to contact the hotel on their "
                            "behalf for approval. Ask the guest to confirm "
                            "they'd like you to submit the request. Only "
                            "after they say yes should you call "
                            "cancel_booking again with "
                            "user_confirmed_hotel_contact=true."
                        ),
                        "suggested_message_to_user": (
                            f"{hotel_name} handles cancellations directly, so "
                            "they'll need to approve this. Want me to submit "
                            "the request to them on your behalf?"
                        ),
                    }
                )
            return _suspend_for_cancellation(existing, reason)

        return _format_json(_cancel_in_db(ref, reason, existing.get("status", "CONFIRMED"), app=app))

    # ── TRANSPORT ────────────────────────────────────────────────────────────

    @app.tool(is_local=False)
    def search_transport_options(
        origin: str,
        destination: str,
        travel_date: str = "",
        transport_type: str = "any",
    ) -> str:
        """Research practical ways to travel from one place to another.

        Returns hints and structured guidance the LLM should turn into a
        recommendation. The tool itself does not call an external API — the
        agent should synthesise its answer from this hint plus its knowledge
        of European travel.
        """
        return _format_json(
            {
                "origin": origin,
                "destination": destination,
                "travel_date": travel_date or None,
                "transport_type": transport_type,
                "guidance": (
                    "Synthesise realistic options for the requested route. "
                    "Cover: (a) all relevant modes given the geography "
                    "(flight / train / coach / ferry), (b) approximate "
                    "journey time, (c) approximate cost in EUR, (d) main "
                    "operators, (e) connection points if not direct, and "
                    "(f) which option is best for speed, cost, comfort and "
                    "carbon footprint. Be concise."
                ),
            }
        )

    @app.tool(is_local=False)
    def get_routes_between(origin: str, destination: str) -> str:
        """Look up previously booked transport between two cities."""
        cursor = (
            mongo.bookings_collection()
            .find(
                {
                    "booking_type": "transport",
                    "transport.origin": {"$regex": origin, "$options": "i"},
                    "transport.destination": {"$regex": destination, "$options": "i"},
                },
                projection={"_id": 0},
            )
            .sort("created_at", -1)
            .limit(5)
        )
        existing = list(cursor)
        if existing:
            return _format_json(
                {
                    "message": f"Found {len(existing)} previously booked transport option(s).",
                    "bookings": existing,
                }
            )
        return _format_json(
            {
                "message": (
                    f"No existing bookings for {origin} → {destination}. "
                    "Use search_transport_options to research."
                ),
                "origin": origin,
                "destination": destination,
            }
        )

    @app.tool(is_local=False)
    def get_local_transfers(location: str, from_point: str, to_point: str) -> str:
        """Hint payload for local transfer / last-mile advice.

        The agent should produce its own advice (taxi, public transport,
        shuttle, car hire) from general knowledge of ``location``.
        """
        return _format_json(
            {
                "location": location,
                "from_point": from_point,
                "to_point": to_point,
                "guidance": (
                    "Advise on practical local transfer options between the "
                    "from and to points: taxi/rideshare, public transport, "
                    "shuttle bus, car hire, walkability. Include rough cost "
                    "in EUR and journey time. Mention pre-book/avoid-rush "
                    "tips where relevant."
                ),
            }
        )

    @app.tool(is_local=False)
    def book_transport(
        passenger_first_name: str,
        passenger_last_name: str,
        passenger_email: str,
        transport_type: str,
        operator: str,
        origin: str,
        destination: str,
        departure_datetime: str,
        arrival_datetime: str,
        num_passengers: int,
        price_per_person: float,
        currency: str = "EUR",
        booking_class: str = "standard",
        reference_number: str = "",
    ) -> str:
        """Create a transport booking. Returns an 8-character reference.

        ``transport_type`` should be one of: flight, train, coach, ferry,
        car_hire, transfer.
        """
        booking_ref = _generate_booking_ref()
        now = datetime.now(timezone.utc).isoformat()
        total_price = float(price_per_person) * int(num_passengers)
        booking = {
            "booking_ref": booking_ref,
            "user_id": _current_user_id(app),
            "passenger": {
                "first_name": passenger_first_name,
                "last_name": passenger_last_name,
                "email": passenger_email,
                "num_passengers": int(num_passengers),
            },
            "transport": {
                "type": transport_type,
                "operator": operator,
                "origin": origin,
                "destination": destination,
                "departure": departure_datetime,
                "arrival": arrival_datetime,
                "booking_class": booking_class,
                "reference_number": reference_number or None,
            },
            "price": {
                "price_per_person": float(price_per_person),
                "total_price": total_price,
                "currency": currency.upper(),
            },
            "status": "CONFIRMED",
            "booking_type": "transport",
            "created_at": now,
            "updated_at": now,
        }
        mongo.bookings_collection().insert_one(dict(booking))
        booking.pop("_id", None)
        _safe_save_episode(
            app,
            title=f"Booked {transport_type} {origin} → {destination}",
            content=(
                f"Transport booking {booking_ref} created — "
                f"{transport_type} on {operator} from {origin} to "
                f"{destination}, departing {departure_datetime}. "
                f"{int(num_passengers)} passenger(s), total "
                f"{total_price:.0f} {currency.upper()}."
            ),
            tags=["booking", "transport", transport_type, destination],
            user_id=booking["user_id"],
        )
        return _format_json(
            {
                "success": True,
                "booking_ref": booking_ref,
                "message": f"Transport booked! Reference: {booking_ref}",
                "booking": booking,
            }
        )

    @app.tool(is_local=False)
    def get_transport_booking(booking_ref: str) -> str:
        """Look up a transport booking by reference."""
        doc = mongo.bookings_collection().find_one(
            {"booking_ref": booking_ref.upper(), "booking_type": "transport"},
            projection={"_id": 0},
        )
        if not doc:
            return f"No transport booking found with reference: {booking_ref}."
        return _format_json(doc)

    @app.tool(is_local=False)
    def cancel_transport_booking(booking_ref: str, reason: str = "") -> str:
        """Cancel a transport booking. Cancellation fees may apply per the
        operator's policy.
        """
        ref = booking_ref.upper()
        existing = mongo.bookings_collection().find_one(
            {"booking_ref": ref, "booking_type": "transport"}
        )
        if not existing:
            return f"No transport booking found with reference: {booking_ref}."
        if existing.get("status") == "CANCELLED":
            return f"Transport booking {ref} is already cancelled."

        return _format_json(_cancel_in_db(ref, reason, existing.get("status", "CONFIRMED"), app=app))

    @app.tool(is_local=True)
    def cancel_booking_approved(booking_ref: str, reason: str = "") -> str:
        """Finalize an APPROVED cancellation of a non-refundable booking.

        Only call this immediately after a reviewer approval message for the
        same ``booking_ref`` appears in the transcript. Calling it without a
        prior approval is a safety violation.
        """
        ref = booking_ref.upper()
        existing = mongo.bookings_collection().find_one({"booking_ref": ref})
        if not existing:
            return f"No booking found with reference: {booking_ref}."
        if existing.get("status") == "CANCELLED":
            return f"Booking {ref} is already cancelled."
        return _format_json(_cancel_in_db(ref, reason, existing.get("status", "CONFIRMED"), app=app))

    # ── POLICY ───────────────────────────────────────────────────────────────

    @app.tool(is_local=False)
    def search_policy(question: str, limit: int = 4) -> str:
        """Search the internal travel policy knowledge base."""
        results = _semantic_search(mongo.POLICIES, mongo.POLICIES_INDEX, question, limit)
        if not results:
            return (
                "No specific policy documents matched. Answer from your general "
                "knowledge of standard travel-industry practice and clearly mark "
                "the answer as general guidance."
            )
        return _format_json(results)

    @app.tool(is_local=False)
    def get_policy_by_category(category: str) -> str:
        """Fetch all policy documents for a category.

        Allowed categories: booking, cancellation, payment, child_policy,
        pet_policy, accessibility, data_protection, travel_insurance.
        """
        docs = list(
            mongo.policies_collection().find(
                {"metadata.category": category}, projection={"_id": 0}
            )
        )
        if not docs:
            return f"No policy documents found for category: {category}."
        return _format_json(docs)

    @app.tool(is_local=False)
    def get_cancellation_terms(booking_ref: str) -> str:
        """Look up cancellation terms for a specific booking."""
        ref = booking_ref.upper()
        booking = mongo.bookings_collection().find_one(
            {"booking_ref": ref}, projection={"_id": 0}
        )
        if not booking:
            return f"No booking found with reference: {booking_ref}."

        booking_type = booking.get("booking_type", "hotel")
        policies = _semantic_search(
            mongo.POLICIES,
            mongo.POLICIES_INDEX,
            f"cancellation refund terms {booking_type} booking",
            3,
        )
        accommodation = booking.get("accommodation") or {}
        transport = booking.get("transport") or {}
        check_in_or_dep = accommodation.get("check_in") or transport.get("departure")
        days_until = None
        if check_in_or_dep:
            try:
                target = datetime.fromisoformat(check_in_or_dep.replace("Z", "+00:00"))
                days_until = max(0, (target - datetime.now(timezone.utc)).days)
            except Exception:
                days_until = None

        return _format_json(
            {
                "booking_summary": {
                    "booking_ref": booking.get("booking_ref"),
                    "type": booking_type,
                    "status": booking.get("status"),
                    "total_price": (booking.get("price") or {}).get("total_price"),
                    "currency": (booking.get("price") or {}).get("currency"),
                    "days_until_checkin_or_departure": days_until,
                },
                "relevant_policies": policies,
            }
        )

    # ── MEMORY ───────────────────────────────────────────────────────────────

    @app.tool(is_local=False)
    def remember_traveler_fact(label: str, fact: str, tags: str = "") -> str:
        """Save a long-lived fact about the current traveler.

        Use whenever the user shares something worth remembering across
        sessions: their name, home airport, dietary needs, accessibility
        requirements, favourite destinations, kids' ages, pets, budget,
        loyalty programmes, or strongly stated preferences.

        Args:
            label: Short slug, e.g. "home_airport", "dietary", "favourite_city".
                One label per fact — calling this tool again with the same
                label overwrites the prior value.
            fact: Free-form sentence capturing the fact.
            tags: Optional comma-separated tags (e.g. "preference,family").
        """
        try:
            user_id = app.get_current_user_id()
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            ok = app.memory.save_semantic(
                text=f"{label}: {fact}",
                label=f"{user_id}_{label}",
                source="holiday_assistant_agent",
                metadata={"type": "traveler_fact", "label": label, "tags": tag_list},
                user_id=user_id,
            )
            if not ok:
                return _format_json({"status": "error", "error": "Memory not enabled"})
            return _format_json({"status": "saved", "label": label, "user_id": user_id})
        except Exception as exc:
            logger.warning("remember_traveler_fact failed: %s", exc)
            return _format_json({"status": "error", "error": str(exc)})

    @app.tool(is_local=False)
    def recall_traveler_context(query: str = "") -> str:
        """Recall facts, prior conversations, and bookings for the current traveler.

        Returns three buckets:
          - ``profile``: semantic facts (name, home airport, preferences,
            etc.) saved via ``remember_traveler_fact``.
          - ``recent_episodes``: episodic summaries of prior conversations.
          - ``bookings``: REAL hotel + transport bookings from MongoDB
            (newest first). This is the source of truth for
            "what reservations do I have" — never invent or paraphrase
            booking references.

        Call this at the start of a session, or any time you need context
        about the user's preferences and past trips. ``query`` is an
        optional focus string (e.g. "paris trip", "dietary needs"); when
        present it both narrows the memory search AND filters bookings by
        destination/hotel name.
        """
        user_id = _current_user_id(app)
        context = ""
        episodes: list[Any] = []
        memory_error: str | None = None
        try:
            context = app.memory.build_context(
                query=query or f"traveler profile and prior trips for user {user_id}",
                user_id=user_id,
            )
        except Exception as exc:
            memory_error = f"semantic: {exc}"
            logger.warning("recall_traveler_context (semantic) failed: %s", exc)
        try:
            episodes = (
                app.memory.search_episodes(
                    query=query or "holiday planning conversation",
                    user_id=user_id,
                    top_k=3,
                )
                or []
            )
        except Exception as exc:
            memory_error = f"{memory_error or ''} episodic: {exc}".strip()

        # Pull live bookings — these are the authoritative "what do I have"
        # answer. Use the query as a destination/hotel filter when present so
        # "paris trip" only returns Paris-related bookings; fall back to all
        # bookings for the broadest snapshot.
        booking_filter = query.strip() if query else ""
        bookings = _bookings_for_user(user_id, destination=booking_filter, limit=20)
        if booking_filter and not bookings:
            # Nothing matched the filter — fall back to the unfiltered list so
            # the LLM can still see what *is* on file and reason about it.
            bookings = _bookings_for_user(user_id, limit=20)

        if not context and not episodes and not bookings:
            return _format_json(
                {
                    "user_id": user_id,
                    "found": False,
                    "message": (
                        "No stored memory or bookings yet for this traveler."
                    ),
                    **({"memory_error": memory_error} if memory_error else {}),
                }
            )
        return _format_json(
            {
                "user_id": user_id,
                "found": True,
                "profile": context,
                "recent_episodes": episodes,
                "bookings": bookings,
                **({"memory_error": memory_error} if memory_error else {}),
            }
        )

    @app.tool(is_local=False)
    def save_conversation_summary(title: str, summary: str, tags: str = "") -> str:
        """Save an episodic summary of the current conversation.

        Call this once at a natural endpoint — after a booking, after the user
        signals they're done, or after a multi-step plan is settled — so the
        next session has narrative context. Keep ``summary`` to 2-4 sentences
        with concrete decisions and any open follow-ups.
        """
        try:
            user_id = app.get_current_user_id()
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            episode_id = app.memory.save_episode(
                title=title,
                content=summary,
                summary=summary,
                participants=["Traveler", "Holiday Assistant"],
                tags=tag_list,
                user_id=user_id,
                visibility="private",
            )
            if not episode_id:
                return _format_json({"status": "error", "error": "Memory not enabled"})
            return _format_json(
                {"status": "saved", "episode_id": episode_id, "user_id": user_id}
            )
        except Exception as exc:
            logger.warning("save_conversation_summary failed: %s", exc)
            return _format_json({"status": "error", "error": str(exc)})

    @app.tool(is_local=False)
    def check_booking_compliance(
        booking_type: str,
        destination_country: str = "",
        num_children: int = 0,
        has_pets: bool = False,
        check_in: str = "",
        check_out: str = "",
    ) -> str:
        """Compliance summary for a planned booking.

        Pulls in policy snippets relevant to the booking type and any flagged
        attributes (children, pets, destination-specific requirements).
        """
        queries: list[str] = [f"{booking_type} booking policy"]
        if num_children and int(num_children) > 0:
            queries.append("child policy age restrictions")
        if has_pets:
            queries.append("pet policy allowed breeds restrictions")
        if destination_country:
            queries.append(f"travel requirements {destination_country}")

        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        for q in queries:
            for doc in _semantic_search(mongo.POLICIES, mongo.POLICIES_INDEX, q, 2):
                key = doc.get("pageContent", "")[:120]
                if key in seen:
                    continue
                seen.add(key)
                results.append(doc)
        return _format_json(
            {
                "queries": queries,
                "matched_policies": results,
                "context": {
                    "booking_type": booking_type,
                    "destination_country": destination_country or None,
                    "num_children": int(num_children),
                    "has_pets": bool(has_pets),
                    "check_in": check_in or None,
                    "check_out": check_out or None,
                },
            }
        )
