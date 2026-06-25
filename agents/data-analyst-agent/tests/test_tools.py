from __future__ import annotations

import json
from typing import Any, Callable

from data_analyst_agent.data_store import DemoDataStore
from data_analyst_agent.tools import register_tools


class FakeMemory:
    def search_semantic(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"label": "semantic", "query": kwargs["query"]}]

    def search_taxonomic(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"term": "taxonomic", "query": kwargs["query"]}]

    def search_episodes(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"title": "episodic", "query": kwargs["query"]}]

    def discover_procedures(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"procedure": "pedal-cohort-pricing-review", "query": kwargs["query"]}]


class FakeApp:
    def __init__(self) -> None:
        self.memory = FakeMemory()
        self.registered_tools: dict[str, Callable[..., str]] = {}

    def tool(self, **kwargs: Any) -> Callable[[Callable[..., str]], Callable[..., str]]:
        def decorator(func: Callable[..., str]) -> Callable[..., str]:
            self.registered_tools[func.__name__] = func
            return func

        return decorator

    def get_current_user_id(self) -> str:
        return "reviewer-user"


def test_registered_memory_recall_tool_invokes_memory_helper() -> None:
    app = FakeApp()
    register_tools(app, DemoDataStore(records=[]))

    result = json.loads(app.registered_tools["recall_demo_memories"]("pedal cohort"))

    assert result["semantic"] == [{"label": "semantic", "query": "pedal cohort"}]
    assert result["taxonomic"] == [{"term": "taxonomic", "query": "pedal cohort"}]
    assert result["episodic"] == [{"title": "episodic", "query": "pedal cohort"}]
    assert result["procedural"] == [
        {"procedure": "pedal-cohort-pricing-review", "query": "pedal cohort"}
    ]
