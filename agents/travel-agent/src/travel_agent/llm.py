"""LLM provider selection for travel-agent."""

from __future__ import annotations

import logging
import os
from typing import Any, cast

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


def _openai_kwargs(
    api_key: str,
    model: str,
    temperature: float,
    base_url: str,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "model": model,
        "temperature": temperature,
    }
    if base_url:
        if "grove-foundry" in base_url:
            kwargs["base_url"] = base_url.split("/v1")[0] + "/v1"
            kwargs["default_headers"] = {"api-key": api_key}
        else:
            kwargs["base_url"] = base_url.rstrip("/")
        if "/openai/deployments/" in base_url.lower():
            api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
            kwargs["default_query"] = {"api-version": api_version}
            kwargs["default_headers"] = {"api-key": api_key}
    return kwargs


def build_llm(
    provider: str | None,
    model: str | None,
    temperature: float = 0,
) -> BaseChatModel:
    """Create an LLM from agent.yaml provider/model config and environment secrets."""
    configured_provider = (provider or "").strip().lower()
    configured_model = (model or "").strip()
    if not configured_provider:
        raise RuntimeError(
            "agent.yaml config.provider must be set to one of: cerebras, gemini, openai."
        )
    if not configured_model:
        raise RuntimeError("agent.yaml config.model must be set for travel-agent.")

    cerebras_key = os.environ.get("CEREBRAS_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    openai_base_url = os.environ.get("OPENAI_BASE_URL", "")

    def _build_cerebras() -> BaseChatModel:
        from langchain_cerebras import ChatCerebras

        logger.info("Using Cerebras LLM: %s, temperature=%s", configured_model, temperature)
        kwargs: dict[str, Any] = {
            "api_key": cerebras_key,
            "model": configured_model,
            "temperature": temperature,
        }
        if "qwen-3-32b" in configured_model.lower():
            kwargs["disable_reasoning"] = True
        cerebras_cls: Any = ChatCerebras
        return cast(BaseChatModel, cerebras_cls(**kwargs))

    def _build_gemini() -> BaseChatModel:
        from langchain_google_genai import ChatGoogleGenerativeAI

        logger.info("Using Gemini LLM: %s, temperature=%s", configured_model, temperature)
        gemini_cls: Any = ChatGoogleGenerativeAI
        return cast(
            BaseChatModel,
            gemini_cls(
                api_key=gemini_key,
                model=configured_model,
                temperature=temperature,
                thinking_budget=0,
            ),
        )

    def _build_openai() -> BaseChatModel:
        from langchain_openai import ChatOpenAI

        logger.info("Using OpenAI LLM: %s, temperature=%s", configured_model, temperature)
        openai_cls: Any = ChatOpenAI
        return cast(
            BaseChatModel,
            openai_cls(
                **_openai_kwargs(openai_key, configured_model, temperature, openai_base_url)
            ),
        )

    builders = {
        "cerebras": (cerebras_key, _build_cerebras),
        "gemini": (gemini_key, _build_gemini),
        "openai": (openai_key, _build_openai),
    }
    if configured_provider not in builders:
        raise RuntimeError(
            "Unsupported config.provider in agent.yaml. Use one of: cerebras, gemini, openai."
        )

    provider_key, provider_builder = builders[configured_provider]
    if provider_key:
        return provider_builder()
    raise RuntimeError(
        f"agent.yaml config.provider is set to {configured_provider!r}, "
        "but the matching API key is missing from .env."
    )
