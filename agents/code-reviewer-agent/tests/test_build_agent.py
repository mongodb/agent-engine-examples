"""Unit tests for ``code_reviewer_agent.main.build_agent``.

These tests catch the class of bug that silently slipped through the
deep-agent-demo bring-up: anything that makes ``build_agent()`` explode
at graph-construction time (wrong middleware protocol, missing env var,
renamed SDK symbol) would have failed these tests long before the user
tried the playground UI.

The test strategy is deliberately minimal:

1. Configure env vars (``RUNNER_MODE=tool``, fake OPENAI creds) so that
   the module can be imported without touching the network and without
   triggering the AER-mode ``SecureWrappedLLM`` wrapping.
2. Call ``build_agent()`` and assert it returns a compiled LangGraph.
3. Enumerate the bound tool names and assert the expected deep-agent
   built-ins are present — catches any future shadowing by a custom
   ``tools=[...]`` entry using the same name as a built-in.

No live LLM or tool-pod calls are made; this runs in under a second
and has no external dependencies.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# The deep-agent built-ins we expect ``app.deep_agent()`` to wire into
# the graph via the MagentaToolPodBackend + deepagents middleware.
# (``write_todos`` and ``task`` come from deepagents' base middleware.)
EXPECTED_BUILTIN_TOOLS = {
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Set the minimum env required for ``build_agent`` to run offline.

    ``RUNNER_MODE=tool`` bypasses ``SecureWrappedLLM`` wrapping inside
    ``App.llm()`` — we're only exercising graph construction, not the
    secure I/O path.
    """
    monkeypatch.setenv("RUNNER_MODE", "tool")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-tests")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("MONGODB_URI", "mongodb://fake-for-tests:27017")
    monkeypatch.setenv("ENABLE_TRACING", "false")


@pytest.fixture()
def graph(monkeypatch):
    """Import the agent and invoke its entrypoint builder."""
    # Import is deferred so env fixture has a chance to run first.
    import code_reviewer_agent.main as agent_main

    agent_main.app._runtime.agent_config.features.deep_agent = True
    monkeypatch.setattr(agent_main.app, "checkpointer", lambda: None)
    return agent_main.build_agent()


class TestBuildAgent:
    def test_returns_compiled_graph(self, graph):
        """``build_agent()`` must return a compiled LangGraph state graph."""
        from langgraph.graph.state import CompiledStateGraph

        assert isinstance(graph, CompiledStateGraph)

    def test_graph_exposes_expected_builtin_tools(self, graph):
        """The compiled graph's tool node must include all deep-agent built-ins.

        Catches regressions where custom ``tools=`` entries shadow a
        built-in by name (e.g. defining ``@tool def write_file`` would
        silently replace the sandboxed built-in and send writes to AER's
        real filesystem instead of the tool pod).
        """
        tool_node = graph.nodes["tools"].bound
        tool_names = set(tool_node.tools_by_name.keys())
        missing = EXPECTED_BUILTIN_TOOLS - tool_names
        assert not missing, f"built-in tools missing from graph: {missing}"

    def test_graph_has_model_and_tools_nodes(self, graph):
        """Sanity: the agent loop has the expected model <-> tools nodes."""
        nodes = set(graph.nodes.keys())
        assert "model" in nodes
        assert "tools" in nodes


