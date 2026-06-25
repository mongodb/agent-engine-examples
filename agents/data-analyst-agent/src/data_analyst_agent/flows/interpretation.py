"""Interpretation, review, and writeback flow nodes for the data analyst graph."""

from __future__ import annotations

from typing import Any, cast

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from data_analyst_agent.artifacts import build_review_summary_artifact
from data_analyst_agent.data_store import DemoDataStore
from data_analyst_agent.flow_messages import (
    assistant_with_artifacts,
    latest_tool_json,
    request_tools,
    task_status_messages,
    tool_call,
)
from data_analyst_agent.review import (
    DEFAULT_RATING_ACTION,
    DEFAULT_RELATIVITY,
    build_review_interrupt_payload,
    normalize_review_decision,
)
from data_analyst_agent.state import DataAnalystState


def prepare_review(data_store: DemoDataStore):
    def node(state: DataAnalystState) -> dict[str, Any]:
        analytics_result = state.get("analytics_result") or {
            "loss_frequency": data_store.compare_loss_frequency(),
            "cohort_mix": data_store.cohort_mix_over_time(),
        }
        narratives = state.get("narratives") or data_store.claims_narratives(
            pedal_count=1,
            limit=5,
        )
        recommendation = build_recommendation(analytics_result, narratives)
        review_artifact = build_review_summary_artifact(
            artifact_id="artifact-review-rating-recommendation",
            title="Pedal-count rating recommendation",
            summary=recommendation["summary"],
            decision=recommendation,
        )
        content = (
            f"{recommendation['summary']}\n\n"
            "I need manager approval before writing this recommendation to audit_log."
        )
        return {
            "messages": [
                *task_status_messages(
                    "interpretation_flow",
                    "Prepare rating recommendation and review package.",
                    "Generated recommendation; awaiting approval",
                ),
                assistant_with_artifacts(content, [review_artifact]),
            ],
            "analytics_result": analytics_result,
            "narratives": narratives,
            "recommendation": recommendation,
            "artifacts": [review_artifact],
        }

    return node


def human_review(data_store: DemoDataStore):
    def node(state: DataAnalystState) -> dict[str, Any]:
        recommendation = state.get("recommendation") or build_recommendation(
            {
                "loss_frequency": data_store.compare_loss_frequency(),
                "cohort_mix": data_store.cohort_mix_over_time(),
            },
            data_store.claims_narratives(pedal_count=1, limit=5),
        )
        payload = build_review_interrupt_payload(
            summary=recommendation["summary"],
            evidence=cast(list[str], recommendation["evidence"]),
            rating_action=cast(str, recommendation["rating_action"]),
            proposed_relativity=cast(float, recommendation["proposed_relativity"]),
            filing_required=cast(bool, recommendation["filing_required"]),
        )
        resume_value = interrupt(payload)
        decision, notes = normalize_review_decision(resume_value)
        return {"review_decision": {"decision": decision, "notes": notes}}

    return node


def start_writeback_tool(data_store: DemoDataStore):
    def node(state: DataAnalystState) -> dict[str, Any]:
        recommendation = state.get("recommendation") or build_recommendation(
            {
                "loss_frequency": data_store.compare_loss_frequency(),
                "cohort_mix": data_store.cohort_mix_over_time(),
            },
            data_store.claims_narratives(pedal_count=1, limit=5),
        )
        notes = state.get("review_decision", {}).get("notes", "")
        return {
            "messages": [
                request_tools(
                    tool_call(
                        "write_audit_log",
                        {
                            "decision": "approved",
                            "summary": recommendation["summary"],
                            "reviewer_notes": notes,
                        },
                    )
                )
            ],
            "pending_tool_flow": "writeback",
        }

    return node


def present_writeback(state: DataAnalystState) -> dict[str, Any]:
    document = cast(dict[str, Any], latest_tool_json(state, "write_audit_log"))
    return {
        "messages": [
            *task_status_messages(
                "interpretation_flow",
                "Persist the approved recommendation to audit_log.",
                "Decision written",
            ),
            AIMessage(
                content=(
                    "Decision written to audit_log with status approved "
                    f"at {document['created_at']}."
                )
            ),
        ]
    }


def review_rejected(state: DataAnalystState) -> dict[str, Any]:
    review = state.get("review_decision", {})
    decision = review.get("decision") or "rejected"
    notes = review.get("notes", "")
    return {
        "messages": [
            AIMessage(
                content=(
                    "Recommendation was not written to audit_log. "
                    f"Reviewer decision: {decision or 'rejected'}. {notes}".strip()
                )
            )
        ]
    }


def build_recommendation(
    analytics_result: dict[str, Any],
    narratives: list[dict[str, Any]],
) -> dict[str, Any]:
    loss_rows = analytics_result.get("loss_frequency", [])
    rows_by_pedal = {
        row.get("pedal_count"): row
        for row in loss_rows
        if isinstance(row, dict) and isinstance(row.get("pedal_count"), int)
    }
    one_pedal = rows_by_pedal.get(1)
    two_pedal = rows_by_pedal.get(2)
    if one_pedal is None or two_pedal is None:
        missing = []
        if one_pedal is None:
            missing.append("1 pedal")
        if two_pedal is None:
            missing.append("2 pedals")
        return {
            "summary": (
                "Unable to prepare a rating recommendation because analytics results "
                "are missing required pedal cohorts."
            ),
            "rating_action": "No rating action until complete cohort data is available.",
            "proposed_relativity": 1.0,
            "filing_required": False,
            "evidence": [f"Missing analytics rows for: {', '.join(missing)}."],
        }

    evidence = [
        (
            "One-pedal vehicles show "
            f"{one_pedal['loss_frequency_per_1000']} claims per 1000 policies versus "
            f"{two_pedal['loss_frequency_per_1000']} for two-pedal vehicles."
        ),
        "Cohort mix has shifted toward three-pedal vehicles in later quarters.",
    ]
    if narratives:
        evidence.append(
            "Claims narratives cite regenerative braking limiting one-pedal impact speed."
        )
    return {
        "summary": (
            "Propose introducing pedal count as a rating factor, assigning a "
            "0.83 relativity to one-pedal vehicles based on lower observed loss frequency "
            "and supporting claims narratives."
        ),
        "rating_action": DEFAULT_RATING_ACTION,
        "proposed_relativity": DEFAULT_RELATIVITY,
        "filing_required": True,
        "evidence": evidence,
    }
