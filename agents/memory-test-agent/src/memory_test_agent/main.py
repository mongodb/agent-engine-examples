"""Memory surface-area test agent — uses SDK Memory API, no LLM."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from magenta_sdklanggraph import App

from memory_test_agent.state import MemoryTestAgentState
from memory_test_agent.tools import register

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()

app = App(
    app_name="memory-test-agent",
    mongodb_uri=os.environ.get("MONGODB_URI", ""),
    database_name=os.environ.get("MONGODB_DATABASE", "memory_test_agent"),
    enable_tracing=False,
)

tools_map = register(app)


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    logger.info("Building memory-test-agent graph (deterministic, no LLM)")

    def run_scenarios(state: MemoryTestAgentState) -> dict:
        """Run SDK surface-area + OE policy scenarios unconditionally."""
        report = tools_map["run_all_scenarios"]()
        return {"messages": [AIMessage(content=report)]}

    builder = StateGraph(MemoryTestAgentState)
    builder.add_node("run", run_scenarios)
    builder.add_edge(START, "run")
    builder.add_edge("run", END)
    return builder.compile(checkpointer=app.checkpointer())


def main() -> None:
    app.run()


if __name__ == "__main__":
    main()
