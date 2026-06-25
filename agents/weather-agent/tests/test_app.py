"""Smoke tests for the weather agent package."""

from weather_agent.agent import app, build_agent


def test_app_and_entrypoint() -> None:
    assert app is not None
    assert callable(build_agent)
