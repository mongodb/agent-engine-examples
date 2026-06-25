"""Mock tools and synthetic seed data for the Travel Agent demo."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_DISRUPTION_ID = "DISR-1001"


def partner_cost_threshold() -> float:
    """Read the partner approval threshold from the environment at call time.

    Resolved lazily so it stays in sync with the matching helper in main.py
    and reflects test or runtime overrides instead of import-time values.
    """
    return float(os.environ.get("PARTNER_COST_THRESHOLD", "650"))


_next_hold_num = 1200
_next_ticket_num = 7300
_next_voucher_num = 4100
_next_hotel_num = 2200
_next_notice_num = 9000

_pending_holds: dict[tuple[str, str], dict[str, Any]] = {}


def _load_json(filename: str) -> Any:
    return json.loads((DATA_DIR / filename).read_text())


DISRUPTIONS = {item["disruption_id"]: item for item in _load_json("disruptions.json")}
POLICIES = _load_json("policies.json")
AIRPORTS = {item["code"]: item for item in _load_json("airports.json")}
BASE_PNRS = _load_json("pnrs.json")
INVENTORY = _load_json("inventory.json")


def _base_booking_template(disruption_id: str, pnr: str) -> dict[str, Any]:
    disruption = DISRUPTIONS[disruption_id]
    return {
        "pnr": pnr,
        "current_flight": disruption["flight_number"],
        "origin": disruption["origin"],
        "destination": disruption["destination"],
        "scheduled_departure": disruption["scheduled_departure"],
        "scheduled_arrival": disruption["scheduled_arrival"],
    }


def _build_all_pnrs() -> list[dict[str, Any]]:
    records = list(BASE_PNRS)
    base_counts: dict[str, int] = {}
    existing_pnrs = {record["pnr"] for record in records}
    for record in BASE_PNRS:
        disruption_id = record["disruption_id"]
        base_counts[disruption_id] = base_counts.get(disruption_id, 0) + 1

    target_disruptions = sorted(set(base_counts) | {DEFAULT_DISRUPTION_ID})
    for disruption_index, disruption_id in enumerate(target_disruptions):
        disruption = DISRUPTIONS.get(disruption_id)
        if disruption is None:
            continue
        desired_count = int(disruption.get("impacted_passenger_count", 0))
        missing_count = max(0, desired_count - base_counts.get(disruption_id, 0))
        next_suffix = 500 + disruption_index * 100
        generated = 0
        while generated < missing_count:
            pnr = f"TRV-{next_suffix:05d}"
            next_suffix += 1
            if pnr in existing_pnrs:
                continue
            existing_pnrs.add(pnr)
            generated += 1
            records.append(
                {
                    "pnr": pnr,
                    "disruption_id": disruption_id,
                    "passenger_name": f"Standard Traveler {disruption_index + 1}-{generated}",
                    "party_size": 1,
                    "traveler_type": "standard",
                    "tier": "silver" if generated <= 4 else "standard",
                    "cabin": "economy",
                    "special_service_codes": [],
                    "needs_same_day_arrival": False,
                    "corporate_account": "",
                    "visa_safe_routing_only": False,
                    "preference_notes": [
                        "Prefer direct flights when available",
                        "Send SMS updates",
                    ],
                    "history_notes": [],
                    "queue_bucket_hint": "auto_rebook",
                    "booking": {
                        **_base_booking_template(disruption_id, pnr),
                        "fare_brand": "economy-flex",
                    },
                }
            )
    return records


ALL_PNRS = _build_all_pnrs()
PNR_INDEX = {record["pnr"]: record for record in ALL_PNRS}


def _priority_score(record: dict[str, Any]) -> int:
    score = 0
    if record.get("traveler_type") == "unaccompanied_minor":
        score += 120
    if any(code in record.get("special_service_codes", []) for code in ["WCHR", "MEDA"]):
        score += 110
    if record.get("tier") == "platinum":
        score += 80
    elif record.get("tier") == "gold":
        score += 55
    if record.get("needs_same_day_arrival"):
        score += 60
    if record.get("traveler_type") == "family":
        score += 35
    if record.get("corporate_account"):
        score += 20
    return score


def _queue_bucket(record: dict[str, Any]) -> str:
    if record.get("queue_bucket_hint"):
        return record["queue_bucket_hint"]
    if record.get("traveler_type") == "unaccompanied_minor":
        return "manual_review"
    if any(code in record.get("special_service_codes", []) for code in ["WCHR", "MEDA"]):
        return "manual_review"
    if record.get("tier") == "platinum" or record.get("needs_same_day_arrival"):
        return "approval_queue"
    return "auto_rebook"


def get_disruption_event_data(disruption_id: str) -> dict[str, Any] | None:
    return DISRUPTIONS.get(disruption_id)


def list_impacted_pnrs_data(disruption_id: str) -> list[dict[str, Any]]:
    return [record for record in ALL_PNRS if record["disruption_id"] == disruption_id]


def prioritize_impacted_pnrs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prioritized = []
    for record in records:
        item = dict(record)
        item["priority_score"] = _priority_score(record)
        item["queue_bucket"] = _queue_bucket(record)
        prioritized.append(item)
    prioritized.sort(key=lambda item: (-item["priority_score"], item["pnr"]))
    return prioritized


def build_batch_queue(disruption_id: str, limit: int) -> dict[str, list[dict[str, Any]]]:
    prioritized = prioritize_impacted_pnrs(list_impacted_pnrs_data(disruption_id))[:limit]
    queues = {
        "auto_rebook": [],
        "approval_queue": [],
        "manual_review": [],
    }
    for record in prioritized:
        queues[record["queue_bucket"]].append(
            {
                "pnr": record["pnr"],
                "passenger_name": record["passenger_name"],
                "traveler_type": record["traveler_type"],
                "tier": record["tier"],
                "reason": ", ".join(record.get("special_service_codes", []))
                or record["traveler_type"],
            }
        )
    return queues


def get_passenger_context_data(pnr: str) -> dict[str, Any] | None:
    return PNR_INDEX.get(pnr)


def get_booking_record_data(pnr: str) -> dict[str, Any] | None:
    passenger = PNR_INDEX.get(pnr)
    if passenger is None:
        return None
    return passenger["booking"]


def _policy_for_profile(passenger: dict[str, Any], disruption_type: str) -> dict[str, Any]:
    base = dict(POLICIES["weather_cancellation"])
    base["disruption_type"] = disruption_type
    base["priority_treatment"] = passenger.get("tier") in {"gold", "platinum"}
    base["manual_review_required"] = passenger.get("traveler_type") == "unaccompanied_minor" or any(
        code in passenger.get("special_service_codes", []) for code in ["WCHR", "MEDA"]
    )
    return base


def _options_for_pnr(pnr: str) -> list[dict[str, Any]]:
    return INVENTORY.get("pnr_overrides", {}).get(pnr, INVENTORY["generic_options"])


def search_alternative_inventory_data(
    pnr: str,
    constraints: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    passenger = get_passenger_context_data(pnr)
    if passenger is None:
        return []
    options = [dict(option) for option in _options_for_pnr(pnr)]
    constraints = constraints or {}
    for option in options:
        option.setdefault("pnr", pnr)
        option.setdefault("destination", passenger["booking"]["destination"])
        option.setdefault("origin", passenger["booking"]["origin"])
        if constraints.get("require_same_cabin"):
            option["eligible"] = option.get("same_cabin", False)
        else:
            option["eligible"] = True
    return options


def _approval_reasons(passenger: dict[str, Any], option: dict[str, Any]) -> list[str]:
    reasons = []
    if option.get("additional_cost", 0) > partner_cost_threshold():
        reasons.append("cost exceeds partner approval threshold")
    if option.get("downgrade") and passenger.get("tier") in {"gold", "platinum"}:
        reasons.append("premium traveler downgrade")
    if passenger.get("traveler_type") == "unaccompanied_minor":
        reasons.append("unaccompanied minor requires supervisor review")
    if any(code in passenger.get("special_service_codes", []) for code in ["WCHR", "MEDA"]):
        reasons.append("special assistance traveler requires manual validation")
    if passenger.get("visa_safe_routing_only") and option.get("via_country") not in (None, "US"):
        reasons.append("routing crosses a non-approved transit country")
    return reasons


def score_reaccommodation_options_data(pnr: str) -> list[dict[str, Any]]:
    passenger = get_passenger_context_data(pnr)
    if passenger is None:
        return []
    options = search_alternative_inventory_data(pnr)
    ranked = []
    for option in options:
        score = 100
        score -= int(option.get("arrival_delay_hours", 0)) * 2
        score += 16 if option.get("same_cabin") else -18
        score += (
            20 if option.get("same_day_arrival") and passenger.get("needs_same_day_arrival") else 0
        )
        score += 8 if option.get("direct") else -8 * int(option.get("connection_count", 0))
        score -= 24 if option.get("overnight_required") else 0
        score -= 14 if option.get("downgrade") else 0
        score -= 8 if option.get("baggage_risk") else 0
        score -= (
            5
            if option.get("provider") == "partner" and not passenger.get("needs_same_day_arrival")
            else 0
        )
        approval_reasons = _approval_reasons(passenger, option)
        hotel_eligible = bool(option.get("overnight_required"))
        voucher_amount = 40 if hotel_eligible else 0
        ranked.append(
            {
                **option,
                "score": score,
                "approval_required": bool(approval_reasons),
                "approval_reasons": approval_reasons,
                "hotel_eligible": hotel_eligible,
                "voucher_amount": voucher_amount,
            }
        )
    ranked.sort(key=lambda item: (-item["score"], item["option_id"]))
    return ranked


def _option_lookup(pnr: str, option_id: str) -> dict[str, Any] | None:
    for option in score_reaccommodation_options_data(pnr):
        if option["option_id"] == option_id:
            return option
    return None


def get_disruption_event(disruption_id: str) -> str:
    """Return the disruption record for a specific disruption ID."""
    disruption = get_disruption_event_data(disruption_id)
    if disruption is None:
        return json.dumps({"status": "error", "message": f"Unknown disruption_id: {disruption_id}"})
    return json.dumps(disruption, indent=2)


def list_impacted_pnrs(disruption_id: str) -> str:
    """List impacted passengers for a disruption, including queue priority metadata."""
    impacted = prioritize_impacted_pnrs(list_impacted_pnrs_data(disruption_id))
    return json.dumps({"disruption_id": disruption_id, "impacted": impacted}, indent=2)


def get_passenger_context(pnr: str) -> str:
    """Return the full synthetic passenger profile for a given PNR."""
    passenger = get_passenger_context_data(pnr)
    if passenger is None:
        return json.dumps({"status": "error", "message": f"Unknown PNR: {pnr}"})
    return json.dumps(passenger, indent=2)


def get_booking_record(pnr: str) -> str:
    """Return the current disrupted booking for a passenger."""
    booking = get_booking_record_data(pnr)
    if booking is None:
        return json.dumps({"status": "error", "message": f"Unknown PNR: {pnr}"})
    return json.dumps(booking, indent=2)


def get_irrops_policy(
    disruption_type: str,
    traveler_type: str,
    cabin: str = "economy",
    tier: str = "standard",
) -> str:
    """Return the applicable recovery policy for a disruption and traveler profile."""
    passenger = {
        "traveler_type": traveler_type,
        "special_service_codes": [],
        "tier": tier,
        "cabin": cabin,
    }
    return json.dumps(_policy_for_profile(passenger, disruption_type), indent=2)


def search_alternative_inventory(pnr: str, require_same_cabin: bool = False) -> str:
    """Return candidate recovery options for the passenger."""
    options = search_alternative_inventory_data(pnr, {"require_same_cabin": require_same_cabin})
    return json.dumps({"pnr": pnr, "options": options}, indent=2)


def score_reaccommodation_options(pnr: str) -> str:
    """Rank candidate recovery options for the passenger with approval flags."""
    ranked = score_reaccommodation_options_data(pnr)
    return json.dumps({"pnr": pnr, "ranked_options": ranked}, indent=2)


def hold_reaccommodation_option(pnr: str, option_id: str) -> str:
    """Place a temporary hold on a specific replacement option."""
    global _next_hold_num
    option = _option_lookup(pnr, option_id)
    if option is None:
        return json.dumps(
            {"status": "error", "message": f"Option {option_id} not available for {pnr}"}
        )
    hold_id = f"HOLD-{_next_hold_num}"
    _next_hold_num += 1
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=20)
    _pending_holds[(pnr, option_id)] = {"hold_id": hold_id, "expires_at": expires_at.isoformat()}
    return json.dumps(
        {
            "status": "held",
            "pnr": pnr,
            "option_id": option_id,
            "hold_id": hold_id,
            "expires_at": expires_at.isoformat(),
            "provider": option.get("carrier"),
        },
        indent=2,
    )


def reissue_ticket(pnr: str, option_id: str) -> str:
    """Issue the passenger onto the selected replacement option."""
    global _next_ticket_num
    option = _option_lookup(pnr, option_id)
    if option is None:
        return json.dumps(
            {"status": "error", "message": f"Option {option_id} not available for {pnr}"}
        )
    ticket_number = f"074-88{_next_ticket_num}"
    _next_ticket_num += 1
    hold = _pending_holds.get((pnr, option_id), {})
    return json.dumps(
        {
            "status": "ticketed",
            "pnr": pnr,
            "option_id": option_id,
            "ticket_number": ticket_number,
            "hold_id": hold.get("hold_id"),
            "carrier": option.get("carrier"),
            "flight_number": option.get("flight_number"),
            "departure_time": option.get("departure_time"),
            "arrival_time": option.get("arrival_time"),
            "cabin": option.get("cabin"),
        },
        indent=2,
    )


def create_travel_voucher(pnr: str, voucher_type: str, amount: float) -> str:
    """Issue a synthetic travel voucher for the passenger."""
    global _next_voucher_num
    voucher_id = f"VCHR-{_next_voucher_num}"
    _next_voucher_num += 1
    return json.dumps(
        {
            "status": "issued",
            "pnr": pnr,
            "voucher_id": voucher_id,
            "voucher_type": voucher_type,
            "amount": amount,
            "currency": "USD",
        },
        indent=2,
    )


def book_hotel(pnr: str, city: str, nights: int) -> str:
    """Book a synthetic airport hotel for an overnight disruption."""
    global _next_hotel_num
    hotel_id = f"HOTEL-{_next_hotel_num}"
    _next_hotel_num += 1
    airport = AIRPORTS.get(city, {})
    return json.dumps(
        {
            "status": "confirmed",
            "pnr": pnr,
            "hotel_booking_id": hotel_id,
            "city": city,
            "nights": nights,
            "property": airport.get("default_hotel", f"{city} Airport Hotel"),
        },
        indent=2,
    )


def send_trip_update(pnr: str, channel: str, summary: str) -> str:
    """Send a synthetic traveler notification with the final recovery summary."""
    global _next_notice_num
    message_id = f"MSG-{_next_notice_num}"
    _next_notice_num += 1
    return json.dumps(
        {
            "status": "sent",
            "pnr": pnr,
            "channel": channel,
            "message_id": message_id,
            "summary": summary,
        },
        indent=2,
    )
