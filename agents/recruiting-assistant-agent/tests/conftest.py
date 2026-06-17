from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


@pytest.fixture(autouse=True)
def suppress_runtime_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("runner_shared.runtime.setup_logging", lambda **kwargs: None)
    monkeypatch.setenv("ENABLE_MEMORY", "false")
    monkeypatch.setenv("ENABLE_TRACING", "false")
    monkeypatch.setenv("ENABLE_GUARDRAILS", "false")
    monkeypatch.setenv("ORG_ID", "507f1f77bcf86cd799439099")
    monkeypatch.setenv("PROJECT_ID", "507f1f77bcf86cd799439112")
    monkeypatch.setenv("WORKSPACE_ID", "recruiting-assistant-agent")
