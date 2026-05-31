"""Tests for simple-agent graph."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from simple_agent.main import app
from simple_agent.state import SimpleAgentState


class FakeLLM(FakeMessagesListChatModel):
    """Fake LLM that supports bind_tools."""

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> "FakeLLM":  # type: ignore[override]
        return self


def _build_test_graph(fake_llm: FakeLLM):
    """Build the graph with a fake LLM for testing."""
    tools = app.get_tools()

    def agent_node(state: SimpleAgentState) -> dict:
        messages = state["messages"]
        response = fake_llm.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: SimpleAgentState):
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return "end"

    builder = StateGraph(SimpleAgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")
    return builder.compile()


def test_graph_compiles_and_executes() -> None:
    fake_llm = FakeLLM(responses=[AIMessage(content="Hello!")])
    result = _build_test_graph(fake_llm).invoke(
        {"messages": [HumanMessage(content="hi")]}
    )
    response = result["messages"][-1]
    assert isinstance(response, AIMessage)
    assert response.content == "Hello!"


def test_graph_routes_to_end_without_tool_calls() -> None:
    fake_llm = FakeLLM(responses=[AIMessage(content="I'm happy to help!")])
    result = _build_test_graph(fake_llm).invoke(
        {"messages": [HumanMessage(content="hello")]}
    )
    response = result["messages"][-1]
    assert isinstance(response, AIMessage)
    assert not response.tool_calls
