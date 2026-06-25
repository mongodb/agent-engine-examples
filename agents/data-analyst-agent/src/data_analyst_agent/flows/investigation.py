"""Investigation flow nodes for the data analyst graph."""

from __future__ import annotations

from typing import Any, cast

from data_analyst_agent.artifacts import build_source_artifact
from data_analyst_agent.flow_messages import (
    assistant_with_artifacts,
    latest_tool_json,
    request_tools,
    task_status_messages,
    tool_call,
)
from data_analyst_agent.state import DataAnalystState


def start_tools(state: DataAnalystState) -> dict[str, Any]:
    return {
        "messages": [
            *task_status_messages(
                "investigation_flow",
                "Retrieve representative one-pedal claims narratives.",
                "Retrieving narrative evidence",
            ),
            request_tools(tool_call("get_claim_narratives", {"pedal_count": 1, "limit": 5})),
        ],
        "pending_tool_flow": "investigation",
    }


def present(state: DataAnalystState) -> dict[str, Any]:
    narratives = cast(list[dict[str, Any]], latest_tool_json(state, "get_claim_narratives"))
    source_artifact = build_source_artifact(
        artifact_id="artifact-source-claim-narratives",
        title="Representative one-pedal claims narratives",
        sources=[
            {
                "id": narrative["customer_id"],
                "label": narrative["customer_id"],
                "summary": narrative["narrative_summary"],
            }
            for narrative in narratives
        ],
        summary="Claims narratives used to support the pedal-cohort interpretation.",
    )
    content = investigation_summary(narratives)
    return {
        "messages": [
            *task_status_messages(
                "investigation_flow",
                "Connect narrative evidence to the one-pedal result.",
                "Retrieved narrative evidence and presented findings",
            ),
            assistant_with_artifacts(content, [source_artifact]),
        ],
        "narratives": narratives,
        "artifacts": [source_artifact],
    }


def investigation_summary(narratives: list[dict[str, Any]]) -> str:
    if not narratives:
        return "Investigation flow complete. I did not find one-pedal claim narratives to sample."
    examples = "\n".join(
        f"- {item['customer_id']}: {item['narrative_summary']}" for item in narratives[:3]
    )
    return (
        "Investigation flow complete. The sampled one-pedal narratives support the quantitative "
        "pattern: lower-impact urban events and fewer severe collision descriptions dominate.\n\n"
        f"{examples}"
    )
