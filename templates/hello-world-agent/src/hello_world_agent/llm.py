"""LLM provider selection for hello-world-agent."""

from __future__ import annotations

import os

# agentic-create: llm-imports:start
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

# agentic-create: llm-imports:end
from langchain_core.language_models import BaseChatModel

# agentic-create: llm-builder:start
MODEL = "gpt-5.4-mini"


def build_llm(temperature: float = 0) -> BaseChatModel:
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("LLM_API_KEY is missing; add it to .env or project secrets")
    return ChatOpenAI(model=MODEL, api_key=SecretStr(api_key), temperature=temperature)


# agentic-create: llm-builder:end
