"""Unit tests for ``CodeReviewerLoggingMiddleware``.

The middleware itself is small (one ``awrap_model_call`` override that
logs before and after delegating to the handler), so the tests only
verify two invariants:

1. The handler's response is returned unchanged — middleware must be a
   transparent observer for the parent's response synthesis to work.
2. Both log lines fire in order on the success path; on a handler raise
   the "complete" line is suppressed so the logs accurately reflect the
   model-call lifecycle.
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same minimal env shim used by the other test files in this package.

    Set BEFORE ``code_reviewer_agent.main`` is imported (the imports below are
    deferred into test bodies for that reason — App() runs at module load and
    reads ``RUNNER_MODE`` from the env, so a top-level import here would
    poison ``test_build_agent.py``'s graph fixture by constructing the App
    in a different mode).
    """
    monkeypatch.setenv("RUNNER_MODE", "tool")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-tests")
    monkeypatch.setenv("MONGODB_URI", "mongodb://fake-for-tests:27017")


class _StubResponse:
    """Stand-in for ``ModelResponse`` — the middleware never inspects it."""


@pytest.mark.anyio
async def test_response_is_passed_through_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from code_reviewer_agent.main import CodeReviewerLoggingMiddleware

    middleware = CodeReviewerLoggingMiddleware()
    sentinel = _StubResponse()

    async def handler(_request: object) -> _StubResponse:
        return sentinel

    with caplog.at_level(logging.INFO):
        result = await middleware.awrap_model_call(request=object(), handler=handler)

    assert result is sentinel
    messages = [r.message for r in caplog.records]
    # Both log lines fire on the success path, in order.
    assert messages.count("Model call started") == 1
    assert messages.count("Model call complete") == 1
    assert messages.index("Model call started") < messages.index("Model call complete")


@pytest.mark.anyio
async def test_handler_exception_propagates_without_complete_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the handler raises, the ``complete`` line must NOT fire — otherwise
    the logs would imply a successful model call where there was none, and
    a downstream observability rule that pairs starts with completes would
    silently lose the failure signal."""
    from code_reviewer_agent.main import CodeReviewerLoggingMiddleware

    middleware = CodeReviewerLoggingMiddleware()

    async def handler(_request: object) -> _StubResponse:
        raise RuntimeError("model boom")

    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError, match="model boom"):
            await middleware.awrap_model_call(request=object(), handler=handler)

    messages = [r.message for r in caplog.records]
    assert "Model call started" in messages
    assert "Model call complete" not in messages


@pytest.fixture
def anyio_backend() -> str:
    """Pin async tests to asyncio (matches the SDK's conftest convention)."""
    return "asyncio"
