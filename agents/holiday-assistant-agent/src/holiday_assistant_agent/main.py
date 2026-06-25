"""Holiday Assistant Agent — entrypoint and graph."""

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

from holiday_assistant_agent.llm import build_llm, patch_runtime_llm
from holiday_assistant_agent.state import HolidayAssistantState
from holiday_assistant_agent.system_message import SYSTEM_PROMPT
from holiday_assistant_agent.tools import register

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()
patch_runtime_llm()

MONGODB_URI = os.environ.get("MONGODB_URI", "")
MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "agent_memory")
ENABLE_TRACING = os.environ.get("ENABLE_TRACING", "false").lower() == "true"
ENABLE_MEMORY = os.environ.get("ENABLE_MEMORY", "false").lower() == "true"

app = App(
    app_name="holiday-assistant-agent",
    mongodb_uri=MONGODB_URI,
    database_name=MONGODB_DATABASE,
    enable_tracing=ENABLE_TRACING,
    enable_memory=ENABLE_MEMORY,
)

register(app)

# Holiday queries can chain a few searches before producing a recommendation
# (e.g. find hotel → check rooms → check policy → book), so the per-turn
# budget is generous.
MAX_TOOL_CALLS_PER_TURN = 20


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    """Build the LangGraph agent for the holiday assistant."""
    logger.info("Building holiday-assistant-agent graph")
    runtime_llm = app.llm(build_llm(temperature=0))
    tools = app.get_tools()
    llm_with_tools = runtime_llm.bind_tools(app.get_tool_schemas())

    def agent_node(state: HolidayAssistantState) -> dict:
        messages = state["messages"]
        today = state.get("today") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M %Z")

        memory_block = ""
        if ENABLE_MEMORY:
            try:
                user_id = app.get_current_user_id()
                last_human = next(
                    (m.content for m in reversed(messages) if getattr(m, "type", "") == "human"),
                    "",
                )
                query = last_human if isinstance(last_human, str) else ""

                # Auto-inject lightweight semantic facts (name, home airport,
                # preferences). Heavier episodic history stays behind the
                # `recall_traveler_context` tool — the model fetches it when
                # it judges a returning user needs deeper context.
                context = app.memory.build_context(
                    query=query or "traveler profile and preferences",
                    user_id=user_id,
                )
                if context:
                    memory_block = (
                        "\n\n## Saved facts about this traveler\n"
                        f"{context}\n\n"
                        "Use these facts to personalize responses. If a saved "
                        "fact contradicts what the user just said, trust the "
                        "latest message and call `remember_traveler_fact` to "
                        "update. Call `recall_traveler_context` when you need "
                        "deeper history (prior trips, past conversations)."
                    )
            except Exception as exc:
                logger.warning("Memory lookup failed: %s", exc)

        # Use replace() rather than format() — the prompt contains JSON
        # examples with literal `{...}` braces (e.g. the human_review tool
        # message shape) which str.format() would otherwise treat as format
        # fields and raise KeyError on.
        system_prompt = SYSTEM_PROMPT.replace("{today}", today) + memory_block

        if not messages or not isinstance(messages[0], SystemMessage):
            prompt_messages = [SystemMessage(content=system_prompt)] + list(messages)
        else:
            prompt_messages = [SystemMessage(content=system_prompt)] + list(messages[1:])

        response = llm_with_tools.invoke(prompt_messages)
        response = app.validate_llm_response(response)
        return {"messages": [response]}

    def should_continue(state: HolidayAssistantState) -> Literal["tools", "end"]:
        messages = state["messages"]
        last = messages[-1]

        if hasattr(last, "tool_calls") and last.tool_calls:  # type: ignore[union-attr]
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

    builder = StateGraph(HolidayAssistantState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=app.checkpointer())


def main() -> None:
    """Run the holiday assistant agent."""
    app.run()


if __name__ == "__main__":
    main()
