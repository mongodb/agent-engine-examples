from __future__ import annotations

import os
from typing import Annotated, Any, Literal, TypedDict, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from magenta_sdklanggraph import App


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def build_remote_mcp_agent(
    app: App,
    system_prompt: str,
    llm: BaseChatModel | None = None,
) -> CompiledStateGraph:
    """Build a small agent whose tool surface comes from remote MCP config."""

    if llm is None:
        llm = create_chat_model(app)

    wrapped_llm = app.llm(llm)
    llm_with_tools = wrapped_llm.bind_tools(app.get_tool_schemas())
    tools = app.get_tools()

    def agent_node(state: AgentState) -> AgentState:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt)] + list(messages)

        response = llm_with_tools.invoke(messages)
        response = app.validate_llm_response(response)
        return {"messages": [response]}

    def should_continue(state: AgentState) -> Literal["tools", "end"]:
        last_message = state["messages"][-1]
        tool_calls = getattr(last_message, "tool_calls", None)
        return "tools" if tool_calls else "end"

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=app.checkpointer())


def create_chat_model(app: App) -> BaseChatModel:
    llm_config = cast(Any, app).llm_config
    provider = (getattr(llm_config, "provider", "") or "").strip().lower()
    configured_model = (getattr(llm_config, "model", "") or "").strip()
    configured_base_url = (getattr(llm_config, "base_url", "") or "").strip()

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        openai_key = os.environ.get("OPENAI_API_KEY", "")
        openai_base_url = configured_base_url or os.environ.get("OPENAI_BASE_URL", "").strip()
        kwargs: dict[str, Any] = {
            "model": configured_model or os.environ.get("OPENAI_MODEL", "gpt-5.4-mini"),
            "temperature": 0,
        }
        if openai_key:
            kwargs["api_key"] = openai_key
        if openai_base_url:
            if "grove-foundry" in openai_base_url:
                kwargs["base_url"] = openai_base_url.split("/v1")[0] + "/v1"
                if openai_key:
                    kwargs["default_headers"] = {"api-key": openai_key}
            else:
                kwargs["base_url"] = openai_base_url.rstrip("/")
        return ChatOpenAI(**kwargs)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        anthropic_base_url = configured_base_url or os.environ.get("ANTHROPIC_BASE_URL", "").strip()
        kwargs: dict[str, Any] = {
            "model_name": configured_model
            or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            "temperature": 0,
        }
        if anthropic_key:
            kwargs["api_key"] = anthropic_key
        if anthropic_base_url:
            if "grove-foundry" in anthropic_base_url:
                kwargs["base_url"] = anthropic_base_url.split("/v1")[0].rstrip("/")
                if anthropic_key:
                    kwargs["default_headers"] = {"api-key": anthropic_key}
            else:
                kwargs["base_url"] = anthropic_base_url.rstrip("/")
        return ChatAnthropic(**kwargs)

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        gemini_key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
        kwargs: dict[str, Any] = {
            "model": configured_model or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
            "temperature": 0,
        }
        if gemini_key:
            kwargs["api_key"] = gemini_key
        return ChatGoogleGenerativeAI(**kwargs)

    raise RuntimeError("Set agent.yaml config.provider to one of: openai, anthropic, gemini.")
