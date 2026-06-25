"""Shared graph state for the data analyst demo."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from data_analyst_agent.artifacts import Artifact


class DataAnalystState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    flow_sequence: list[str]
    analytics_result: dict[str, Any]
    narratives: list[dict[str, Any]]
    recommendation: dict[str, Any]
    review_decision: dict[str, str]
    pending_tool_flow: str
    artifacts: list[Artifact]
