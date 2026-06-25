from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage


class FakeLLM(FakeMessagesListChatModel):
    def bind_tools(self, tools: list[Any], **kwargs: Any) -> FakeLLM:  # type: ignore[override]
        return self


def test_graph_compiles_and_executes() -> None:
    from recruiting_assistant_agent import main

    fake_llm = FakeLLM(responses=[AIMessage(content="I can help with recruiting workflows.")])
    result = main.build_agent(llm=fake_llm).invoke({"messages": [HumanMessage(content="Hello")]})

    response = result["messages"][-1]
    assert isinstance(response, AIMessage)
    assert response.content == "I can help with recruiting workflows."


def test_graph_finishes_without_tool_calls() -> None:
    from recruiting_assistant_agent import main

    fake_llm = FakeLLM(responses=[AIMessage(content="No tool call needed here.")])
    result = main.build_agent(llm=fake_llm).invoke({"messages": [HumanMessage(content="Hi")]})

    response = result["messages"][-1]
    assert isinstance(response, AIMessage)
    assert not response.tool_calls


def test_build_agent_uses_runtime_llm_wrapper(monkeypatch) -> None:
    from recruiting_assistant_agent import main

    fake_llm = FakeLLM(responses=[AIMessage(content="Wrapped runtime LLM works.")])
    sentinel_model = object()
    captured_model = None

    def fake_build_runtime_llm(temperature: float = 0):
        assert temperature == 0
        return sentinel_model

    def fake_app_llm(model):
        nonlocal captured_model
        captured_model = model
        return fake_llm

    monkeypatch.setattr(main, "_build_runtime_llm", fake_build_runtime_llm)
    monkeypatch.setattr(main.app, "llm", fake_app_llm)

    result = main.build_agent().invoke({"messages": [HumanMessage(content="Hello")]})

    assert captured_model is sentinel_model
    assert result["messages"][-1].content == "Wrapped runtime LLM works."


def test_select_llm_provider_prefers_cerebras_before_gemini() -> None:
    from recruiting_assistant_agent import main

    assert main._select_llm_provider("cerebras-key", "gemini-key", "openai-key") == "cerebras"
    assert main._select_llm_provider("", "gemini-key", "openai-key") == "gemini"
    assert main._select_llm_provider("", "", "openai-key") == "openai"


def test_openai_base_url_kwargs_cover_grove_and_azure(monkeypatch) -> None:
    from recruiting_assistant_agent import main

    grove_kwargs = main._openai_llm_kwargs(
        "openai-key",
        "gpt-test",
        0,
        "https://grove-gateway.example.net/grove-foundry-prod/openai/v1/chat/completions",
    )
    assert (
        grove_kwargs["base_url"] == "https://grove-gateway.example.net/grove-foundry-prod/openai/v1"
    )
    assert grove_kwargs["default_headers"] == {"api-key": "openai-key"}

    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
    azure_kwargs = main._openai_llm_kwargs(
        "azure-key",
        "gpt-test",
        0,
        "https://example.openai.azure.com/openai/deployments/recruiting",
    )
    assert (
        azure_kwargs["base_url"] == "https://example.openai.azure.com/openai/deployments/recruiting"
    )
    assert azure_kwargs["default_query"] == {"api-version": "2025-01-01-preview"}
    assert azure_kwargs["default_headers"] == {"api-key": "azure-key"}


def test_validate_policy_uses_public_guardrails_client(monkeypatch) -> None:
    from recruiting_assistant_agent import main

    captured: dict[str, Any] = {}

    class FakeGuardrailsClient:
        def validate_output(self, text: str, *, context: Any = None) -> Any:
            captured["text"] = text
            captured["context"] = context
            return SimpleNamespace(
                validation_passed=False,
                errors=["batch_size policy"],
                validator_name="outreach_policy",
            )

    monkeypatch.setattr(main.app._runtime, "_guardrails_client", FakeGuardrailsClient())

    result = main._validate_policy({"batch_size": 20})

    assert result["require_review"] is True
    assert result["reasons"] == ["batch_size policy"]
    assert json.loads(captured["text"])["context"] == {"batch_size": 20}


def test_memory_saves_require_active_user(monkeypatch) -> None:
    from recruiting_assistant_agent import main

    monkeypatch.setattr(main.app, "get_current_user_id", lambda: None)

    summary = json.loads(main.save_conversation_summary("title", "summary"))
    insight = json.loads(main.save_learned_insight("insight", ["tag"]))

    assert summary == {"status": "error", "message": "No active user context available."}
    assert insight == {"status": "error", "message": "No active user context available."}


def test_build_memory_context_response_exposes_selected_memories(monkeypatch) -> None:
    from recruiting_assistant_agent import main

    selected_memory = SimpleNamespace(
        source="semantic",
        metadata={
            "source": "candidate_profile",
            "label": "candidate_profile",
            "contextual_metadata": {"candidate_name": "Marcus Chen"},
        },
    )
    rich_context = SimpleNamespace(
        formatted_context="Marcus Chen has robotics experience.",
        selected_memories=[selected_memory],
    )

    class FakeMemoryEngine:
        def build_context(self, **kwargs: Any) -> Any:
            assert kwargs["include_memories"] is True
            assert kwargs["session_id"] == "session-1"
            return rich_context

    monkeypatch.setattr(main, "_memory_engine", lambda: FakeMemoryEngine())

    result = main._build_memory_context_response(
        query="robotics candidates",
        user_id="user-1",
        session_id="session-1",
    )

    assert result is rich_context
    assert main._build_citation_index(result.selected_memories) == (
        "[1] Candidate Profiles — Marcus Chen"
    )


def test_main_runs_registered_app(monkeypatch) -> None:
    from recruiting_assistant_agent import main

    called = False

    def fake_run() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(main.app, "run", fake_run)

    main.main()

    assert called is True
