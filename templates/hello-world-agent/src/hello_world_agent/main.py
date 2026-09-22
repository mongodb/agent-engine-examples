"""Hello-world Magenta template."""

from __future__ import annotations

import logging
from typing import Literal

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from agent_engine_sdk_langgraph import App

from hello_world_agent.llm import build_llm
from hello_world_agent.state import HelloWorldState
from hello_world_agent.system_message import SYSTEM_PROMPT
from hello_world_agent.tools import register

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()

app = App(app_name="hello-world-agent")

register(app)


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    """Build the LangGraph agent."""
    logger.info("Building hello-world-agent graph")
    runtime_llm = app.llm(build_llm(temperature=0))
    tools = app.get_tools()
    llm_with_tools = runtime_llm.bind_tools(app.get_tool_schemas())

    def agent_node(state: HelloWorldState) -> dict:
        messages = state["messages"]

        if not messages or not isinstance(messages[0], SystemMessage):
            prompt_messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)
        else:
            prompt_messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages[1:])

        response = llm_with_tools.invoke(prompt_messages)
        return {"messages": [response]}

    def should_continue(state: HelloWorldState) -> Literal["tools", "end"]:
        last = state["messages"][-1]
        return "tools" if hasattr(last, "tool_calls") and last.tool_calls else "end"  # type: ignore[union-attr]

    builder = StateGraph(HelloWorldState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=app.checkpointer())


def main() -> None:
    """Run the hello-world agent."""
    app.run()


if __name__ == "__main__":
    main()
