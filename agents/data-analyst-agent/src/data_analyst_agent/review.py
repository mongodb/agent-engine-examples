"""Human review payload builders for native LangGraph interrupts."""

from __future__ import annotations

from typing import Any

from data_analyst_agent.data_store import AUDIT_COLLECTION

DEFAULT_RATING_ACTION = "Introduce pedal count as a rating factor for one-pedal vehicles."
DEFAULT_RELATIVITY = 0.83
REVIEW_REASONS = [
    "Rating recommendations require manager approval before audit log writeback.",
    "Introducing a new rating factor requires regulatory filing review.",
]


def build_review_context(
    *,
    summary: str,
    evidence: list[str],
    rating_action: str = DEFAULT_RATING_ACTION,
    proposed_relativity: float = DEFAULT_RELATIVITY,
    filing_required: bool = True,
) -> dict[str, Any]:
    instructions = (
        f"Approve to write the recommendation to {AUDIT_COLLECTION} or reject with notes."
    )
    return {
        "decision_type": "rating_recommendation",
        "summary": summary,
        "rating_action": rating_action,
        "proposed_relativity": proposed_relativity,
        "filing_required": filing_required,
        "guardrail_triggered": False,
        "guardrail_reasons": [],
        "review_reasons": REVIEW_REASONS,
        "evidence": evidence,
        "writeback_target": AUDIT_COLLECTION,
        "allowed_decisions": ["approve", "reject"],
        "instructions": instructions,
        "review_presentation": {
            "schema_version": "review-presentation/v1",
            "title": "Rating recommendation review",
            "summary": "Review the proposed pedal-count rating recommendation before writeback.",
            "sections": [
                {
                    "title": "Recommendation",
                    "fields": [
                        {
                            "key": "rating_action",
                            "label": "Proposed action",
                            "value": rating_action,
                            "format": "text",
                        },
                        {
                            "key": "proposed_relativity",
                            "label": "Proposed relativity",
                            "value": proposed_relativity,
                            "format": "number",
                        },
                        {
                            "key": "filing_required",
                            "label": "Filing required",
                            "value": filing_required,
                            "format": "boolean",
                        },
                        {
                            "key": "writeback_target",
                            "label": "Writeback target",
                            "value": AUDIT_COLLECTION,
                            "format": "text",
                        },
                    ],
                },
                {
                    "title": "Evidence",
                    "fields": [
                        {
                            "key": "summary",
                            "label": "Summary",
                            "value": summary,
                            "format": "long_text",
                        },
                        {
                            "key": "evidence",
                            "label": "Supporting evidence",
                            "value": evidence,
                            "format": "list",
                        },
                    ],
                },
                {
                    "title": "Review guidance",
                    "fields": [
                        {
                            "key": "review_reasons",
                            "label": "Review reasons",
                            "value": REVIEW_REASONS,
                            "format": "list",
                        },
                        {
                            "key": "instructions",
                            "label": "Instructions",
                            "value": instructions,
                            "format": "long_text",
                        },
                    ],
                },
            ],
            "actions": [
                {"id": "approve", "label": "Approve"},
                {"id": "reject", "label": "Reject"},
            ],
        },
    }


def build_review_interrupt_payload(
    *,
    summary: str,
    evidence: list[str],
    rating_action: str = DEFAULT_RATING_ACTION,
    proposed_relativity: float = DEFAULT_RELATIVITY,
    filing_required: bool = True,
) -> dict[str, Any]:
    """Return the value passed to ``langgraph.types.interrupt``."""
    return {
        "suspend_reason": "awaiting_human_review",
        "suspend_context": build_review_context(
            summary=summary,
            evidence=evidence,
            rating_action=rating_action,
            proposed_relativity=proposed_relativity,
            filing_required=filing_required,
        ),
    }


def normalize_review_decision(resume_value: Any) -> tuple[str, str]:
    """Extract approve/reject plus notes from common resume payload shapes."""
    if isinstance(resume_value, str):
        decision = _normalize_decision_token(resume_value)
        return decision, ""

    if isinstance(resume_value, dict):
        nested_review = resume_value.get("human_review") or resume_value.get("review")
        if isinstance(nested_review, dict):
            return normalize_review_decision(nested_review)

        raw_decision = (
            resume_value.get("decision")
            or resume_value.get("action")
            or resume_value.get("status")
            or ""
        )
        notes = str(
            resume_value.get("notes")
            or resume_value.get("reviewer_notes")
            or resume_value.get("feedback")
            or ""
        )
        return _normalize_decision_token(raw_decision), notes

    return "", ""


def _normalize_decision_token(value: Any) -> str:
    decision = str(value).strip().lower()
    if decision == "approved":
        return "approve"
    if decision == "rejected":
        return "reject"
    return decision
