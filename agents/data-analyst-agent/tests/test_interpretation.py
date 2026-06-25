from __future__ import annotations

from data_analyst_agent.flows.interpretation import build_recommendation


def test_build_recommendation_handles_missing_required_cohorts() -> None:
    recommendation = build_recommendation(
        {
            "loss_frequency": [
                {
                    "pedal_count": 1,
                    "label": "1 pedal",
                    "loss_frequency_per_1000": 71.5,
                }
            ],
            "cohort_mix": [],
        },
        [],
    )

    assert recommendation["filing_required"] is False
    assert "Unable to prepare a rating recommendation" in recommendation["summary"]
    assert recommendation["evidence"] == ["Missing analytics rows for: 2 pedals."]
