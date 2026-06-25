"""LLM provider selection for data-analyst-agent."""

from __future__ import annotations

import logging
import os
from typing import Any, cast

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


def build_llm(
    *,
    llm_config: Any | None = None,
    model: str | None = None,
    temperature: float = 0,
) -> BaseChatModel:
    """Create a chat model from agent.yaml hints plus environment secrets."""
    configured_provider = (getattr(llm_config, "provider", None) or "").strip().lower()
    configured_model = model or getattr(llm_config, "model", None)

    builders = {
        "gemini": (os.environ.get("GEMINI_API_KEY", ""), _build_gemini),
        "openai": (os.environ.get("OPENAI_API_KEY", ""), _build_openai),
        "anthropic": (os.environ.get("ANTHROPIC_API_KEY", ""), _build_anthropic),
        "cerebras": (os.environ.get("CEREBRAS_API_KEY", ""), _build_cerebras),
    }

    if configured_provider:
        if configured_provider not in builders:
            raise RuntimeError(
                "Unsupported config.provider in agent.yaml. "
                "Use one of: anthropic, cerebras, gemini, openai."
            )
        provider_key, provider_builder = builders[configured_provider]
        if not provider_key:
            raise RuntimeError(
                f"agent.yaml config.provider is set to {configured_provider!r}, "
                "but the matching API key is missing from the environment."
            )
        return provider_builder(configured_model, temperature)

    for provider_key, provider_builder in builders.values():
        if provider_key:
            return provider_builder(configured_model, temperature)

    raise RuntimeError(
        "No LLM API key found. Set GEMINI_API_KEY, OPENAI_API_KEY, "
        "ANTHROPIC_API_KEY, or CEREBRAS_API_KEY."
    )


def _build_gemini(model: str | None, temperature: float) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    model_name = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    logger.info("Using Gemini LLM: %s", model_name)
    return cast(
        BaseChatModel,
        ChatGoogleGenerativeAI(
            api_key=os.environ["GEMINI_API_KEY"],
            model=model_name,
            temperature=temperature,
            thinking_budget=0,
        ),
    )


def _build_openai(model: str | None, temperature: float) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    model_name = model or os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
    kwargs: dict[str, Any] = {
        "api_key": os.environ["OPENAI_API_KEY"],
        "model": model_name,
        "temperature": temperature,
    }
    openai_base_url = os.environ.get("OPENAI_BASE_URL", "")
    if openai_base_url:
        if "grove-foundry" in openai_base_url:
            kwargs["base_url"] = openai_base_url.split("/v1")[0] + "/v1"
            kwargs["default_headers"] = {"api-key": os.environ["OPENAI_API_KEY"]}
        else:
            kwargs["base_url"] = openai_base_url.rstrip("/")
        if "/openai/deployments/" in openai_base_url.lower():
            kwargs["default_query"] = {
                "api-version": os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
            }
            kwargs["default_headers"] = {"api-key": os.environ["OPENAI_API_KEY"]}
    return cast(BaseChatModel, ChatOpenAI(**kwargs))


def _build_anthropic(model: str | None, temperature: float) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    model_name = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    kwargs: dict[str, Any] = {
        "api_key": os.environ["ANTHROPIC_API_KEY"],
        "model_name": model_name,
        "temperature": temperature,
    }
    anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if anthropic_base_url:
        if "grove-foundry" in anthropic_base_url:
            kwargs["base_url"] = anthropic_base_url.split("/v1")[0].rstrip("/")
            kwargs["default_headers"] = {"api-key": os.environ["ANTHROPIC_API_KEY"]}
        else:
            kwargs["base_url"] = anthropic_base_url.rstrip("/")
    return cast(BaseChatModel, ChatAnthropic(**kwargs))


def _build_cerebras(model: str | None, temperature: float) -> BaseChatModel:
    from langchain_cerebras import ChatCerebras

    model_name = model or os.environ.get("CEREBRAS_MODEL", "qwen-3-235b-a22b-instruct-2507")
    logger.info("Using Cerebras LLM: %s", model_name)
    return cast(
        BaseChatModel,
        cast(Any, ChatCerebras)(
            api_key=os.environ["CEREBRAS_API_KEY"],
            model=model_name,
            temperature=temperature,
        ),
    )
