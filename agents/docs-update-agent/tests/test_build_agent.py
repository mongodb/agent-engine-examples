"""Unit tests for ``docs_update_agent.main.build_agent``.

The tests configure fake provider credentials so ``build_agent()`` can compile
the LangGraph locally without touching external services. No live LLM or
tool-pod calls are made.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

EXPECTED_GITHUB_TOOLS = {
    "list_recent_commits",
    "get_commit_diff",
    "get_file_content",
    "list_directory",
    "create_branch",
    "create_or_update_file",
    "create_pull_request",
}

AGENT_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Set the minimum env required for ``build_agent`` to run offline."""
    monkeypatch.setenv("RUNNER_MODE", "tool")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-tests")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4-mini")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("MONGODB_URI", "mongodb://fake-for-tests:27017")
    monkeypatch.setenv("ENABLE_TRACING", "false")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")


@pytest.fixture()
def graph(monkeypatch):
    """Import the agent and invoke its entrypoint builder."""
    import docs_update_agent.main as agent_main

    monkeypatch.setattr(agent_main.app, "checkpointer", lambda: None)
    return agent_main.build_agent()


class TestBuildAgent:
    def test_agent_manifest_does_not_enable_deep_agent_feature(self):
        manifest = yaml.safe_load((AGENT_DIR / "agent.yaml").read_text())

        assert "features" not in manifest or "deep_agent" not in manifest["features"]

    def test_returns_compiled_graph(self, graph):
        from langgraph.graph.state import CompiledStateGraph

        assert isinstance(graph, CompiledStateGraph)

    def test_graph_exposes_expected_github_tools(self, graph):
        tool_node = graph.nodes["tools"].bound
        tool_names = set(tool_node.tools_by_name.keys())
        missing = EXPECTED_GITHUB_TOOLS - tool_names
        assert not missing, f"GitHub tools missing from graph: {missing}"

    def test_graph_has_model_and_tools_nodes(self, graph):
        nodes = set(graph.nodes.keys())
        assert "agent" in nodes
        assert "tools" in nodes


# ---------------------------------------------------------------------------
# Plain LangGraph ReAct wiring
# ---------------------------------------------------------------------------


def _capture_create_react_agent_kwargs(monkeypatch):
    """Monkeypatch ``create_react_agent`` and capture its kwargs."""
    import docs_update_agent.main as agent_main

    captured: dict[str, object] = {}

    def _fake_create_react_agent(*args, **kwargs):
        captured.update(kwargs)
        return MagicMock(name="compiled_graph")

    monkeypatch.setattr(agent_main, "create_react_agent", _fake_create_react_agent)
    return captured, agent_main


class TestReactAgentWiring:
    def test_create_react_agent_gets_model_tools_and_prompt(self, monkeypatch):
        captured, agent_main = _capture_create_react_agent_kwargs(monkeypatch)
        agent_main.build_agent()

        assert set(captured) == {"model", "tools", "prompt"}
        assert captured["model"] is not None
        tool_names = {tool.name for tool in captured["tools"]}
        assert EXPECTED_GITHUB_TOOLS <= tool_names
        assert "Default to FAST REPORT MODE" in captured["prompt"]

    def test_no_deep_agent_only_imports_or_calls(self):
        main_path = AGENT_DIR / "src" / "docs_update_agent" / "main.py"
        source = main_path.read_text()

        for forbidden in ("app.deep_agent", "AgentMiddleware", "SkillsMiddleware"):
            assert forbidden not in source, (
                f"main.py must not reference `{forbidden}` for the SDK-compatible "
                "single-agent fallback."
            )


class TestRuntimeConfiguration:
    def test_system_prompt_uses_current_env_defaults(self, monkeypatch):
        monkeypatch.setenv("TARGET_REPO", "10gen/custom-repo")
        monkeypatch.setenv("LOOKBACK_DAYS", "7")
        captured, agent_main = _capture_create_react_agent_kwargs(monkeypatch)

        agent_main.build_agent()

        system_prompt = captured["prompt"]
        assert "operate on `10gen/custom-repo`" in system_prompt
        assert "use `7`\nday(s)" in system_prompt
        assert "Analyze at most `3` commit(s) per run." in system_prompt
        assert "Dispatch at most `1` specialist subagent(s) per run." in system_prompt
        assert "read at most `2` documentation\n  file(s)" in system_prompt
        assert "Stop after" not in system_prompt
        assert "model call" not in system_prompt
        assert "runtime budget" not in system_prompt.lower()
        assert "AER dispatch timeout" not in system_prompt

    def test_system_prompt_defaults_to_fast_report_mode(self, monkeypatch):
        captured, agent_main = _capture_create_react_agent_kwargs(monkeypatch)

        agent_main.build_agent()

        system_prompt = captured["prompt"]
        assert "Default to FAST REPORT MODE" in system_prompt
        assert "Do NOT call write tools" in system_prompt
        assert "Only use WRITE PR MODE if the user explicitly asks" in system_prompt
        assert "Do not create a branch, update files, or open a PR" in system_prompt

    def test_system_prompt_requires_concise_pr_description_with_source_refs(self, monkeypatch):
        captured, agent_main = _capture_create_react_agent_kwargs(monkeypatch)

        agent_main.build_agent()

        system_prompt = captured["prompt"]
        assert "## Summary" in system_prompt
        assert "## Documentation changes" in system_prompt
        assert "Keep the body concise" in system_prompt
        assert "source commit SHA" in system_prompt
        assert "source PR reference from `list_recent_commits`" in system_prompt
        assert "10gen/agentic-platform#123" in system_prompt
        assert "End the PR body with exactly: `Authored by docs-update-agent.`" in system_prompt

    def test_system_prompt_skips_docs_prefixed_commits(self, monkeypatch):
        captured, agent_main = _capture_create_react_agent_kwargs(monkeypatch)

        agent_main.build_agent()

        system_prompt = captured["prompt"]
        assert "Skip commits whose first-line commit message starts with" in system_prompt
        assert "`docs:`" in system_prompt

    def test_list_recent_commits_defaults_to_lookback_days_env(self, monkeypatch):
        import docs_update_agent.main as agent_main

        calls: list[tuple[str, int, str]] = []

        class FakeClient:
            def list_recent_commits(self, repo: str, since_days: int, branch: str) -> str:
                calls.append((repo, since_days, branch))
                return "ok"

        monkeypatch.setenv("LOOKBACK_DAYS", "5")
        monkeypatch.setattr(agent_main, "_client", FakeClient)

        result = asyncio.run(agent_main.list_recent_commits("10gen/example"))

        assert result == "ok"
        assert calls == [("10gen/example", 5, "main")]
