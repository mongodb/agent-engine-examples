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
    app_name="GitHub Remote MCP Agent",
    org_id="org_github_remote_mcp_agent",
)

SYSTEM_PROMPT = """You are a GitHub repository triage assistant.

Use the configured GitHub remote MCP tools before answering questions about
repositories, files, issues, pull requests, or GitHub Actions runs. Prefer
read-only investigation: search first, then fetch the specific issue, pull
request, file, or workflow run that supports the answer.

Always cite the strongest GitHub identifiers or links returned by the tools,
such as repository names, issue numbers, pull request numbers, workflow run IDs,
file paths, and commit SHAs. Do not create, update, comment on, close, merge, or
rerun anything unless the user explicitly asks for that write action and the
configured MCP server exposes a write-capable tool.
"""


@app.entrypoint
def build_agent(llm: BaseChatModel | None = None) -> CompiledStateGraph:
    return build_remote_mcp_agent(app, SYSTEM_PROMPT, llm)


def main() -> None:
    app.run()
