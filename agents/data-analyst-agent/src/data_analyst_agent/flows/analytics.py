"""Analytics flow nodes for the data analyst graph."""

from __future__ import annotations

import json
from typing import Any, cast

from data_analyst_agent.artifacts import (
    Artifact,
    build_cohort_mix_chart_artifact,
    build_loss_frequency_chart_artifact,
    build_mongodb_query_artifact,
)
from data_analyst_agent.data_store import (
    CUSTOMER_COLLECTION,
    DemoDataStore,
    build_cohort_mix_pipeline,
    build_loss_frequency_pipeline,
)
from data_analyst_agent.flow_messages import (
    assistant_with_artifacts,
    latest_tool_json,
    request_tools,
    task_status_messages,
    tool_call,
)
from data_analyst_agent.state import DataAnalystState


def start_data_tools(state: DataAnalystState) -> dict[str, Any]:
    return {
        "messages": [
            *task_status_messages(
                "analytics_flow",
                "Build and execute validated Customer aggregation pipelines.",
                "Built validated pipelines",
            ),
            request_tools(
                tool_call("compare_loss_frequency"),
                tool_call("cohort_mix_over_time"),
            ),
        ],
        "pending_tool_flow": "analytics_data",
    }


def start_chart_tools(state: DataAnalystState) -> dict[str, Any]:
    analytics_result = state.get("analytics_result")
    if not analytics_result:
        analytics_result = {
            "loss_frequency": latest_tool_json(state, "compare_loss_frequency"),
            "cohort_mix": latest_tool_json(state, "cohort_mix_over_time"),
        }
    analytics_result_json = json.dumps(analytics_result, ensure_ascii=True)
    return {
        "messages": [
            request_tools(
                tool_call(
                    "plot_loss_frequency_chart",
                    {"analytics_result_json": analytics_result_json},
                ),
                tool_call(
                    "plot_cohort_mix_trend_chart",
                    {"analytics_result_json": analytics_result_json},
                ),
            )
        ],
        "analytics_result": analytics_result,
        "pending_tool_flow": "analytics_charts",
    }


def present(data_store: DemoDataStore):
    def node(state: DataAnalystState) -> dict[str, Any]:
        analytics_result = state.get("analytics_result")
        if analytics_result:
            loss_rows = cast(list[dict[str, Any]], analytics_result.get("loss_frequency", []))
            mix_rows = cast(list[dict[str, Any]], analytics_result.get("cohort_mix", []))
        else:
            loss_rows = cast(
                list[dict[str, Any]], latest_tool_json(state, "compare_loss_frequency")
            )
            mix_rows = cast(list[dict[str, Any]], latest_tool_json(state, "cohort_mix_over_time"))
            analytics_result = {"loss_frequency": loss_rows, "cohort_mix": mix_rows}

        artifacts = build_analytics_artifacts(
            loss_rows,
            mix_rows,
            include_mongodb_queries=data_store.has_mongo_customer_data(),
        )
        content = analytics_summary(loss_rows, mix_rows)
        return {
            "messages": [
                *task_status_messages(
                    "analytics_flow",
                    "Summarize controlled loss frequency and cohort-mix results.",
                    "Executed analytics and presented result",
                ),
                assistant_with_artifacts(content, artifacts),
            ],
            "analytics_result": analytics_result,
            "artifacts": artifacts,
        }

    return node


def build_analytics_artifacts(
    loss_rows: list[dict[str, Any]],
    mix_rows: list[dict[str, Any]],
    *,
    include_mongodb_queries: bool = True,
) -> list[Artifact]:
    artifacts: list[Artifact] = []
    if include_mongodb_queries:
        artifacts.append(
            build_mongodb_query_artifact(
                artifact_id="artifact-query-cohort-comparison",
                title="Request 1 on Customer",
                collection=CUSTOMER_COLLECTION,
                pipeline=build_loss_frequency_pipeline(),
                summary="Aggregate Customer records by vehicle.pedal_count for controlled loss frequency.",
            )
        )
    artifacts.append(
        build_loss_frequency_chart_artifact(
            artifact_id="artifact-chart-loss-frequency",
            title="Loss frequency by pedal cohort",
            rows=loss_rows,
        )
    )
    if include_mongodb_queries:
        artifacts.append(
            build_mongodb_query_artifact(
                artifact_id="artifact-query-cohort-mix",
                title="Request 2 on Customer",
                collection=CUSTOMER_COLLECTION,
                pipeline=build_cohort_mix_pipeline(),
                summary="Aggregate Customer book_quarters to show pedal-cohort mix over time.",
            )
        )
    artifacts.append(
        build_cohort_mix_chart_artifact(
            artifact_id="artifact-chart-cohort-mix",
            title="Cohort mix by quarter",
            rows=mix_rows,
        )
    )
    return artifacts


def analytics_summary(
    loss_rows: list[dict[str, Any]],
    mix_rows: list[dict[str, Any]],
) -> str:
    if not loss_rows or not mix_rows:
        return (
            "Analytics flow complete. No Customer records matched the requested "
            "cohort filters. Seed Customer data or adjust the source data before drawing "
            "a rating conclusion."
        )

    best = min(loss_rows, key=lambda row: row["loss_frequency_per_1000"])
    latest_quarter = max(row["quarter"] for row in mix_rows)
    latest_mix = [row for row in mix_rows if row["quarter"] == latest_quarter]
    mix_text = ", ".join(
        f"{row['label']}: {row['share_pct']}%"
        for row in sorted(latest_mix, key=lambda row: row["pedal_count"])
    )
    rows_text = "\n".join(
        "- {label}: {freq} claims per 1000 policies across {count} policies".format(
            label=row["label"],
            freq=row["loss_frequency_per_1000"],
            count=row["policy_count"],
        )
        for row in loss_rows
    )
    return (
        "Analytics flow complete. After controlling to valid age, zip, and annual mileage "
        f"exposures, {best['label']} vehicles show the lowest observed loss frequency.\n\n"
        f"{rows_text}\n\n"
        f"In {latest_quarter}, cohort mix is {mix_text}."
    )
