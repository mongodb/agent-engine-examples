from __future__ import annotations

import sys
import types

import pytest


def test_build_llm_uses_agent_yaml_model_instead_of_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from travel_agent import llm as llm_module

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    monkeypatch.setitem(
        sys.modules,
        "langchain_openai",
        types.SimpleNamespace(ChatOpenAI=FakeChatOpenAI),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "env-openai-model")

    llm = llm_module.build_llm(provider="openai", model="agent-yaml-model", temperature=0.2)

    assert isinstance(llm, FakeChatOpenAI)
    assert llm.kwargs["model"] == "agent-yaml-model"
    assert llm.kwargs["temperature"] == 0.2


def test_build_llm_requires_agent_yaml_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from travel_agent import llm as llm_module

    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "env-openai-model")

    with pytest.raises(RuntimeError, match="config.model"):
        llm_module.build_llm(provider="openai", model=None)


def test_build_llm_requires_configured_provider_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from travel_agent import llm as llm_module

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

    with pytest.raises(RuntimeError, match="matching API key is missing"):
        llm_module.build_llm(provider="openai", model="agent-yaml-model")
