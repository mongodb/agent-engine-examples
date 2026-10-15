"""Travel agent Atlas Agent Engine template."""

from __future__ import annotations

import logging
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolCall, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from agent_engine_sdk_langgraph import App

from travel_agent.llm import build_llm
from travel_agent.system_message import MEMORY_PROMPT, SYSTEM_PROMPT
from travel_agent.tools import register

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()

app = App(app_name="travel-agent")

register(app)


class TravelAgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    approved: bool  # the traveler's decision on a pending booking
    memories: str


def pending_tool_calls(state: TravelAgentState) -> list[ToolCall]:
    """Return the tool calls in the latest message, if the LLM made any."""
    last = state["messages"][-1]
    return last.tool_calls if isinstance(last, AIMessage) else []


def recall(messages: list) -> str:
    """Return long-term memories relevant to the latest user message."""
    query = next((m.text for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    memories = ""
    if query:
        try:
            memories = app.memory.build_context(query=query, max_tokens=2000).formatted_context
        except Exception:
            logger.warning("Memory recall failed; continuing without memories", exc_info=True)
    if isinstance(memories, list):
        memories = "\n".join(str(m.get("content", m)) for m in memories)
    logger.info("Recalled long-term memories" if memories else "No long-term memories found")
    return memories or "None yet."


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    """Build the LangGraph agent."""
    logger.info("Building travel-agent graph")
    llm = app.llm(build_llm())
    tools = app.get_tools()
    tools_map = {t.name: t for t in tools}
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT + MEMORY_PROMPT),
            MessagesPlaceholder(variable_name="messages"),
        ]
    ).partial(top_k=5, tool_names=", ".join(tools_map))
    llm_with_tools = prompt | llm.bind_tools(tools)

    def agent_node(state: TravelAgentState) -> dict:
        messages = state["messages"]
        # Recall once per user turn; the tool-calling steps that follow reuse it
        memories = state.get("memories")
        if memories is None or isinstance(messages[-1], HumanMessage):
            memories = recall(messages)
        response = llm_with_tools.invoke({"messages": messages, "memories": memories})
        return {"messages": [response], "memories": memories}

    def tool_node(state: TravelAgentState, config: RunnableConfig) -> dict:
        results = []
        for tool_call in pending_tool_calls(state):
            if tool_call["name"] == "create_booking" and not state.get("approved"):
                results.append(
                    ToolMessage(
                        content=(
                            "The traveler did NOT approve this booking. Nothing was saved. "
                            "Ask them what they would like to change."
                        ),
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"],
                    )
                )
                continue
            results.append(tools_map[tool_call["name"]].invoke(tool_call, config))
        return {"messages": results}

    def confirm_node(state: TravelAgentState) -> dict:
        pending = [call for call in pending_tool_calls(state) if call["name"] == "create_booking"]
        answer = interrupt(
            {
                "question": "Create this booking? Reply with y or n.",
                "bookings": [call["args"] for call in pending],
            }
        )
        return {"approved": str(answer).strip().lower() == "y"}

    def route_tools(state: TravelAgentState) -> str:
        tool_calls = pending_tool_calls(state)
        if not tool_calls:
            return END
        if any(call["name"] == "create_booking" for call in tool_calls):
            return "confirm"
        return "tools"

    builder = StateGraph(TravelAgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)
    builder.add_node("confirm", confirm_node)
    builder.add_edge(START, "agent")
    builder.add_edge("tools", "agent")
    builder.add_edge("confirm", "tools")
    builder.add_conditional_edges(
        "agent", route_tools, {"tools": "tools", "confirm": "confirm", END: END}
    )
    return builder.compile(checkpointer=app.checkpointer())


def main() -> None:
    """Run the travel agent."""
    app.run()


if __name__ == "__main__":
    main()
