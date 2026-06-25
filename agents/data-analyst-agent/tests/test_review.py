from __future__ import annotations

from data_analyst_agent.review import build_review_interrupt_payload, normalize_review_decision


def test_review_suspend_payload_uses_native_langgraph_shape_without_guardrails() -> None:
    payload = build_review_interrupt_payload(
        summary=(
            "Propose introducing pedal count as a rating factor with a 0.83 relativity "
            "for one-pedal vehicles."
        ),
        evidence=[
            "One-pedal vehicles show lower loss frequency after controls.",
            "Narratives show fewer severe collision claims in the one-pedal cohort.",
        ],
    )

    context = payload["suspend_context"]
    presentation = context["review_presentation"]

    assert payload["suspend_reason"] == "awaiting_human_review"
    assert context["decision_type"] == "rating_recommendation"
    assert context["writeback_target"] == "audit_log"
    assert context["allowed_decisions"] == ["approve", "reject"]
    assert context["guardrail_triggered"] is False
    assert context["guardrail_reasons"] == []
    assert context["review_reasons"]
    assert presentation["schema_version"] == "review-presentation/v1"
    assert {action["id"] for action in presentation["actions"]} == {"approve", "reject"}


def test_normalize_review_decision_accepts_oe_human_review_wrapper() -> None:
    decision, notes = normalize_review_decision(
        {
            "human_review": {
                "decision": "approve",
                "notes": "approved in the review modal",
            }
        }
    )

    assert decision == "approve"
    assert notes == "approved in the review modal"


def test_normalize_review_decision_accepts_review_modal_notes_field() -> None:
    decision, notes = normalize_review_decision(
        {
            "decision": "approved",
            "reviewer_notes": "approved after verifying the local demo evidence",
        }
    )

    assert decision == "approve"
    assert notes == "approved after verifying the local demo evidence"
