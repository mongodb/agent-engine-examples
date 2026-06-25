from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from magenta_sdklanggraph import App

app = App(app_name="Weather Agent")


@app.tool(is_local=False)
def get_weather(city: str) -> str:
    """Get current weather conditions for a city."""
    return f"{city}: 12°F"


def _build_llm():
    if os.environ.get("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI

        openai_key = os.environ["OPENAI_API_KEY"]
        openai_base_url = os.environ.get("OPENAI_BASE_URL", "")
        kwargs: dict = {
            "api_key": openai_key,
            "model": os.environ.get("OPENAI_MODEL", "gpt-5.4-mini"),
        }
        if openai_base_url:
            if "grove-foundry" in openai_base_url:
                kwargs["base_url"] = openai_base_url.split("/v1")[0] + "/v1"
                kwargs["default_headers"] = {"api-key": openai_key}
            else:
                kwargs["base_url"] = openai_base_url.rstrip("/")
        return ChatOpenAI(**kwargs)
    elif os.environ.get("GEMINI_API_KEY"):
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"))
    elif os.environ.get("CEREBRAS_API_KEY"):
        from langchain_cerebras import ChatCerebras

        return ChatCerebras(model=os.environ.get("CEREBRAS_MODEL", "llama3.1-8b"))
    else:
        raise Exception("no LLM provider available.")


@app.entrypoint
def build_agent():
    llm = app.llm(_build_llm())
    tools = app.get_tools()
    bound_llm = llm.bind_tools(app.get_tool_schemas())

    def call_model(state: MessagesState):
        return {"messages": [bound_llm.invoke(state["messages"])]}

    def should_continue(state: MessagesState):
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue)
    graph.add_edge("tools", "agent")

    return graph.compile(checkpointer=app.checkpointer())


def main() -> None:
    """Local dev: load .env and start OE/AER/tool servers via App.run()."""
    load_dotenv()
    app.run()


if __name__ == "__main__":
    main()
