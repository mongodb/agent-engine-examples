"""LLM provider selection for hello-world-agent."""

from __future__ import annotations

import logging
import os
from typing import Any, cast

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


def build_llm(
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0,
) -> BaseChatModel:
    """Create an LLM from agent.yaml hints and environment variables."""
    configured_provider = (provider or "").strip().lower()
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    openai_base_url = os.environ.get("OPENAI_BASE_URL", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    cerebras_key = os.environ.get("CEREBRAS_API_KEY", "")

    def _build_gemini() -> BaseChatModel:
        from langchain_google_genai import ChatGoogleGenerativeAI

        model_name = model or "gemini-2.5-flash"
        logger.info("Using Gemini LLM: %s", model_name)
        kwargs: dict[str, Any] = {
            "api_key": gemini_key,
            "model": model_name,
            "temperature": temperature,
        }
        if model_name.startswith("gemini-2.5-"):
            kwargs["thinking_budget"] = 0
        gemini_cls: Any = ChatGoogleGenerativeAI
        return cast(BaseChatModel, gemini_cls(**kwargs))

    def _build_openai() -> BaseChatModel:
        from langchain_openai import ChatOpenAI

        model_name = model or "gpt-5.4-mini"
        logger.info("Using OpenAI LLM: %s", model_name)
        kwargs: dict[str, Any] = {
            "api_key": openai_key,
            "model": model_name,
            "temperature": temperature,
        }
        if openai_base_url:
            if "grove-foundry" in openai_base_url:
                kwargs["base_url"] = openai_base_url.split("/v1")[0] + "/v1"
                kwargs["default_headers"] = {"api-key": openai_key}
            else:
                kwargs["base_url"] = openai_base_url.rstrip("/")
            if "/openai/deployments/" in openai_base_url.lower():
                api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
                kwargs["default_query"] = {"api-version": api_version}
                kwargs["default_headers"] = {"api-key": openai_key}
        openai_cls: Any = ChatOpenAI
        return cast(BaseChatModel, openai_cls(**kwargs))

    def _build_anthropic() -> BaseChatModel:
        from langchain_anthropic import ChatAnthropic

        model_name = model or "claude-sonnet-4-6"
        logger.info("Using Anthropic LLM: %s", model_name)
        kwargs: dict[str, Any] = {
            "api_key": anthropic_key,
            "model_name": model_name,
            "temperature": temperature,
        }
        if anthropic_base_url:
            if "grove-foundry" in anthropic_base_url:
                kwargs["base_url"] = anthropic_base_url.split("/v1")[0].rstrip("/")
                kwargs["default_headers"] = {"api-key": anthropic_key}
            else:
                kwargs["base_url"] = anthropic_base_url.rstrip("/")
        anthropic_cls: Any = ChatAnthropic
        return cast(BaseChatModel, anthropic_cls(**kwargs))

    def _build_cerebras() -> BaseChatModel:
        from langchain_cerebras import ChatCerebras

        model_name = model or "qwen-3-235b-a22b-instruct-2507"
        logger.info("Using Cerebras LLM: %s", model_name)
        cerebras_cls: Any = ChatCerebras
        return cast(
            BaseChatModel,
            cerebras_cls(
                api_key=cerebras_key,
                model=model_name,
                temperature=temperature,
            ),
        )

    builders = {
        "gemini": (gemini_key, _build_gemini),
        "openai": (openai_key, _build_openai),
        "anthropic": (anthropic_key, _build_anthropic),
        "cerebras": (cerebras_key, _build_cerebras),
    }

    if configured_provider:
        if configured_provider not in builders:
            raise RuntimeError(
                "Unsupported config.provider in agent.yaml. "
                "Use one of: anthropic, cerebras, gemini, openai."
            )
        provider_key, provider_builder = builders[configured_provider]
        if provider_key:
            return provider_builder()
        raise RuntimeError(
            f"agent.yaml config.provider is set to {configured_provider!r}, "
            "but the matching API key is missing from .env."
        )

    for provider_key, provider_builder in builders.values():
        if provider_key:
            return provider_builder()

    raise RuntimeError(
        "No LLM API key found. Set one of: "
        "GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, CEREBRAS_API_KEY."
    )
