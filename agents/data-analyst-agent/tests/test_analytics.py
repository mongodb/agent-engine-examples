from __future__ import annotations

from data_analyst_agent.flows.analytics import analytics_summary, build_analytics_artifacts


def test_analytics_summary_handles_empty_result_sets() -> None:
    summary = analytics_summary([], [])

    assert "No Customer records matched" in summary


def test_in_memory_analytics_artifacts_omit_live_mongodb_queries() -> None:
    artifacts = build_analytics_artifacts(
        [
            {
                "pedal_count": 1,
                "label": "1 pedal",
                "loss_frequency_per_1000": 71.5,
            }
        ],
        [
            {
                "quarter": "2025-Q1",
                "pedal_count": 1,
                "label": "1 pedal",
                "share_pct": 42.0,
            }
        ],
        include_mongodb_queries=False,
    )

    assert [artifact["kind"] for artifact in artifacts] == ["chart", "chart"]
    assert all("image_base64" not in artifact for artifact in artifacts)


def test_seeded_analytics_artifacts_include_query_panels() -> None:
    artifacts = build_analytics_artifacts([], [], include_mongodb_queries=True)

    assert [artifact["kind"] for artifact in artifacts] == [
        "mongodb_query",
        "chart",
        "mongodb_query",
        "chart",
    ]
