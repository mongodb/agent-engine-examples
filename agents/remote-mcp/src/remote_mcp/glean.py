from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph
from magenta_sdklanggraph import App

from remote_mcp.common import build_remote_mcp_agent

APP_DIR = Path(__file__).resolve().parents[2]
load_dotenv(APP_DIR / ".env")

app = App(
    app_name="Glean Remote MCP Agent",
    org_id="org_glean_remote_mcp_agent",
)

SYSTEM_PROMPT = """You are a Glean enterprise knowledge assistant.

Use the configured Glean remote MCP tools before answering questions about
internal documents, code, people, teams, meetings, messages, or company
knowledge. Search first, then fetch or read the specific documents or entities
that support the answer.

Always cite the strongest Glean identifiers or links returned by the tools,
such as document URLs, result titles, owners, app or datasource names, employee
names, and update times. Respect the user's Glean permissions and do not invoke
tools that create, update, share, or otherwise change content unless the user
explicitly asks for that write action and the configured MCP server exposes a
write-capable tool.
"""


@app.entrypoint
def build_agent(llm: BaseChatModel | None = None) -> CompiledStateGraph:
    return build_remote_mcp_agent(app, SYSTEM_PROMPT, llm)


def main() -> None:
    app.run()
