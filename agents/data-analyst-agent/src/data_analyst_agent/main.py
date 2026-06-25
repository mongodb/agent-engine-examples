"""Data Analyst Agent - standalone Magenta example."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph
from magenta_sdklanggraph import App

from data_analyst_agent.data_store import DEFAULT_DATABASE, DemoDataStore
from data_analyst_agent.graph import build_data_analyst_graph
from data_analyst_agent.llm import build_llm
from data_analyst_agent.tools import register_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)
load_dotenv(".env.dev", override=False)
load_dotenv(override=False)

APP_NAME = "data-analyst-agent"
ORG_ID = os.environ.get("ORG_ID", "653700000000000000000101")
MONGODB_URI = os.environ.get("MONGODB_URI", "")
MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", DEFAULT_DATABASE)

app = App(
    app_name=APP_NAME,
    org_id=ORG_ID or None,
    mongodb_uri=MONGODB_URI,
    database_name=MONGODB_DATABASE,
)
logger.info("Data analyst app created")

data_store = DemoDataStore(mongodb_uri=MONGODB_URI, database_name=MONGODB_DATABASE)
register_tools(app, data_store)


def _build_runtime_llm(temperature: float = 0.0) -> BaseChatModel:
    return build_llm(llm_config=getattr(app, "llm_config", None), temperature=temperature)


def _optional_runtime_llm() -> BaseChatModel | None:
    llm_config = getattr(app, "llm_config", None)
    configured_provider = (getattr(llm_config, "provider", None) or "").strip().lower()
    provider_keys = {
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
    }
    if configured_provider and not os.environ.get(provider_keys.get(configured_provider, "")):
        return None
    if not configured_provider and not any(os.environ.get(name) for name in provider_keys.values()):
        return None
    try:
        return app.llm(_build_runtime_llm())
    except Exception as exc:  # noqa: BLE001 - deterministic demo responses remain available.
        logger.warning("LLM unavailable; deterministic demo responses remain enabled: %s", exc)
        return None


def _checkpointer():
    if MONGODB_URI:
        return app.checkpointer()

    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


@app.entrypoint
def build_agent(llm: BaseChatModel | None = None) -> CompiledStateGraph:
    """Build the data analyst graph with native LangGraph review suspend."""
    runtime_llm = app.llm(llm) if llm is not None else _optional_runtime_llm()
    return build_data_analyst_graph(
        app=app,
        data_store=data_store,
        runtime_llm=runtime_llm,
        checkpointer=_checkpointer(),
    )


def main() -> None:
    app.run()


if __name__ == "__main__":
    main()
