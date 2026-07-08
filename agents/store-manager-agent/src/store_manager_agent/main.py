"""Store Manager Copilot Agent — entrypoint and graph."""

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

from store_manager_agent.llm import build_llm, patch_runtime_llm
from store_manager_agent.state import StoreManagerState
from store_manager_agent.system_message import SYSTEM_PROMPT
from store_manager_agent.tools import _routine_name, register

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()
patch_runtime_llm()

MONGODB_URI = os.environ.get("MONGODB_URI", "")
MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "agent_memory")
ENABLE_TRACING = os.environ.get("ENABLE_TRACING", "false").lower() == "true"
ENABLE_MEMORY = os.environ.get("ENABLE_MEMORY", "false").lower() == "true"

app = App(
    app_name="store-manager-agent",
    mongodb_uri=MONGODB_URI,
    database_name=MONGODB_DATABASE,
    enable_tracing=ENABLE_TRACING,
    enable_memory=ENABLE_MEMORY,
)

register(app)

# A morning rundown can chain several lookups before a recommendation
# (e.g. report → low stock → forecast → SOP → draft PO), so the per-turn
# tool budget is generous.
MAX_TOOL_CALLS_PER_TURN = 25


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    """Build the LangGraph agent for the store manager copilot."""
    logger.info("Building store-manager-agent graph")
    runtime_llm = app.llm(build_llm(temperature=0))
    tools = app.get_tools()
    llm_with_tools = runtime_llm.bind_tools(app.get_tool_schemas())

    def agent_node(state: StoreManagerState) -> dict:
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

                # Auto-inject lightweight semantic facts about THIS store
                # (format, demand pattern, flaky equipment). Heavier episodic
                # history (past reorder / markdown decisions) stays behind the
                # `recall_store_context` tool — the model fetches it when it
                # judges the manager needs deeper context.
                context = app.memory.build_context(
                    query=query or "store profile, demand pattern, equipment notes",
                    user_id=user_id,
                    visibility="private",
                )
                if context:
                    memory_block = (
                        "\n\n## Saved facts about this store\n"
                        f"{context}\n\n"
                        "Use these facts to personalize your operational advice. "
                        "If a saved fact contradicts what the manager just said, "
                        "trust the latest message and call `remember_store_fact` "
                        "to update. Call `recall_store_context` when you need "
                        "deeper history (past reorders, markdowns, prior shifts)."
                    )

                # Auto-inject this manager's learned morning-rundown routine
                # (procedural memory) so the rundown always follows it without
                # the model having to remember to look it up. Saved per-manager
                # via `save_report_routine` after the manager opts in.
                try:
                    routine = app.memory.get_procedure(
                        procedure_name=_routine_name(user_id), visibility="org"
                    )
                    routine_content = (
                        routine.get("content") if isinstance(routine, dict) else None
                    ) or ""
                    if routine_content:
                        memory_block += (
                            "\n\n## This manager's saved morning-rundown routine\n"
                            f"{routine_content}\n\n"
                            "When the manager asks for the morning rundown / "
                            "overnight report, FOLLOW THIS ROUTINE automatically — "
                            "run the extra checks it lists (e.g. expiring items, "
                            "planogram compliance) in the SAME turn, without being "
                            "asked, and fold the results in. Mention you included "
                            "them because they're part of their saved routine."
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Routine lookup failed: %s", exc)
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

    def should_continue(state: StoreManagerState) -> Literal["tools", "end"]:
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

    builder = StateGraph(StoreManagerState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=app.checkpointer())


def main() -> None:
    """Run the store manager copilot agent."""
    app.run()


if __name__ == "__main__":
    main()
