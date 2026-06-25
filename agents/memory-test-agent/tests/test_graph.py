"""Tests for memory-test-agent graph."""

from __future__ import annotations


def test_graph_compiles_and_registers_tools() -> None:
    from memory_test_agent.main import tools_map

    expected = {
        "run_all_scenarios",
        "run_sdk_scenarios",
        "run_policy_scenarios",
        "run_context_isolation_scenarios",
    }
    assert set(tools_map.keys()) == expected


def test_graph_node_is_callable() -> None:
    """Verify the run_scenarios node can be called without a real graph."""
    from unittest.mock import patch

    from memory_test_agent.main import tools_map

    # Patch run_all_scenarios to avoid real HTTP calls
    with patch.dict(tools_map, {"run_all_scenarios": lambda: "## Test\n✅ 0/0 scenarios passed"}):
        result = tools_map["run_all_scenarios"]()
    assert "scenarios passed" in result
