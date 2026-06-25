from __future__ import annotations

import pytest

from travel_agent import tools as travel_tools


def test_partner_cost_threshold_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARTNER_COST_THRESHOLD", "900")
    assert travel_tools.partner_cost_threshold() == 900.0


def test_partner_cost_threshold_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARTNER_COST_THRESHOLD", raising=False)
    assert travel_tools.partner_cost_threshold() == 650.0


def test_prioritize_impacted_pnrs_sorted_by_priority() -> None:
    sample = [
        {
            "pnr": "TRV-00001",
            "traveler_type": "standard",
            "tier": "silver",
            "special_service_codes": [],
        },
        {
            "pnr": "TRV-00002",
            "traveler_type": "unaccompanied_minor",
            "tier": "standard",
            "special_service_codes": [],
        },
        {
            "pnr": "TRV-00003",
            "traveler_type": "standard",
            "tier": "platinum",
            "special_service_codes": [],
        },
    ]
    prioritized = travel_tools.prioritize_impacted_pnrs(sample)
    assert prioritized[0]["pnr"] == "TRV-00002"
    assert prioritized[0]["queue_bucket"] == "manual_review"
    assert prioritized[1]["pnr"] == "TRV-00003"


def test_default_disruption_is_loaded() -> None:
    disruption = travel_tools.get_disruption_event_data(travel_tools.DEFAULT_DISRUPTION_ID)
    assert disruption is not None
    assert disruption["disruption_id"] == travel_tools.DEFAULT_DISRUPTION_ID


def test_score_reaccommodation_options_returns_ranked() -> None:
    pnrs = travel_tools.list_impacted_pnrs_data(travel_tools.DEFAULT_DISRUPTION_ID)
    assert pnrs, "expected at least one seeded PNR for the default disruption"
    pnr = pnrs[0]["pnr"]
    ranked = travel_tools.score_reaccommodation_options_data(pnr)
    assert ranked, "expected at least one ranked option"
    assert all("score" in option for option in ranked)
    scores = [option["score"] for option in ranked]
    assert scores == sorted(scores, reverse=True)
