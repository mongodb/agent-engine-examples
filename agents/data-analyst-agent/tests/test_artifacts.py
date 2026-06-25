from __future__ import annotations

from langchain_core.messages import AIMessage

from data_analyst_agent.artifacts import (
    build_chart_artifact,
    build_message_artifact_metadata,
    build_mongodb_query_artifact,
    build_review_summary_artifact,
    build_source_artifact,
)


def test_message_artifacts_use_sdk_metadata_contract() -> None:
    artifacts = [
        build_mongodb_query_artifact(
            artifact_id="artifact-query-cohort-comparison",
            title="Request 1 on Customer",
            collection="Customer",
            pipeline=[{"$match": {"vehicle.pedal_count": {"$in": [1, 2, 3]}}}],
            summary="Compare loss frequency by pedal cohort.",
        ),
        build_chart_artifact(
            artifact_id="artifact-chart-loss-frequency",
            title="Loss frequency by pedal cohort",
            chart_type="bar",
            label_key="label",
            metric="loss_frequency_per_1000",
            data=[{"pedal_count": 1, "label": "1 pedal", "loss_frequency_per_1000": 71.5}],
            summary="Claims per 1000 policies by pedal-count cohort.",
        ),
    ]

    message = AIMessage(
        content="Here is the cohort comparison.",
        additional_kwargs=build_message_artifact_metadata(artifacts),
    )

    assert "artifact_ids" not in message.additional_kwargs
    assert [artifact["id"] for artifact in message.additional_kwargs["artifacts"]] == [
        "artifact-query-cohort-comparison",
        "artifact-chart-loss-frequency",
    ]
    assert message.response_metadata == {}
    assert artifacts[1]["kind"] == "chart"
    assert artifacts[1]["chart_type"] == "bar"
    assert artifacts[1]["metric"] == "loss_frequency_per_1000"
    assert artifacts[0]["pipeline_summary"] == "Compare loss frequency by pedal cohort."


def test_source_and_review_artifacts_match_playground_contract() -> None:
    source_artifact = build_source_artifact(
        artifact_id="artifact-source-claim-narratives",
        title="Representative one-pedal claims narratives",
        sources=[
            {"id": "C000001", "label": "C000001", "summary": "Regenerative braking limited impact."}
        ],
        summary="Claims narratives used to support the recommendation.",
    )
    review_artifact = build_review_summary_artifact(
        artifact_id="artifact-review-rating-recommendation",
        title="Pedal-count rating recommendation",
        summary="Recommend 0.83 relativity.",
        decision={"decision_type": "rating_recommendation"},
    )

    assert source_artifact["content"] == "C000001: Regenerative braking limited impact."
    assert review_artifact["status"] == "awaiting_human_review"
    assert review_artifact["decision_type"] == "rating_recommendation"
