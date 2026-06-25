from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def suppress_runtime_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("runner_shared.runtime.setup_logging", lambda **kwargs: None)
