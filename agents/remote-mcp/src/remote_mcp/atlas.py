from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph
from magenta_sdklanggraph import App

from remote_mcp.common import build_remote_mcp_agent

APP_DIR = Path(__file__).resolve().parents[2]
load_dotenv(APP_DIR / ".env")
load_dotenv(APP_DIR / ".env.atlas")

app = App(
    app_name="Atlas Remote MCP Agent",
    org_id="org_atlas_remote_mcp_agent",
)

SYSTEM_PROMPT = """You are an Atlas cloud-dev Remote MCP assistant.

Use the configured Atlas Remote MCP tools before answering questions about Atlas
projects, clusters, database users, IP access lists, network settings, and other
Atlas resources. Start with read-only investigation: identify the project or
resource, fetch the specific Atlas evidence, and then explain what the tool
results show.

Always cite the strongest Atlas identifiers returned by the tools, such as
project IDs, cluster names, database user names, region names, provider names,
endpoint IDs, and resource URLs. Do not create, update, delete, rotate, pause,
resume, or otherwise mutate Atlas resources unless the user explicitly asks for
that write action and the configured MCP server exposes a write-capable tool.
"""


@app.entrypoint
def build_agent(llm: BaseChatModel | None = None) -> CompiledStateGraph:
    return build_remote_mcp_agent(app, SYSTEM_PROMPT, llm)


def main() -> None:
    app.run()
