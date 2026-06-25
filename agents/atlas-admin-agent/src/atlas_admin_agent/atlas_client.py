"""MongoDB Atlas Administration API v2 HTTP client.

Thin wrapper around ``httpx`` with:

- HTTP Digest authentication (Atlas programmatic API keys).
- Automatic retry with exponential backoff for 429 and 5xx responses.
  ``Retry-After`` headers are respected.
- Pagination helper that follows ``links[rel=next]`` responses.
- Long-poll helper for async Atlas operations (cluster IDLE, restore job
  completion, etc.).

The client is deliberately generic: callers identify endpoints by path
(e.g. ``/groups/{project_id}/clusters``) and the wrapper handles auth,
headers, retries, and JSON parsing. This keeps the agent's surface area
small while still covering every Atlas Admin endpoint.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://cloud.mongodb.com/api/atlas/v2"
DEFAULT_API_VERSION_ACCEPT = "application/vnd.atlas.2025-03-12+json"
DEFAULT_PAGE_SIZE = 500
MAX_PAGES = 200  # safety cap to avoid runaway pagination

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_ATTEMPTS = 5
RETRY_BACKOFF_BASE_SECONDS = 1.0
RETRY_BACKOFF_MAX_SECONDS = 30.0

# Methods accepted by the generic mutation tools. Everything else is rejected
# before we touch httpx so an LLM typo surfaces as a clear validation error.
ALLOWED_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


class AtlasConfigError(ValueError):
    """Raised when required Atlas credentials are missing from the environment."""


class AtlasApiError(RuntimeError):
    """Non-retryable HTTP error from the Atlas Admin API.

    Atlas error responses follow the documented shape:
    ``{"detail": ..., "error": 404, "errorCode": "RESOURCE_NOT_FOUND",
    "parameters": [...], "reason": "Not Found"}``. We lift the useful fields
    onto the exception so callers (and the LLM via ``atlas_execute``) can
    surface them to users.

    A ``status`` of ``0`` is used for transport-level failures (timeouts,
    DNS errors) where we never received an HTTP status.
    """

    def __init__(
        self,
        status: int,
        error_code: str | None,
        body: str,
        url: str,
        *,
        detail: str | None = None,
        reason: str | None = None,
        parameters: list[Any] | None = None,
    ) -> None:
        summary = error_code or reason or "http_error"
        super().__init__(f"Atlas API {status} on {url}: {summary} {body[:500]}")
        self.status = status
        self.error_code = error_code
        self.body = body
        self.url = url
        self.detail = detail
        self.reason = reason
        self.parameters = parameters or []

    @property
    def is_transport_error(self) -> bool:
        return self.status == 0


class AtlasPollTimeout(TimeoutError):
    """Raised when ``AtlasClient.poll`` exhausts its deadline."""

    def __init__(self, path: str, last_response: Any, timeout_s: int) -> None:
        super().__init__(f"Polling {path} timed out after {timeout_s}s")
        self.path = path
        self.last_response = last_response
        self.timeout_s = timeout_s


@dataclass(frozen=True)
class AtlasConfig:
    public_key: str
    private_key: str
    org_id: str
    base_url: str = DEFAULT_BASE_URL
    api_version_accept: str = DEFAULT_API_VERSION_ACCEPT
    timeout_seconds: float = 30.0


class AtlasClient:
    """HTTP client for the Atlas Administration API v2."""

    def __init__(
        self,
        config: AtlasConfig,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._sleep = sleep
        self._monotonic = monotonic
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            auth=httpx.DigestAuth(config.public_key, config.private_key),
            timeout=config.timeout_seconds,
            headers={
                "Accept": config.api_version_accept,
                "Content-Type": "application/json",
            },
            transport=transport,
        )

    @property
    def config(self) -> AtlasConfig:
        return self._config

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AtlasClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- core request path ------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> dict[str, Any]:
        """Perform a single API call with retry/backoff.

        Returns the parsed JSON body for 2xx responses. A 204 returns ``{}``.
        Non-retryable 4xx responses raise ``AtlasApiError``.
        """
        method_u = method.upper()
        normalized_path = path if path.startswith("/") else f"/{path}"
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.request(method_u, normalized_path, params=params, json=json)
            except httpx.HTTPError as exc:
                if attempt >= MAX_RETRY_ATTEMPTS:
                    raise AtlasApiError(0, "http_error", str(exc), normalized_path) from exc
                self._backoff(attempt, None)
                continue

            if 200 <= response.status_code < 300:
                if response.status_code == 204 or not response.content:
                    return {}
                return response.json()

            if response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRY_ATTEMPTS:
                self._backoff(attempt, response.headers.get("Retry-After"))
                continue

            body_text = response.text or ""
            parsed_error = _parse_atlas_error(response)
            raise AtlasApiError(
                response.status_code,
                parsed_error.get("errorCode"),
                body_text,
                normalized_path,
                detail=parsed_error.get("detail"),
                reason=parsed_error.get("reason"),
                parameters=parsed_error.get("parameters"),
            )

    # --- pagination -------------------------------------------------------

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_items: int | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Iterator[dict[str, Any]]:
        """Yield results across all pages of an Atlas list endpoint.

        Follows ``links[rel=next]`` to advance. Stops when no ``next`` link is
        returned, when ``max_items`` results have been yielded, or when the
        ``MAX_PAGES`` safety cap is reached.
        """
        merged = dict(params or {})
        merged.setdefault("itemsPerPage", page_size)
        merged.setdefault("pageNum", 1)

        yielded = 0
        for page_index in range(MAX_PAGES):
            page = self.request("GET", path, params=merged)
            for item in page.get("results", []) or []:
                yield item
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            if not _has_next_link(page):
                return
            merged["pageNum"] = int(merged["pageNum"]) + 1
        logger.warning(
            "paginate(%s) stopped after %d pages — increase MAX_PAGES or use max_items",
            path,
            MAX_PAGES,
        )

    # --- polling ----------------------------------------------------------

    def poll(
        self,
        path: str,
        *,
        predicate: Callable[[dict[str, Any]], bool],
        timeout_s: int,
        interval_s: float = 30.0,
        params: dict[str, Any] | None = None,
        describe: str = "",
    ) -> dict[str, Any]:
        """Poll a GET endpoint until ``predicate(body)`` is truthy.

        Raises :class:`AtlasPollTimeout` once the deadline passes. Designed for
        long-running Atlas operations like waiting for a cluster to reach IDLE
        or a restore job to finish.
        """
        deadline = self._monotonic() + timeout_s
        last: dict[str, Any] = {}
        while True:
            last = self.request("GET", path, params=params)
            if predicate(last):
                return last
            if self._monotonic() >= deadline:
                if describe:
                    logger.warning("Poll %s timed out after %ss", describe, timeout_s)
                raise AtlasPollTimeout(path, last, timeout_s)
            self._sleep(interval_s)

    # --- internal helpers -------------------------------------------------

    def _backoff(self, attempt: int, retry_after_header: str | None) -> None:
        delay: float
        if retry_after_header:
            try:
                delay = max(0.0, float(retry_after_header))
            except (TypeError, ValueError):
                delay = RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        else:
            delay = RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        delay = min(delay, RETRY_BACKOFF_MAX_SECONDS)
        logger.info("Atlas API retry attempt=%d delay=%.2fs", attempt, delay)
        self._sleep(delay)


def from_env() -> AtlasClient:
    """Construct an ``AtlasClient`` from environment variables.

    Reads ``ATLAS_PUBLIC_KEY``, ``ATLAS_PRIVATE_KEY``, ``ATLAS_ORG_ID``, and
    (optionally) ``ATLAS_API_BASE_URL``. Raises :class:`AtlasConfigError` if
    required values are missing.
    """
    public_key = os.environ.get("ATLAS_PUBLIC_KEY", "").strip()
    private_key = os.environ.get("ATLAS_PRIVATE_KEY", "").strip()
    org_id = os.environ.get("ATLAS_ORG_ID", "").strip()
    base_url = os.environ.get("ATLAS_API_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL

    missing = [
        name
        for name, value in (
            ("ATLAS_PUBLIC_KEY", public_key),
            ("ATLAS_PRIVATE_KEY", private_key),
            ("ATLAS_ORG_ID", org_id),
        )
        if not value
    ]
    if missing:
        raise AtlasConfigError(f"Missing required Atlas credentials: {', '.join(missing)}")

    return AtlasClient(
        AtlasConfig(
            public_key=public_key,
            private_key=private_key,
            org_id=org_id,
            base_url=base_url,
        )
    )


def _has_next_link(page: dict[str, Any]) -> bool:
    for link in page.get("links", []) or []:
        if isinstance(link, dict) and link.get("rel") == "next":
            return True
    return False


def _parse_atlas_error(response: httpx.Response) -> dict[str, Any]:
    """Extract the documented Atlas error envelope from a non-2xx response.

    Atlas returns ``{detail, error, errorCode, parameters, reason}``. Fields
    may be missing on older endpoints or when the body isn't JSON.
    """
    try:
        data = response.json()
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("detail", "reason", "errorCode"):
        val = data.get(key)
        if isinstance(val, str):
            out[key] = val
    params = data.get("parameters")
    if isinstance(params, list):
        out["parameters"] = params
    return out


def _extract_atlas_error_code(response: httpx.Response) -> Optional[str]:
    """Backwards-compat wrapper used by older callers/tests."""
    return _parse_atlas_error(response).get("errorCode")
