"""Shared test fixtures for the Atlas Admin Agent tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


@pytest.fixture(autouse=True)
def _suppress_runtime_and_set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic: disable runner logging side-effects and set fake creds.

    Real Atlas credentials aren't needed — tests monkeypatch ``_get_client`` or
    pass fake HTTP transports — but the env must have *something* so importing
    ``main`` / ``tools`` doesn't fail.
    """
    try:
        monkeypatch.setattr("runner_shared.runtime.setup_logging", lambda **kwargs: None)
    except Exception:
        # runner_shared may not be installed in all test environments.
        pass
    monkeypatch.setenv("ENABLE_TRACING", "false")
    monkeypatch.setenv("ENABLE_MEMORY", "false")
    monkeypatch.setenv("ATLAS_PUBLIC_KEY", "fake-public")
    monkeypatch.setenv("ATLAS_PRIVATE_KEY", "fake-private")
    monkeypatch.setenv("ATLAS_ORG_ID", "fake-org")
    monkeypatch.setenv("MONGODB_URI", "")
    monkeypatch.setenv("MONGODB_DATABASE", "atlas_admin_agent_test")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
