"""Seed the local MongoDB cluster with sample hotels + travel policies.

Generates a fixed catalogue (no LLM dependency) so seeding works offline.
If ``VOYAGE_API_KEY`` is set, document embeddings are stored on the
``embedding`` field and Atlas vector search indexes are created. Otherwise
the seed still runs and the agent falls back to keyword search at query
time.

Run with:
    uv run holiday-assistant-seed
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

from dotenv import load_dotenv

from holiday_assistant_agent import mongo
from holiday_assistant_agent.embeddings import embed_documents

logger = logging.getLogger(__name__)


# ─── Sample hotels ───────────────────────────────────────────────────────────


HOTELS: list[dict[str, Any]] = [
    {
        "hotel_id": "h-001",
        "name": "Hotel Catalonia Barcelona Plaza",
        "city": "Barcelona",
        "country": "Spain",
        "country_code": "ES",
        "region": "Catalonia",
        "address": "Plaça d'Espanya, 6-8, 08014 Barcelona",
        "star_rating": 4,
        "property_type": "hotel",
        "room_types": [
            {"type": "standard", "max_occupancy": 2, "price_per_night": 145, "currency": "EUR",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 7},
            {"type": "deluxe", "max_occupancy": 2, "price_per_night": 220, "currency": "EUR",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 7},
            {"type": "suite", "max_occupancy": 4, "price_per_night": 410, "currency": "EUR",
             "breakfast_included": True, "refundable": False, "cancellation_deadline_days": None},
        ],
        "amenities": ["pool", "spa", "gym", "free wifi", "restaurant", "bar"],
        "check_in_time": "15:00",
        "check_out_time": "11:00",
        "availability": {"available_from": "2026-06-01", "available_until": "2026-12-31",
                          "rooms_available": 18},
        "rating": {"score": 8.6, "review_count": 4321},
        "distance_to_centre_km": 1.2,
        "notes": "Modernist 4-star with rooftop pool, walking distance to Las Ramblas.",
    },
    {
        "hotel_id": "h-002",
        "name": "Hilton Barcelona",
        "city": "Barcelona",
        "country": "Spain",
        "country_code": "ES",
        "region": "Catalonia",
        "address": "Avinguda Diagonal 589-591, 08014 Barcelona",
        "star_rating": 4,
        "property_type": "hotel",
        "room_types": [
            {"type": "standard", "max_occupancy": 2, "price_per_night": 169, "currency": "EUR",
             "breakfast_included": False, "refundable": True, "cancellation_deadline_days": 5},
            {"type": "deluxe", "max_occupancy": 2, "price_per_night": 249, "currency": "EUR",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 5},
            {"type": "family", "max_occupancy": 4, "price_per_night": 320, "currency": "EUR",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 5},
        ],
        "amenities": ["pool", "gym", "free wifi", "restaurant", "business centre"],
        "check_in_time": "15:00",
        "check_out_time": "12:00",
        "availability": {"available_from": "2026-06-01", "available_until": "2026-12-31",
                          "rooms_available": 24},
        "rating": {"score": 8.3, "review_count": 5811},
        "distance_to_centre_km": 2.6,
        "notes": "Reliable business 4-star on Avinguda Diagonal with metro access.",
    },
    {
        "hotel_id": "h-003",
        "name": "Pension Avinyo",
        "city": "Barcelona",
        "country": "Spain",
        "country_code": "ES",
        "region": "Catalonia",
        "address": "Carrer d'Avinyó, 42, 08002 Barcelona",
        "star_rating": 2,
        "property_type": "hostel",
        "room_types": [
            {"type": "standard", "max_occupancy": 2, "price_per_night": 79, "currency": "EUR",
             "breakfast_included": False, "refundable": True, "cancellation_deadline_days": 1},
        ],
        "amenities": ["free wifi", "shared lounge"],
        "check_in_time": "14:00",
        "check_out_time": "11:00",
        "availability": {"available_from": "2026-06-01", "available_until": "2026-12-31",
                          "rooms_available": 8},
        "rating": {"score": 7.5, "review_count": 612},
        "distance_to_centre_km": 0.5,
        "notes": "Budget Gothic Quarter pension steps from Plaça Reial.",
    },
    {
        "hotel_id": "h-004",
        "name": "Katikies Santorini",
        "city": "Oia",
        "country": "Greece",
        "country_code": "GR",
        "region": "Cyclades",
        "address": "Main Street, Oia 84702, Santorini",
        "star_rating": 5,
        "property_type": "boutique_hotel",
        "room_types": [
            {"type": "deluxe", "max_occupancy": 2, "price_per_night": 720, "currency": "EUR",
             "breakfast_included": True, "refundable": False, "cancellation_deadline_days": None},
            {"type": "suite", "max_occupancy": 2, "price_per_night": 1180, "currency": "EUR",
             "breakfast_included": True, "refundable": False, "cancellation_deadline_days": None},
        ],
        "amenities": ["infinity pool", "spa", "fine dining", "free wifi", "sea view"],
        "check_in_time": "15:00",
        "check_out_time": "12:00",
        "availability": {"available_from": "2026-04-15", "available_until": "2026-10-31",
                          "rooms_available": 6},
        "rating": {"score": 9.6, "review_count": 1280},
        "distance_to_centre_km": 0.3,
        "notes": "Iconic cliffside boutique hotel in Oia with infinity pools and caldera views.",
        "requires_cancellation_approval": True,
    },
    {
        "hotel_id": "h-005",
        "name": "Mystique Resort",
        "city": "Oia",
        "country": "Greece",
        "country_code": "GR",
        "region": "Cyclades",
        "address": "Oia, 84702, Santorini",
        "star_rating": 5,
        "property_type": "resort",
        "room_types": [
            {"type": "suite", "max_occupancy": 2, "price_per_night": 950, "currency": "EUR",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 14},
            {"type": "penthouse", "max_occupancy": 3, "price_per_night": 1450, "currency": "EUR",
             "breakfast_included": True, "refundable": False, "cancellation_deadline_days": None},
        ],
        "amenities": ["spa", "infinity pool", "private terraces", "fine dining", "free wifi"],
        "check_in_time": "15:00",
        "check_out_time": "12:00",
        "availability": {"available_from": "2026-04-15", "available_until": "2026-10-31",
                          "rooms_available": 4},
        "rating": {"score": 9.4, "review_count": 980},
        "distance_to_centre_km": 0.4,
        "notes": "Luxury Aegean cliffside resort with private terraces and award-winning spa.",
        "requires_cancellation_approval": True,
    },
    {
        "hotel_id": "h-006",
        "name": "Le Meurice Paris",
        "city": "Paris",
        "country": "France",
        "country_code": "FR",
        "region": "Île-de-France",
        "address": "228 Rue de Rivoli, 75001 Paris",
        "star_rating": 5,
        "property_type": "hotel",
        "room_types": [
            {"type": "deluxe", "max_occupancy": 2, "price_per_night": 980, "currency": "EUR",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 14},
            {"type": "suite", "max_occupancy": 3, "price_per_night": 1850, "currency": "EUR",
             "breakfast_included": True, "refundable": False, "cancellation_deadline_days": None},
        ],
        "amenities": ["spa", "michelin restaurant", "concierge", "fitness centre", "free wifi"],
        "check_in_time": "15:00",
        "check_out_time": "12:00",
        "availability": {"available_from": "2026-06-01", "available_until": "2026-12-31",
                          "rooms_available": 12},
        "rating": {"score": 9.3, "review_count": 2150},
        "distance_to_centre_km": 0.2,
        "notes": "Iconic palace hotel facing the Tuileries, home to a Michelin-starred restaurant.",
        "requires_cancellation_approval": True,
    },
    {
        "hotel_id": "h-007",
        "name": "Hôtel des Grands Boulevards",
        "city": "Paris",
        "country": "France",
        "country_code": "FR",
        "region": "Île-de-France",
        "address": "17 Boulevard Poissonnière, 75002 Paris",
        "star_rating": 4,
        "property_type": "boutique_hotel",
        "room_types": [
            {"type": "standard", "max_occupancy": 2, "price_per_night": 235, "currency": "EUR",
             "breakfast_included": False, "refundable": True, "cancellation_deadline_days": 3},
            {"type": "deluxe", "max_occupancy": 2, "price_per_night": 320, "currency": "EUR",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 3},
        ],
        "amenities": ["restaurant", "bar", "free wifi", "courtyard"],
        "check_in_time": "15:00",
        "check_out_time": "12:00",
        "availability": {"available_from": "2026-06-01", "available_until": "2026-12-31",
                          "rooms_available": 14},
        "rating": {"score": 8.9, "review_count": 1740},
        "distance_to_centre_km": 1.0,
        "notes": "Stylish boutique hotel in the 2nd arrondissement with a quiet courtyard restaurant.",
    },
    {
        "hotel_id": "h-008",
        "name": "Vila Vita Parc",
        "city": "Algarve",
        "country": "Portugal",
        "country_code": "PT",
        "region": "Algarve",
        "address": "Alporchinhos, 8400-450 Porches",
        "star_rating": 5,
        "property_type": "resort",
        "room_types": [
            {"type": "deluxe", "max_occupancy": 2, "price_per_night": 540, "currency": "EUR",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 14},
            {"type": "family", "max_occupancy": 4, "price_per_night": 760, "currency": "EUR",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 14},
            {"type": "suite", "max_occupancy": 4, "price_per_night": 1100, "currency": "EUR",
             "breakfast_included": True, "refundable": False, "cancellation_deadline_days": None},
        ],
        "amenities": ["beach access", "spa", "tennis", "kids club", "fine dining", "free wifi"],
        "check_in_time": "15:00",
        "check_out_time": "12:00",
        "availability": {"available_from": "2026-04-01", "available_until": "2026-11-30",
                          "rooms_available": 28},
        "rating": {"score": 9.5, "review_count": 1620},
        "distance_to_centre_km": 5.4,
        "notes": "Family-friendly Algarve cliff-top resort with private beach access and tennis.",
    },
    {
        "hotel_id": "h-009",
        "name": "Casa Algarvia",
        "city": "Lagos",
        "country": "Portugal",
        "country_code": "PT",
        "region": "Algarve",
        "address": "Rua da Barroca 12, 8600-505 Lagos",
        "star_rating": 3,
        "property_type": "apartment",
        "room_types": [
            {"type": "standard", "max_occupancy": 2, "price_per_night": 110, "currency": "EUR",
             "breakfast_included": False, "refundable": True, "cancellation_deadline_days": 3},
            {"type": "family", "max_occupancy": 4, "price_per_night": 175, "currency": "EUR",
             "breakfast_included": False, "refundable": True, "cancellation_deadline_days": 3},
        ],
        "amenities": ["kitchen", "free wifi", "rooftop terrace", "pet friendly"],
        "check_in_time": "16:00",
        "check_out_time": "11:00",
        "availability": {"available_from": "2026-04-01", "available_until": "2026-11-30",
                          "rooms_available": 5},
        "rating": {"score": 8.4, "review_count": 432},
        "distance_to_centre_km": 0.4,
        "notes": "Pet-friendly serviced apartment in Lagos old town, short walk to the marina.",
    },
    {
        "hotel_id": "h-010",
        "name": "Belmond Hotel Caruso",
        "city": "Ravello",
        "country": "Italy",
        "country_code": "IT",
        "region": "Amalfi Coast",
        "address": "Piazza San Giovanni del Toro 2, 84010 Ravello",
        "star_rating": 5,
        "property_type": "resort",
        "room_types": [
            {"type": "deluxe", "max_occupancy": 2, "price_per_night": 1150, "currency": "EUR",
             "breakfast_included": True, "refundable": False, "cancellation_deadline_days": None},
            {"type": "suite", "max_occupancy": 3, "price_per_night": 1850, "currency": "EUR",
             "breakfast_included": True, "refundable": False, "cancellation_deadline_days": None},
        ],
        "amenities": ["infinity pool", "spa", "fine dining", "concierge", "sea view"],
        "check_in_time": "15:00",
        "check_out_time": "12:00",
        "availability": {"available_from": "2026-04-15", "available_until": "2026-10-31",
                          "rooms_available": 6},
        "rating": {"score": 9.7, "review_count": 985},
        "distance_to_centre_km": 0.1,
        "notes": "Restored 11th-century palace perched above the Amalfi coast with cliff-edge pool.",
        "requires_cancellation_approval": True,
    },
    {
        "hotel_id": "h-011",
        "name": "Highland Glen Lodge",
        "city": "Inverness",
        "country": "United Kingdom",
        "country_code": "GB",
        "region": "Scottish Highlands",
        "address": "Glen Affric Road, IV3 8LA, Inverness",
        "star_rating": 4,
        "property_type": "hotel",
        "room_types": [
            {"type": "standard", "max_occupancy": 2, "price_per_night": 130, "currency": "GBP",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 3},
            {"type": "family", "max_occupancy": 4, "price_per_night": 220, "currency": "GBP",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 3},
        ],
        "amenities": ["restaurant", "free wifi", "fishing", "pet friendly", "log fires"],
        "check_in_time": "15:00",
        "check_out_time": "11:00",
        "availability": {"available_from": "2026-05-01", "available_until": "2026-10-31",
                          "rooms_available": 12},
        "rating": {"score": 8.7, "review_count": 540},
        "distance_to_centre_km": 6.8,
        "notes": "Pet-friendly Highlands lodge near Loch Ness with private river fishing.",
    },
    {
        "hotel_id": "h-012",
        "name": "Hotel Excelsior Dubrovnik",
        "city": "Dubrovnik",
        "country": "Croatia",
        "country_code": "HR",
        "region": "Dalmatia",
        "address": "Frana Supila 12, 20000 Dubrovnik",
        "star_rating": 5,
        "property_type": "hotel",
        "room_types": [
            {"type": "standard", "max_occupancy": 2, "price_per_night": 295, "currency": "EUR",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 7},
            {"type": "deluxe", "max_occupancy": 2, "price_per_night": 420, "currency": "EUR",
             "breakfast_included": True, "refundable": True, "cancellation_deadline_days": 7},
        ],
        "amenities": ["beach access", "pool", "spa", "free wifi", "sea view"],
        "check_in_time": "15:00",
        "check_out_time": "12:00",
        "availability": {"available_from": "2026-04-01", "available_until": "2026-11-30",
                          "rooms_available": 22},
        "rating": {"score": 9.0, "review_count": 1810},
        "distance_to_centre_km": 0.5,
        "notes": "Five-star hotel a short walk from Dubrovnik Old Town with private beach access.",
    },
]


# ─── Sample policies ─────────────────────────────────────────────────────────


POLICIES: list[dict[str, Any]] = [
    {
        "policy_id": "pol-booking-001",
        "title": "Booking Window Policy",
        "category": "booking",
        "content": (
            "Bookings may be made up to 18 months in advance and modified up to 48 hours "
            "before check-in subject to availability. Lead time for resorts during peak "
            "summer (July–August) is at least 14 days. Group bookings of 6 or more rooms "
            "must be confirmed at least 30 days before arrival. Last-minute bookings made "
            "within 24 hours of arrival require a non-refundable deposit equal to one "
            "night's stay."
        ),
        "applies_to": ["all_properties"],
        "effective_date": "2025-01-01",
        "last_updated": "2026-01-15",
        "version": "2.1",
    },
    {
        "policy_id": "pol-cancel-001",
        "title": "Cancellation and Refund Policy",
        "category": "cancellation",
        "content": (
            "Refundable bookings cancelled at least 7 days before check-in receive a "
            "full refund minus a 5% processing fee. Cancellations made between 3 and 7 "
            "days before check-in are refunded 50% of the total stay. Cancellations "
            "within 72 hours of arrival forfeit the first night. Non-refundable rates "
            "and resort suites cannot be cancelled. Force majeure cancellations (illness, "
            "natural disaster, government travel ban) are reviewed case-by-case with "
            "documentation."
        ),
        "applies_to": ["all_properties"],
        "effective_date": "2025-03-01",
        "last_updated": "2026-02-01",
        "version": "3.2",
    },
    {
        "policy_id": "pol-payment-001",
        "title": "Payment Terms Policy",
        "category": "payment",
        "content": (
            "We accept Visa, MasterCard, American Express, Apple Pay, Google Pay, and "
            "SEPA bank transfer. A deposit of 20% is taken at booking; the balance is "
            "charged 14 days before arrival for refundable rates and immediately for "
            "non-refundable rates. Currency is determined by the property's location. "
            "We do not store full card numbers — payment processing is performed by "
            "PCI-DSS Level 1 partners."
        ),
        "applies_to": ["all_properties"],
        "effective_date": "2025-01-01",
        "last_updated": "2026-01-10",
        "version": "1.4",
    },
    {
        "policy_id": "pol-child-001",
        "title": "Child Policy",
        "category": "child_policy",
        "content": (
            "Children under 2 stay free in a parent's room without an extra bed. "
            "Children 2–11 incur a €25 nightly extra-bed charge where an extra bed "
            "is requested. Children 12 and above are charged at adult rates. Some "
            "boutique hotels and adults-only resorts have a minimum age of 16; this "
            "is flagged on the property page. Cots are free of charge subject to "
            "availability and must be requested at booking."
        ),
        "applies_to": ["all_properties"],
        "effective_date": "2025-04-01",
        "last_updated": "2026-02-15",
        "version": "2.0",
    },
    {
        "policy_id": "pol-pet-001",
        "title": "Pet Policy",
        "category": "pet_policy",
        "content": (
            "Pets are welcome at properties tagged 'pet friendly' (typically apartments "
            "and select hotels). Up to two pets per room with a combined weight of 25kg "
            "are allowed. A nightly cleaning fee of €20 per pet applies. Service animals "
            "are welcome at every property regardless of pet policy. Owners are liable "
            "for any property damage. Pets must not be left unattended in rooms."
        ),
        "applies_to": ["pet_friendly_properties"],
        "effective_date": "2025-04-01",
        "last_updated": "2026-02-15",
        "version": "1.3",
    },
    {
        "policy_id": "pol-access-001",
        "title": "Accessibility Policy",
        "category": "accessibility",
        "content": (
            "All properties provide step-free access to a reception area and at least "
            "one accessible room with roll-in shower. WCAG 2.2 AA compliance is "
            "maintained on the booking site, including alt text and keyboard "
            "navigation. Accessible-room availability is published in real time. "
            "Service animals travel free. Guests requiring specific assistance should "
            "request it at booking so the property can prepare."
        ),
        "applies_to": ["all_properties"],
        "effective_date": "2025-01-01",
        "last_updated": "2026-01-30",
        "version": "1.2",
    },
    {
        "policy_id": "pol-data-001",
        "title": "Traveller Data Protection Policy",
        "category": "data_protection",
        "content": (
            "We comply with GDPR and the UK Data Protection Act 2018. Personal data is "
            "retained for 7 years for tax and contractual purposes after the last "
            "interaction, after which it is anonymised. Marketing consent is opt-in. "
            "Travellers may request export or deletion of their data at any time via "
            "privacy@example.com. We do not sell personal data."
        ),
        "applies_to": ["all_users"],
        "effective_date": "2024-05-25",
        "last_updated": "2026-01-15",
        "version": "4.0",
    },
    {
        "policy_id": "pol-insurance-001",
        "title": "Travel Insurance Recommendation",
        "category": "travel_insurance",
        "content": (
            "Travel insurance is strongly recommended for all bookings and mandatory for "
            "trips that include an inter-continental flight or a ski / extreme-sports "
            "package. We partner with Allianz Travel for one-click cover at checkout. "
            "Policies should include trip cancellation, medical, baggage and repatriation. "
            "EU residents should additionally carry an EHIC/GHIC card."
        ),
        "applies_to": ["all_bookings"],
        "effective_date": "2025-06-01",
        "last_updated": "2026-02-15",
        "version": "1.1",
    },
]


# ─── Page content builders ───────────────────────────────────────────────────


def _hotel_summary(h: dict[str, Any]) -> str:
    cheapest = min(h["room_types"], key=lambda r: r["price_per_night"])
    return (
        f"{h['name']} – a {h['star_rating']}-star "
        f"{h['property_type'].replace('_', ' ')} in {h['city']}, {h['country']}. "
        f"Located in {h['region']}, {h['distance_to_centre_km']}km from the city centre. "
        f"Rooms from {cheapest['currency']} {cheapest['price_per_night']}/night. "
        f"Amenities: {', '.join(h['amenities'][:5])}. "
        f"Check-in {h['check_in_time']}, check-out {h['check_out_time']}. "
        f"Rating {h['rating']['score']}/10 ({h['rating']['review_count']} reviews). "
        f"{h['notes']}"
    )


def _policy_summary(p: dict[str, Any]) -> str:
    return f"{p['title']}: {p['content']}"


# ─── Vector index helpers ────────────────────────────────────────────────────


def _ensure_search_index(collection: Any, index_name: str, dim: int) -> None:
    existing = []
    try:
        existing = list(collection.list_search_indexes())
    except Exception:
        pass
    for idx in existing:
        if idx.get("name") == index_name:
            logger.info("Search index %s already exists", index_name)
            return

    definition = {
        "name": index_name,
        "type": "vectorSearch",
        "definition": {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": dim,
                    "similarity": "cosine",
                }
            ]
        },
    }
    try:
        collection.database.command(
            "createSearchIndexes",
            collection.name,
            indexes=[definition],
        )
        logger.info("Created vector search index %s on %s", index_name, collection.name)
    except Exception as exc:
        logger.warning("Failed to create vector search index %s: %s", index_name, exc)


# ─── Seeding ─────────────────────────────────────────────────────────────────


def _embed_or_skip(texts: list[str]) -> list[list[float]] | None:
    if not os.environ.get("VOYAGE_API_KEY"):
        logger.info("VOYAGE_API_KEY not set — skipping embeddings; agent will use keyword search")
        return None
    try:
        return embed_documents(texts)
    except Exception as exc:
        logger.warning("Embedding generation failed (%s); seeding without embeddings", exc)
        return None


def seed_hotels() -> int:
    coll = mongo.hotels_collection()
    coll.delete_many({})
    summaries = [_hotel_summary(h) for h in HOTELS]
    embeddings = _embed_or_skip(summaries)

    docs = []
    for i, hotel in enumerate(HOTELS):
        doc = {"pageContent": summaries[i], "metadata": dict(hotel)}
        if embeddings is not None:
            doc["embedding"] = embeddings[i]
        docs.append(doc)
    coll.insert_many(docs)

    if embeddings is not None:
        dim = len(embeddings[0])
        _ensure_search_index(coll, mongo.HOTELS_INDEX, dim)
    return len(docs)


def seed_policies() -> int:
    coll = mongo.policies_collection()
    coll.delete_many({})
    contents = [_policy_summary(p) for p in POLICIES]
    embeddings = _embed_or_skip(contents)

    docs = []
    for i, policy in enumerate(POLICIES):
        doc = {"pageContent": contents[i], "metadata": dict(policy)}
        if embeddings is not None:
            doc["embedding"] = embeddings[i]
        docs.append(doc)
    coll.insert_many(docs)

    if embeddings is not None:
        dim = len(embeddings[0])
        _ensure_search_index(coll, mongo.POLICIES_INDEX, dim)
    return len(docs)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
    load_dotenv()

    client = mongo.get_client()
    client.admin.command("ping")
    logger.info("Connected to MongoDB at %s", os.environ.get("MONGODB_URI", "(default)"))

    n_hotels = seed_hotels()
    n_policies = seed_policies()

    # Ensure bookings collection exists.
    mongo.bookings_collection()
    logger.info("Seed complete: %d hotels, %d policies", n_hotels, n_policies)

    # Give the index a few seconds to come online if we just created it.
    if os.environ.get("VOYAGE_API_KEY"):
        time.sleep(3)


if __name__ == "__main__":
    sys.exit(main())
