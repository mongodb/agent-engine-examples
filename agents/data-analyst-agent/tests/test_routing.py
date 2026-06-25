from __future__ import annotations

from data_analyst_agent.routing import select_flow_sequence


def test_combined_investigation_and_recommendation_prompt_runs_full_workflow() -> None:
    assert select_flow_sequence(
        "Investigate the pedal-cohort result with claims narratives and provide a rating recommendation",
        procedural_memory_matches=False,
    ) == ["analytics_flow", "investigation_flow", "interpretation_flow"]
