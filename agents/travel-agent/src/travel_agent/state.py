from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class _OptionalAgentState(TypedDict, total=False):
    user_id: Optional[str]
    session_id: Optional[str]
    memory_context: str
    citation_index: str
    current_procedure: Optional[dict[str, Any]]
    procedure_step_index: int
    step_results: list[dict[str, str]]
    travel_context: dict[str, Any]
    current_flow: str
    resolution_rounds: int


class AgentState(_OptionalAgentState):
    messages: Annotated[list[BaseMessage], add_messages]
