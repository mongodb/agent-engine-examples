"""Simple Agent — Web Search Assistant entrypoint."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Literal

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from magenta_sdklanggraph import App

from simple_agent.llm import build_llm, patch_runtime_llm
from simple_agent.state import SimpleAgentState
from simple_agent.system_message import SYSTEM_PROMPT
from simple_agent.tools import register

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()
patch_runtime_llm()

MONGODB_URI = os.environ.get("MONGODB_URI", "")
MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "simple_agent")
ENABLE_TRACING = os.environ.get("ENABLE_TRACING", "false").lower() == "true"
ENABLE_MEMORY = os.environ.get("ENABLE_MEMORY", "false").lower() == "true"

app = App(
    app_name="simple-agent",
    mongodb_uri=MONGODB_URI,
    database_name=MONGODB_DATABASE,
    enable_tracing=ENABLE_TRACING,
    enable_memory=ENABLE_MEMORY,
)

register(app)

MAX_TOOL_CALLS_PER_TURN = 10


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    """Build the LangGraph agent for the simple agent."""
    logger.info("Building simple-agent graph")
    runtime_llm = app.llm(build_llm(temperature=0))
    tools = app.get_tools()
    llm_with_tools = runtime_llm.bind_tools(app.get_tool_schemas())

    def agent_node(state: SimpleAgentState) -> dict:
        messages = state["messages"]
        email = state.get("email", "")
        today = state.get("today", "") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M %Z")

        system_prompt = SYSTEM_PROMPT.format(email=email, today=today)

        if not messages or not isinstance(messages[0], SystemMessage):
            prompt_messages = [SystemMessage(content=system_prompt)] + list(messages)
        else:
            prompt_messages = [SystemMessage(content=system_prompt)] + list(messages[1:])

        response = llm_with_tools.invoke(prompt_messages)
        response = app.validate_llm_response(response)
        return {"messages": [response]}

    def should_continue(state: SimpleAgentState) -> Literal["tools", "end"]:
        messages = state["messages"]
        last = messages[-1]

        if hasattr(last, "tool_calls") and last.tool_calls:  # type: ignore[union-attr]
            # Safety check: prevent infinite tool-call loops
            tool_call_count = sum(
                1
                for m in messages
                if hasattr(m, "tool_calls") and m.tool_calls  # type: ignore[union-attr]
            )
            if tool_call_count > MAX_TOOL_CALLS_PER_TURN:
                logger.warning("Too many tool calls (%d), ending conversation", tool_call_count)
                return "end"
            return "tools"
        return "end"

    builder = StateGraph(SimpleAgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=app.checkpointer())


def main() -> None:
    """Run the simple agent."""
    app.run()


if __name__ == "__main__":
    main()
