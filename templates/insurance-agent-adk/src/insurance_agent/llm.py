"""LLM provider selection for insurance-agent-adk."""

from __future__ import annotations

import os

# agentic-create: llm-imports:start
from google.adk.models.lite_llm import LiteLlm

# agentic-create: llm-imports:end
from google.adk.models import BaseLlm

# agentic-create: llm-builder:start
MODEL = "openai/gpt-5.4-mini"


def build_llm(temperature: float = 0) -> BaseLlm:
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("LLM_API_KEY is missing; add it to .env or project secrets")
    return LiteLlm(model=MODEL, api_key=api_key, temperature=temperature)


# agentic-create: llm-builder:end
