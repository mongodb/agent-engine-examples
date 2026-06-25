"""Tests for the LLM factory.

We exercise each provider branch and each special-case URL format
(direct OpenAI, Azure OpenAI, Grove Foundry, Anthropic+Grove,
Gemini, Cerebras) by stubbing the langchain provider modules. This
catches regressions in the env-var handling and base-URL munging
without needing any real API keys or network access.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import pytest

from atlas_admin_agent import llm as llm_mod


class _FakeClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _install_fake_module(name: str, attr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a synthetic module that exports ``attr = _FakeClient``."""
    mod = types.ModuleType(name)
    setattr(mod, attr, _FakeClient)
    monkeypatch.setitem(sys.modules, name, mod)


def _clear_all_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "CEREBRAS_API_KEY",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL",
        "GEMINI_MODEL",
        "CEREBRAS_MODEL",
        "AZURE_OPENAI_API_VERSION",
    ):
        monkeypatch.delenv(key, raising=False)


# --- no key configured ----------------------------------------------------


def test_build_llm_raises_when_no_key_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_all_provider_env(monkeypatch)
    with pytest.raises(ValueError) as excinfo:
        llm_mod.build_llm()
    msg = str(excinfo.value)
    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CEREBRAS_API_KEY"):
        assert name in msg


# --- provider priority: gemini > openai > anthropic > cerebras ---------


def test_gemini_takes_priority_when_multiple_keys_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("CEREBRAS_API_KEY", "c")
    _install_fake_module("langchain_google_genai", "ChatGoogleGenerativeAI", monkeypatch)

    client = llm_mod.build_llm()
    assert isinstance(client, _FakeClient)
    assert client.kwargs["api_key"] == "g"


def test_openai_picked_when_only_openai_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    _install_fake_module("langchain_openai", "ChatOpenAI", monkeypatch)

    client = llm_mod.build_llm()
    assert client.kwargs["api_key"] == "o"


def test_anthropic_picked_when_only_anthropic_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    _install_fake_module("langchain_anthropic", "ChatAnthropic", monkeypatch)

    client = llm_mod.build_llm()
    assert client.kwargs["api_key"] == "a"
    # langchain_anthropic uses `model_name` rather than `model`.
    assert "model_name" in client.kwargs


def test_cerebras_picked_when_only_cerebras_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("CEREBRAS_API_KEY", "c")
    _install_fake_module("langchain_cerebras", "ChatCerebras", monkeypatch)

    client = llm_mod.build_llm()
    assert client.kwargs["api_key"] == "c"


# --- OpenAI-compatible special cases ------------------------------------


def test_openai_with_plain_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://my-proxy.example.com/v1/")
    _install_fake_module("langchain_openai", "ChatOpenAI", monkeypatch)

    client = llm_mod.build_llm()
    assert client.kwargs["base_url"] == "https://my-proxy.example.com/v1"
    # not Azure, so no default_query
    assert "default_query" not in client.kwargs


def test_openai_with_grove_foundry_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "gfkey")
    monkeypatch.setenv(
        "OPENAI_BASE_URL",
        "https://grove-gateway-prod.azure-api.net/grove-foundry-prod/openai/v1",
    )
    _install_fake_module("langchain_openai", "ChatOpenAI", monkeypatch)

    client = llm_mod.build_llm()
    assert client.kwargs["base_url"].endswith("/openai/v1")
    # Grove path uses api-key header, not Authorization
    assert client.kwargs["default_headers"] == {"api-key": "gfkey"}


def test_openai_with_azure_deployments_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "azkey")
    monkeypatch.setenv(
        "OPENAI_BASE_URL",
        "https://my-resource.openai.azure.com/openai/deployments/my-deployment",
    )
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-03-01-preview")
    _install_fake_module("langchain_openai", "ChatOpenAI", monkeypatch)

    client = llm_mod.build_llm()
    # Azure needs api-version query and api-key header.
    assert client.kwargs["default_query"] == {"api-version": "2025-03-01-preview"}
    assert client.kwargs["default_headers"] == {"api-key": "azkey"}


def test_openai_azure_without_api_version_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "azkey")
    monkeypatch.setenv(
        "OPENAI_BASE_URL",
        "https://my-resource.openai.azure.com/openai/deployments/my-deployment",
    )
    _install_fake_module("langchain_openai", "ChatOpenAI", monkeypatch)

    client = llm_mod.build_llm()
    assert "api-version" in client.kwargs["default_query"]


def test_openai_uses_custom_model_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.9-custom")
    _install_fake_module("langchain_openai", "ChatOpenAI", monkeypatch)

    client = llm_mod.build_llm()
    assert client.kwargs["model"] == "gpt-4.9-custom"


def test_build_llm_accepts_explicit_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    _install_fake_module("langchain_openai", "ChatOpenAI", monkeypatch)

    client = llm_mod.build_llm(model="explicit-model")
    assert client.kwargs["model"] == "explicit-model"


# --- Anthropic-compatible special cases ---------------------------------


def test_anthropic_with_grove_foundry_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak")
    monkeypatch.setenv(
        "ANTHROPIC_BASE_URL",
        "https://grove-gateway-prod.azure-api.net/grove-foundry-prod/anthropic/v1",
    )
    _install_fake_module("langchain_anthropic", "ChatAnthropic", monkeypatch)

    client = llm_mod.build_llm()
    # Grove strips to the pre-/v1 root
    assert client.kwargs["base_url"].endswith("/grove-foundry-prod/anthropic")
    assert client.kwargs["default_headers"] == {"api-key": "ak"}


def test_anthropic_with_plain_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://my-anthropic.example.com/v1/")
    _install_fake_module("langchain_anthropic", "ChatAnthropic", monkeypatch)

    client = llm_mod.build_llm()
    assert client.kwargs["base_url"] == "https://my-anthropic.example.com/v1"
    assert "default_headers" not in client.kwargs


# --- patch_runtime_llm safety -------------------------------------------


def test_patch_runtime_llm_is_idempotent_and_safe_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If runner_shared isn't importable, patch_runtime_llm must not raise.
    monkeypatch.setitem(sys.modules, "runner_shared.runtime", None)
    llm_mod.patch_runtime_llm()  # no exception


def test_patch_runtime_llm_replaces_create_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub runner_shared.runtime.TenantRuntime so we can observe the patch.
    runtime_mod = types.ModuleType("runner_shared.runtime")

    class TenantRuntime:
        def _create_llm(self, model=None, temperature=0.0):
            return "original"

    runtime_mod.TenantRuntime = TenantRuntime  # type: ignore[attr-defined]
    # The parent package may need to exist too so import works.
    parent = sys.modules.get("runner_shared") or types.ModuleType("runner_shared")
    parent.runtime = runtime_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runner_shared", parent)
    monkeypatch.setitem(sys.modules, "runner_shared.runtime", runtime_mod)

    # Reload the llm module so its import of TenantRuntime picks up our stub.
    importlib.reload(llm_mod)

    # Stub OpenAI so build_llm succeeds.
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    _install_fake_module("langchain_openai", "ChatOpenAI", monkeypatch)

    llm_mod.patch_runtime_llm()
    instance = TenantRuntime()
    result = instance._create_llm(model="override-model")
    assert isinstance(result, _FakeClient)
    assert result.kwargs["model"] == "override-model"
