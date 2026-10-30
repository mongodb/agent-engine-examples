"""Hello-world Atlas Agent Engine OpenAI Agents template."""

from __future__ import annotations

import logging

from agents import Agent, Tool
from dotenv import load_dotenv
from agent_engine_sdk_openai_agents import App

from hello_world_agent.llm import build_llm
from hello_world_agent.system_message import SYSTEM_PROMPT
from hello_world_agent.tools import register

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()

app = App(app_name="hello-world-agent-openai", app_version="1.0.0")

register(app)


@app.entrypoint
def build_agent() -> Agent:
    """Build the OpenAI Agents SDK agent."""
    logger.info("Building hello-world-agent-openai agent")
    tools: list[Tool] = [*app.tools()]
    return Agent(
        name="daily",
        instructions=SYSTEM_PROMPT.strip(),
        model=app.llm(build_llm()),
        tools=tools,
    )


def main() -> None:
    """Run the hello-world agent."""
    app.run()


if __name__ == "__main__":
    main()
