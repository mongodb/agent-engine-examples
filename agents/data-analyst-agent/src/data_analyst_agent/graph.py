"""LangGraph topology for the data analyst demo."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from data_analyst_agent.data_store import DemoDataStore
from data_analyst_agent.flow_messages import last_user_text
from data_analyst_agent.flows import analytics, interpretation, investigation
from data_analyst_agent.routing import (
    after_analytics,
    after_investigation,
    after_review,
    after_tools,
    first_flow,
    matches_procedural_memory,
    select_flow_sequence,
)
from data_analyst_agent.state import DataAnalystState


def build_data_analyst_graph(
    *,
    app: Any,
    data_store: DemoDataStore,
    runtime_llm: BaseChatModel | None,
    checkpointer: Any,
) -> CompiledStateGraph:
    builder = StateGraph(DataAnalystState)

    builder.add_node("route_request", _route_request(app))
    builder.add_node("small_talk", _small_talk(app, runtime_llm))
    builder.add_node("start_analytics_tools", analytics.start_data_tools)
    builder.add_node("start_analytics_chart_tools", analytics.start_chart_tools)
    builder.add_node("run_tools", ToolNode(app.get_tools()))
    builder.add_node("present_analytics_flow", analytics.present(data_store))
    builder.add_node("start_investigation_tools", investigation.start_tools)
    builder.add_node("present_investigation_flow", investigation.present)
    builder.add_node("interpretation_flow", interpretation.prepare_review(data_store))
    builder.add_node("human_review", interpretation.human_review(data_store))
    builder.add_node("start_writeback_tool", interpretation.start_writeback_tool(data_store))
    builder.add_node("present_writeback", interpretation.present_writeback)
    builder.add_node("review_rejected", interpretation.review_rejected)

    builder.add_edge(START, "route_request")
    builder.add_conditional_edges(
        "route_request",
        first_flow,
        {
            "analytics_flow": "start_analytics_tools",
            "investigation_flow": "start_investigation_tools",
            "interpretation_flow": "interpretation_flow",
            "small_talk": "small_talk",
        },
    )

    builder.add_edge("start_analytics_tools", "run_tools")
    builder.add_edge("start_investigation_tools", "run_tools")
    builder.add_edge("start_writeback_tool", "run_tools")
    builder.add_conditional_edges(
        "run_tools",
        after_tools,
        {
            "start_analytics_chart_tools": "start_analytics_chart_tools",
            "present_analytics_flow": "present_analytics_flow",
            "present_investigation_flow": "present_investigation_flow",
            "present_writeback": "present_writeback",
        },
    )

    builder.add_edge("start_analytics_chart_tools", "run_tools")
    builder.add_conditional_edges(
        "present_analytics_flow",
        after_analytics,
        {
            "investigation_flow": "start_investigation_tools",
            "interpretation_flow": "interpretation_flow",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "present_investigation_flow",
        after_investigation,
        {
            "interpretation_flow": "interpretation_flow",
            "end": END,
        },
    )

    builder.add_edge("interpretation_flow", "human_review")
    builder.add_conditional_edges(
        "human_review",
        after_review,
        {
            "start_writeback_tool": "start_writeback_tool",
            "review_rejected": "review_rejected",
        },
    )

    builder.add_edge("present_writeback", END)
    builder.add_edge("review_rejected", END)
    builder.add_edge("small_talk", END)
    return builder.compile(checkpointer=checkpointer)


def _route_request(app: Any):
    def node(state: DataAnalystState) -> dict[str, Any]:
        query = last_user_text(state)
        return {
            "flow_sequence": select_flow_sequence(
                query,
                procedural_memory_matches=matches_procedural_memory(app, query),
            )
        }

    return node


def _small_talk(app: Any, runtime_llm: BaseChatModel | None):
    def node(state: DataAnalystState) -> dict[str, Any]:
        if runtime_llm is not None:
            response = runtime_llm.invoke(
                [
                    HumanMessage(
                        content=(
                            "Reply briefly as a data analyst demo agent. User said: "
                            f"{last_user_text(state)}"
                        )
                    )
                ]
            )
            return {"messages": [app.validate_llm_response(response)]}
        return {
            "messages": [
                AIMessage(
                    content=(
                        "I can compare pedal-count cohorts, investigate claims narratives, "
                        "and prepare a reviewed rating recommendation."
                    )
                )
            ]
        }

    return node
