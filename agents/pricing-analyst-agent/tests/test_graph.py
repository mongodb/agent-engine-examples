from __future__ import annotations

from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage


class FakeLLM(FakeMessagesListChatModel):
    def bind_tools(self, tools: list[Any], **kwargs: Any) -> FakeLLM:  # type: ignore[override]
        return self


def test_graph_compiles_and_executes() -> None:
    from pricing_analyst_agent import main

    fake_llm = FakeLLM(responses=[AIMessage(content="I can help with pricing workflows.")])
    result = main.build_agent(llm=fake_llm).invoke({"messages": [HumanMessage(content="Hello")]})

    response = result["messages"][-1]
    assert isinstance(response, AIMessage)
    assert response.content == "I can help with pricing workflows."


def test_graph_finishes_without_tool_calls() -> None:
    from pricing_analyst_agent import main

    fake_llm = FakeLLM(responses=[AIMessage(content="No tool call needed here.")])
    result = main.build_agent(llm=fake_llm).invoke({"messages": [HumanMessage(content="Hi")]})

    response = result["messages"][-1]
    assert isinstance(response, AIMessage)
    assert not response.tool_calls
