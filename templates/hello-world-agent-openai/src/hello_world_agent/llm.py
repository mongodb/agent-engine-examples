"""LLM provider selection for hello-world-agent-openai."""

from __future__ import annotations

import os

# agentic-create: llm-imports:start
from agents import OpenAIResponsesModel
from openai import AsyncOpenAI

# agentic-create: llm-imports:end
from agents import Model

# agentic-create: llm-builder:start
MODEL = "gpt-5.4-mini"


def build_llm() -> Model:
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("LLM_API_KEY is missing; add it to .env or project secrets")
    return OpenAIResponsesModel(model=MODEL, openai_client=AsyncOpenAI(api_key=api_key))


# agentic-create: llm-builder:end
