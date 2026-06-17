"""LLM provider selection for simple-agent."""

from __future__ import annotations

import logging
import os
from typing import Any, cast

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


def patch_runtime_llm() -> None:
    """Monkey-patch the runner_shared runtime to support Azure OpenAI.

    The runtime's built-in ``_create_llm`` uses plain ``ChatOpenAI`` which doesn't
    add the ``?api-version=`` query parameter that Azure requires. This patch
    replaces ``_create_llm`` with our ``build_llm`` so that every LLM created
    (including by the Tool Pod) goes through the Azure-aware path.
    """
    try:
        from runner_shared.runtime import TenantRuntime  # type: ignore[import-untyped]

        def _patched_create_llm(
            self: Any, model: str | None = None, temperature: float = 0.0
        ) -> BaseChatModel:
            return build_llm(model=model, temperature=temperature)

        TenantRuntime._create_llm = _patched_create_llm  # type: ignore[assignment]
        logger.info("Patched runtime _create_llm for Azure OpenAI support")
    except Exception as exc:
        logger.warning("Could not patch runtime _create_llm: %s", exc)


def build_llm(model: str | None = None, temperature: float = 0) -> BaseChatModel:
    """Create an LLM from environment variables."""
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    openai_base_url = os.environ.get("OPENAI_BASE_URL", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    cerebras_key = os.environ.get("CEREBRAS_API_KEY", "")

    if gemini_key:
        from langchain_google_genai import ChatGoogleGenerativeAI

        model_name = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        logger.info("Using Gemini LLM: %s", model_name)
        gemini_cls: Any = ChatGoogleGenerativeAI
        return cast(
            BaseChatModel,
            gemini_cls(
                api_key=gemini_key,
                model=model_name,
                temperature=temperature,
                thinking_budget=0,
            ),
        )

    if openai_key:
        from langchain_openai import ChatOpenAI

        model_name = model or os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
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

    if anthropic_key:
        from langchain_anthropic import ChatAnthropic

        model_name = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
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

    if cerebras_key:
        from langchain_cerebras import ChatCerebras

        model_name = model or os.environ.get("CEREBRAS_MODEL", "qwen-3-235b-a22b-instruct-2507")
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

    raise ValueError(
        "No LLM API key configured. Set one of: "
        "GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, CEREBRAS_API_KEY"
    )
