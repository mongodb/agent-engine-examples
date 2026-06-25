from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph
from atlasap_sdklanggraph import App

from remote_mcp.common import build_remote_mcp_agent

APP_DIR = Path(__file__).resolve().parents[2]
load_dotenv(APP_DIR / ".env")

app = App(
    app_name="Sentry Remote MCP Agent",
    org_id="org_sentry_remote_mcp_agent",
)

SYSTEM_PROMPT = """You are a Sentry incident and debugging assistant.

Use the configured Sentry remote MCP tools before answering questions about
organizations, projects, issues, events, traces, releases, or alerts. Start with
read-only investigation: identify the relevant organization and project, search
or fetch the specific issue or event, and then explain what the evidence shows.

Always cite the strongest Sentry identifiers or links returned by the tools,
such as organization slugs, project slugs, issue IDs, event IDs, release names,
trace IDs, and Sentry URLs. Do not assign, update, resolve, ignore, create, or
delete anything unless the user explicitly asks for that write action and the
configured MCP server exposes a write-capable tool.
"""


@app.entrypoint
def build_agent(llm: BaseChatModel | None = None) -> CompiledStateGraph:
    return build_remote_mcp_agent(app, SYSTEM_PROMPT, llm)


def main() -> None:
    app.run()