class TestBuildAgentGroveHeaders:
    """``_build_llm`` must emit the correct auth header for Grove endpoints."""

    def test_default_reasoning_effort_is_medium(self, monkeypatch):
        monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)

        import code_reviewer_agent.main as agent_main

        llm = agent_main._build_llm()

        assert llm.reasoning_effort == "medium"

    def test_blank_reasoning_effort_falls_back_to_medium(self, monkeypatch):
        monkeypatch.setenv("OPENAI_REASONING_EFFORT", "")

        import code_reviewer_agent.main as agent_main

        llm = agent_main._build_llm()

        assert llm.reasoning_effort == "medium"

    def test_grove_base_url_uses_api_key_header(self, monkeypatch):
        monkeypatch.setenv(
            "OPENAI_BASE_URL",
            "https://grove-gateway-prod.azure-api.net/grove-foundry-prod/openai/v1",
        )
        monkeypatch.setenv("OPENAI_API_KEY", "secret-grove-key")

        # Re-import after env changes so the new base URL is read.
        import importlib

        import code_reviewer_agent.main as agent_main

        importlib.reload(agent_main)

        llm = agent_main._build_llm()

        headers = (
            getattr(llm, "default_headers", None)
            or getattr(llm, "model_kwargs", {}).get("default_headers")
            or {}
        )
        assert headers.get("api-key") == "secret-grove-key", (
            "Grove base URL requires api-key header, not Authorization: Bearer. "
            f"Got headers: {headers}"
        )

    def test_non_grove_base_url_uses_default_auth(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-regular-key")

        import importlib

        import code_reviewer_agent.main as agent_main

        importlib.reload(agent_main)

        llm = agent_main._build_llm()

        headers = (
            getattr(llm, "default_headers", None)
            or getattr(llm, "model_kwargs", {}).get("default_headers")
            or {}
        )
        # Vanilla OpenAI should not override with api-key header.
        assert "api-key" not in (headers or {})


# ---------------------------------------------------------------------------
# DeepAgent skills=[...] canonical-kwarg wiring
# ---------------------------------------------------------------------------
#
# These tests pin the bundled-skills contract: specialists declare relative
# ``skills=[...]`` paths, the SDK resolves them from the directory containing
# ``agent.yaml``, and the example does not repoint the writable ToolPod
# workspace at its bundled skill files.
#
# The pattern is "capture-only": we monkeypatch ``app.deep_agent`` on the
# App instance before invoking ``build_agent``, record the kwargs, and
# return a ``MagicMock`` so ``build_agent`` returns cleanly. No graph is
# actually compiled — this is a pure contract test on the call-site.


def _capture_deep_agent_kwargs(monkeypatch):
    """Monkeypatch ``app.deep_agent`` and return a dict that will be
    populated with its call kwargs when ``build_agent`` runs.
    """
    import code_reviewer_agent.main as agent_main

    captured: dict[str, object] = {}

    def _fake_deep_agent(*args, **kwargs):
        captured.update(kwargs)
        return MagicMock(name="compiled_graph")

    monkeypatch.setattr(agent_main.app, "deep_agent", _fake_deep_agent)
    return captured, agent_main


class TestSkillsWiring:
    """Phase A architecture: parent-orchestrator + eight specialist subagents.

    Each specialist owns exactly one skill (skills do NOT inherit across
    the parent/subagent boundary in deepagents). The parent has `skills=None`.

    Guards against regressions:
      1. Parent ``skills=[...]`` leaking back in (Phase A mandates parent=None).
      2. Wrong number of specialists or missing ones.
      3. Specialist with no skills= declared, or the wrong skill path.
      4. ``backend=`` override bypassing the audited ``MagentaToolPodBackend``.
      5. Middleware-bypass imports.
      6. Missing ``response_format`` on any specialist (breaks structured
         output aggregation by the parent).
    """

    EXPECTED_SPECIALISTS = {
        "convention_reviewer": "convention-quality",
        "concurrency_reviewer": "concurrency-leaks",
        "db_reviewer": "db-patterns",
        "error_handling_reviewer": "error-handling",
        "security_reviewer": "security-review",
        "test_coverage_reviewer": "test-coverage",
        "documentation_reviewer": "documentation",
        "api_stability_reviewer": "api-stability",
    }

    def test_parent_has_no_skills(self, monkeypatch):
        """Phase A: parent orchestrator must pass ``skills=None``."""
        captured, agent_main = _capture_deep_agent_kwargs(monkeypatch)
        agent_main.build_agent()
        assert captured.get("skills") is None, (
            "Phase A requires the parent agent to have skills=None "
            f"(skills live on specialist subagents). Got: {captured.get('skills')!r}"
        )

    def test_eight_specialists_configured(self, monkeypatch):
        """``subagents=`` must be the 8-element Phase A specialist roster."""
        captured, agent_main = _capture_deep_agent_kwargs(monkeypatch)
        agent_main.build_agent()

        assert "subagents" in captured, "build_agent() did not pass subagents= to app.deep_agent"
        specialists = captured["subagents"]
        assert isinstance(specialists, list), (
            f"subagents must be a list, got {type(specialists).__name__}"
        )
        assert len(specialists) == 8, (
            f"Phase A requires exactly 8 specialists, got {len(specialists)}"
        )

        got_names = {s["name"] for s in specialists}
        expected_names = set(self.EXPECTED_SPECIALISTS)
        assert got_names == expected_names, (
            f"specialist roster mismatch — missing: {expected_names - got_names}, "
            f"extra: {got_names - expected_names}"
        )

    def test_each_specialist_has_its_own_skill(self, monkeypatch):
        """Each specialist must declare exactly one skill path matching the expected mapping."""
        captured, agent_main = _capture_deep_agent_kwargs(monkeypatch)
        agent_main.build_agent()

        specialists = captured["subagents"]
        for spec in specialists:
            name = spec["name"]
            assert "skills" in spec, f"specialist {name!r} is missing skills= declaration"
            skills = spec["skills"]
            assert isinstance(skills, list) and len(skills) == 1, (
                f"specialist {name!r} must declare exactly one skill, got {skills!r}"
            )
            expected_dir = self.EXPECTED_SPECIALISTS[name]
            expected_path = f"skills/{expected_dir}"
            assert skills[0] == expected_path, (
                f"specialist {name!r} must load skill {expected_path!r}, got {skills[0]!r}"
            )

    def test_specialists_do_not_use_response_format(self, monkeypatch):
        """Phase A emits prose findings with REVIEW-RULE-ID-* citations.

        Structured output via ``response_format=ToolStrategy(FindingsList)``
        was the original Phase A ambition but hit a downstream middleware
        issue (``'dict' object has no attribute 'schema'``) that isn't
        worth chasing for a demo-grade app. Phase B will add structured
        output + confidence scoring once the middleware path is sorted.

        This test pins the current Phase A decision so a future change
        that re-adds response_format also gets the Phase B treatment.
        """
        captured, agent_main = _capture_deep_agent_kwargs(monkeypatch)
        agent_main.build_agent()

        specialists = captured["subagents"]
        for spec in specialists:
            assert "response_format" not in spec, (
                f"specialist {spec['name']!r} has response_format set — "
                "Phase A uses prose output, not structured. If you are "
                "re-adding structured output, move this to Phase B."
            )

    def test_no_backend_override(self, monkeypatch):
        """``backend=`` must NOT be passed (default MagentaToolPodBackend)."""
        captured, agent_main = _capture_deep_agent_kwargs(monkeypatch)

        agent_main.build_agent()

        assert "backend" not in captured, (
            "build_agent() passed `backend=` to app.deep_agent — v0.3 "
            "canonical path requires the default MagentaToolPodBackend. "
            f"Captured kwargs: {list(captured.keys())}"
        )

    def test_no_middleware_bypass_imports(self):
        """main.py must not import middleware-bypass symbols.

        The v0.3 canonical path is ``skills=[...]`` with the default
        backend. Importing ``SkillsMiddleware``, ``FilesystemBackend``,
        or ``LocalShellBackend`` signals a middleware-bypass pattern
        that skips the audited MagentaToolPodBackend I/O flow.
        """
        main_path = (
            Path(__file__).resolve().parent.parent / "src" / "code_reviewer_agent" / "main.py"
        )
        source = main_path.read_text()

        for forbidden in ("SkillsMiddleware", "FilesystemBackend", "LocalShellBackend"):
            assert forbidden not in source, (
                f"main.py must not reference `{forbidden}` — v0.3 canonical "
                f"skills wiring uses the `skills=[...]` kwarg with the default "
                f"MagentaToolPodBackend (no middleware bypass)."
            )
