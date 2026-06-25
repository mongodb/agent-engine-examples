"""Flow selection and conditional-edge routing for the data analyst graph."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal, cast

from data_analyst_agent.state import DataAnalystState

logger = logging.getLogger(__name__)

CANONICAL_PATTERN = re.compile(
    r"loss frequency.*one.*two.*three.*pedal|pedal.*cohort.*mix",
    re.IGNORECASE | re.DOTALL,
)
INVESTIGATION_PATTERN = re.compile(r"investigat|narrative|claim", re.IGNORECASE)
RECOMMENDATION_PATTERN = re.compile(
    r"\brecommend(?:ation)?\b|rating action|relativity|\bapprove\b",
    re.IGNORECASE,
)
DEFAULT_MEMORY_USER_ID = "local-dev-user"


def select_flow_sequence(
    query: str,
    *,
    procedural_memory_matches: bool,
) -> list[str]:
    normalized = query.lower()
    if (
        procedural_memory_matches
        or "learned workflow" in normalized
        or "full workflow" in normalized
    ):
        return ["analytics_flow", "investigation_flow", "interpretation_flow"]
    has_investigation_intent = bool(INVESTIGATION_PATTERN.search(query))
    has_recommendation_intent = bool(RECOMMENDATION_PATTERN.search(query))
    if has_investigation_intent and has_recommendation_intent:
        return ["analytics_flow", "investigation_flow", "interpretation_flow"]
    if has_recommendation_intent:
        return ["interpretation_flow"]
    if has_investigation_intent:
        return ["investigation_flow"]
    if CANONICAL_PATTERN.search(query):
        return ["analytics_flow"]
    return ["small_talk"]


def matches_procedural_memory(app: Any, query: str) -> bool:
    try:
        matches = app.memory.discover_procedures(
            query=query,
            user_id=memory_user_id(app),
            top_k=3,
            similarity_threshold=0.74,
        )
    except Exception as exc:  # noqa: BLE001 - memory is optional for local smoke tests.
        logger.warning("Procedural memory lookup failed: %s", exc)
        return False

    for match in matches or []:
        if isinstance(match, dict) and match.get("procedure") == "pedal-cohort-pricing-review":
            return True
        procedure = getattr(match, "procedure", None)
        if procedure == "pedal-cohort-pricing-review":
            return True
    return False


def memory_user_id(app: Any) -> str:
    return app.get_current_user_id() or DEFAULT_MEMORY_USER_ID


def first_flow(
    state: DataAnalystState,
) -> Literal[
    "analytics_flow",
    "investigation_flow",
    "interpretation_flow",
    "small_talk",
]:
    return cast(Any, state.get("flow_sequence", ["small_talk"])[0])


def after_analytics(
    state: DataAnalystState,
) -> Literal["investigation_flow", "interpretation_flow", "end"]:
    sequence = state.get("flow_sequence", [])
    if "investigation_flow" in sequence:
        return "investigation_flow"
    if "interpretation_flow" in sequence:
        return "interpretation_flow"
    return "end"


def after_investigation(state: DataAnalystState) -> Literal["interpretation_flow", "end"]:
    if "interpretation_flow" in state.get("flow_sequence", []):
        return "interpretation_flow"
    return "end"


def after_tools(
    state: DataAnalystState,
) -> Literal[
    "start_analytics_chart_tools",
    "present_analytics_flow",
    "present_investigation_flow",
    "present_writeback",
]:
    pending = state.get("pending_tool_flow")
    if pending == "analytics_data":
        return "start_analytics_chart_tools"
    if pending == "analytics_charts":
        return "present_analytics_flow"
    if pending == "investigation":
        return "present_investigation_flow"
    if pending == "writeback":
        return "present_writeback"
    raise ValueError(f"Unknown pending tool flow: {pending!r}")


def after_review(state: DataAnalystState) -> Literal["start_writeback_tool", "review_rejected"]:
    if state.get("review_decision", {}).get("decision") == "approve":
        return "start_writeback_tool"
    return "review_rejected"
