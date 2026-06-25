"""Tests for ``atlas_client.AtlasClient``.

These tests exercise the behaviors documented for the Atlas Administration
API v2:

- Accept header ``application/vnd.atlas.<yyyy-mm-dd>+json``, Content-Type
  ``application/json`` (https://www.mongodb.com/docs/atlas/api/versioned-api-overview/).
- Pagination envelope ``{results, links, totalCount}`` with ``pageNum`` /
  ``itemsPerPage`` query params (default 100, max 500) and ``links[rel=next]``
  for continuation.
- Error envelope ``{detail, error, errorCode, parameters, reason}``.
- Rate limiting: HTTP 429 with optional ``Retry-After`` and optional
  ``RateLimit-Limit`` / ``RateLimit-Remaining`` headers; may be missing.

Everything is driven through ``httpx.MockTransport`` so no network calls
happen.
"""

from __future__ import annotations

import json

import httpx
import pytest

from atlas_admin_agent.atlas_client import (
    ALLOWED_HTTP_METHODS,
    DEFAULT_API_VERSION_ACCEPT,
    DEFAULT_BASE_URL,
    DEFAULT_PAGE_SIZE,
    MAX_PAGES,
    MAX_RETRY_ATTEMPTS,
    RETRYABLE_STATUSES,
    AtlasApiError,
    AtlasClient,
    AtlasConfig,
    AtlasConfigError,
    AtlasPollTimeout,
    _extract_atlas_error_code,
    _parse_atlas_error,
    from_env,
)


# --- fixtures -------------------------------------------------------------


def _fake_config(base_url: str = "https://atlas.test/api/atlas/v2") -> AtlasConfig:
    return AtlasConfig(
        public_key="pub",
        private_key="priv",
        org_id="org",
        base_url=base_url,
        timeout_seconds=5.0,
    )


def _build_client(
    handler,
    *,
    sleep=lambda _s: None,
    monotonic=None,
    base_url="https://atlas.test/api/atlas/v2",
) -> AtlasClient:
    transport = httpx.MockTransport(handler)
    mono = monotonic or (lambda: 0.0)
    return AtlasClient(
        _fake_config(base_url),
        sleep=sleep,
        monotonic=mono,
        transport=transport,
    )


# --- module-level constants match docs ------------------------------------


def test_default_base_url_matches_docs() -> None:
    assert DEFAULT_BASE_URL == "https://cloud.mongodb.com/api/atlas/v2"


def test_default_accept_header_has_versioned_shape() -> None:
    # Docs: Accept: application/vnd.atlas.<yyyy-mm-dd>+json
    assert DEFAULT_API_VERSION_ACCEPT.startswith("application/vnd.atlas.")
    assert DEFAULT_API_VERSION_ACCEPT.endswith("+json")


def test_allowed_http_methods_covers_the_standard_set() -> None:
    assert ALLOWED_HTTP_METHODS == {"GET", "POST", "PUT", "PATCH", "DELETE"}


def test_retryable_statuses_covers_429_and_5xx_family() -> None:
    assert 429 in RETRYABLE_STATUSES
    for status in (500, 502, 503, 504):
        assert status in RETRYABLE_STATUSES


def test_default_page_size_matches_atlas_maximum() -> None:
    # Docs: itemsPerPage max is 500.
    assert DEFAULT_PAGE_SIZE == 500


# --- request: happy path + headers ----------------------------------------


def test_request_returns_parsed_json_for_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/groups/p1/clusters")
        return httpx.Response(200, json={"results": [{"name": "c1"}], "links": []})

    with _build_client(handler) as client:
        body = client.request("GET", "/groups/p1/clusters")

    assert body == {"results": [{"name": "c1"}], "links": []}


def test_request_sends_versioned_accept_header_and_content_type() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["accept"] = request.headers.get("Accept")
        seen["content_type"] = request.headers.get("Content-Type")
        return httpx.Response(200, json={})

    with _build_client(handler) as client:
        client.request("GET", "/orgs")

    assert seen["accept"] == DEFAULT_API_VERSION_ACCEPT
    assert seen["content_type"] == "application/json"


def test_request_normalizes_missing_leading_slash() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={})

    with _build_client(handler) as client:
        client.request("GET", "orgs")

    assert seen["path"].endswith("/orgs")
    assert not seen["path"].endswith("//orgs")


def test_request_body_is_sent_as_json() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(201, json={"id": "abc"})

    with _build_client(handler) as client:
        client.request("POST", "/groups", json={"name": "new"})

    assert seen["body"] == {"name": "new"}


def test_request_without_body_sends_no_request_body() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_length"] = len(request.content)
        return httpx.Response(204)

    with _build_client(handler) as client:
        client.request("DELETE", "/groups/x/clusters/y")

    assert seen["content_length"] == 0


def test_request_passes_query_params_through() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["envelope"] = request.url.params.get("envelope")
        seen["pretty"] = request.url.params.get("pretty")
        return httpx.Response(200, json={"results": []})

    with _build_client(handler) as client:
        client.request("GET", "/groups", params={"envelope": "true", "pretty": "true"})

    assert seen["envelope"] == "true"
    assert seen["pretty"] == "true"


def test_request_lowercase_method_is_normalized() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return httpx.Response(200, json={})

    with _build_client(handler) as client:
        client.request("get", "/orgs")

    assert seen["method"] == "GET"


# --- status code handling -------------------------------------------------


def test_request_204_returns_empty_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    with _build_client(handler) as client:
        assert client.request("DELETE", "/groups/x/clusters/y") == {}


def test_request_2xx_with_empty_body_returns_empty_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, content=b"")

    with _build_client(handler) as client:
        assert client.request("POST", "/groups/x/clusters/y:pause") == {}


def test_request_raises_on_406_when_accept_header_is_missing_or_wrong() -> None:
    # Docs: Atlas returns 406 if Accept header is missing or unversioned.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            406,
            json={
                "detail": "Invalid Accept header.",
                "error": 406,
                "errorCode": "NOT_ACCEPTABLE",
                "reason": "Not Acceptable",
                "parameters": [],
            },
        )

    client = _build_client(handler)
    try:
        with pytest.raises(AtlasApiError) as excinfo:
            client.request("GET", "/orgs")
    finally:
        client.close()

    assert excinfo.value.status == 406
    assert excinfo.value.error_code == "NOT_ACCEPTABLE"


def test_request_raises_atlas_api_error_with_full_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "detail": "No project named 'missing' exists.",
                "error": 404,
                "errorCode": "RESOURCE_NOT_FOUND",
                "parameters": ["missing"],
                "reason": "Not Found",
            },
        )

    client = _build_client(handler)
    try:
        with pytest.raises(AtlasApiError) as excinfo:
            client.request("GET", "/groups/missing")
    finally:
        client.close()

    err = excinfo.value
    assert err.status == 404
    assert err.error_code == "RESOURCE_NOT_FOUND"
    assert err.detail == "No project named 'missing' exists."
    assert err.reason == "Not Found"
    assert err.parameters == ["missing"]
    assert not err.is_transport_error


def test_request_raises_on_4xx_without_parseable_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"<html>not json</html>")

    client = _build_client(handler)
    try:
        with pytest.raises(AtlasApiError) as excinfo:
            client.request("GET", "/groups")
    finally:
        client.close()

    assert excinfo.value.status == 400
    assert excinfo.value.error_code is None
    assert excinfo.value.detail is None


# --- retry behavior -------------------------------------------------------


def test_request_retries_on_429_with_retry_after() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="slow down")
        return httpx.Response(200, json={"ok": True})

    client = _build_client(handler, sleep=sleeps.append)
    try:
        body = client.request("GET", "/groups")
    finally:
        client.close()

    assert body == {"ok": True}
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_request_retries_on_429_without_retry_after_using_backoff() -> None:
    # Docs: Atlas "might not always return the response headers. You must handle
    # missing response headers gracefully."
    calls: list[int] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(len(calls) + 1)
        if len(calls) < 3:
            return httpx.Response(429, json={"errorCode": "RATE_LIMITED_TOKEN_BUCKET"})
        return httpx.Response(200, json={"ok": True})

    client = _build_client(handler, sleep=sleeps.append)
    try:
        client.request("GET", "/groups")
    finally:
        client.close()

    # exponential backoff: attempt 1 -> 1s, attempt 2 -> 2s
    assert sleeps == [1.0, 2.0]


def test_request_retries_on_5xx_with_exponential_backoff() -> None:
    sleeps: list[float] = []
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={"ok": True})

    client = _build_client(handler, sleep=sleeps.append)
    try:
        body = client.request("GET", "/groups")
    finally:
        client.close()

    assert body == {"ok": True}
    assert len(attempts) == 3
    assert sleeps == [1.0, 2.0]


def test_request_gives_up_after_max_retry_attempts_on_5xx() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500, json={"errorCode": "INTERNAL"})

    client = _build_client(handler)
    try:
        with pytest.raises(AtlasApiError) as excinfo:
            client.request("GET", "/groups")
    finally:
        client.close()

    assert len(attempts) == MAX_RETRY_ATTEMPTS
    assert excinfo.value.status == 500


def test_request_retry_after_invalid_value_falls_back_to_backoff() -> None:
    sleeps: list[float] = []
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "not-a-number"})
        return httpx.Response(200, json={})

    client = _build_client(handler, sleep=sleeps.append)
    try:
        client.request("GET", "/groups")
    finally:
        client.close()

    # falls back to 1s (attempt 1 backoff) rather than crashing
    assert sleeps == [1.0]


def test_request_backoff_is_capped_at_max_seconds() -> None:
    # Monkey around the backoff cap by forcing many 5xx and inspecting delays.
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if len(sleeps) < 4:
            return httpx.Response(503)
        return httpx.Response(200, json={})

    client = _build_client(handler, sleep=sleeps.append)
    try:
        client.request("GET", "/groups")
    finally:
        client.close()

    # Sleeps should all be <= RETRY_BACKOFF_MAX_SECONDS (30.0 in the module)
    from atlas_admin_agent.atlas_client import RETRY_BACKOFF_MAX_SECONDS

    for s in sleeps:
        assert s <= RETRY_BACKOFF_MAX_SECONDS


def test_request_does_not_retry_on_non_retryable_4xx() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(404, json={"errorCode": "RESOURCE_NOT_FOUND"})

    client = _build_client(handler)
    try:
        with pytest.raises(AtlasApiError):
            client.request("GET", "/groups/missing")
    finally:
        client.close()

    assert len(attempts) == 1


def test_request_retries_on_transport_errors() -> None:
    # httpx.TimeoutException is an HTTPError; we retry with backoff and
    # ultimately raise AtlasApiError with status=0 (transport error).
    attempts: list[int] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectTimeout("bad dns")

    client = _build_client(handler, sleep=sleeps.append)
    try:
        with pytest.raises(AtlasApiError) as excinfo:
            client.request("GET", "/groups")
    finally:
        client.close()

    assert len(attempts) == MAX_RETRY_ATTEMPTS
    err = excinfo.value
    assert err.is_transport_error
    assert err.status == 0


# --- pagination -----------------------------------------------------------


def test_paginate_follows_next_link_until_exhausted() -> None:
    pages = {
        1: {
            "results": [{"name": "c1"}, {"name": "c2"}],
            "totalCount": 4,
            "links": [{"rel": "next", "href": "..."}],
        },
        2: {
            "results": [{"name": "c3"}],
            "totalCount": 4,
            "links": [{"rel": "next", "href": "..."}],
        },
        3: {
            "results": [{"name": "c4"}],
            "totalCount": 4,
            "links": [{"rel": "self", "href": "..."}],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page_num = int(request.url.params.get("pageNum", "1"))
        return httpx.Response(200, json=pages[page_num])

    with _build_client(handler) as client:
        items = list(client.paginate("/groups/p1/clusters"))

    assert [i["name"] for i in items] == ["c1", "c2", "c3", "c4"]


def test_paginate_uses_500_default_items_per_page() -> None:
    # Docs: default is 100; Atlas max is 500. Our client defaults to 500
    # to minimize round-trips.
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.setdefault("first_items_per_page", request.url.params.get("itemsPerPage"))
        return httpx.Response(200, json={"results": [], "links": []})

    with _build_client(handler) as client:
        list(client.paginate("/groups"))

    assert seen["first_items_per_page"] == "500"


def test_paginate_respects_explicit_page_size() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("itemsPerPage"))
        return httpx.Response(200, json={"results": [], "links": []})

    with _build_client(handler) as client:
        list(client.paginate("/groups", page_size=50))

    assert seen == ["50"]


def test_paginate_respects_max_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [{"n": 1}, {"n": 2}, {"n": 3}, {"n": 4}],
                "links": [{"rel": "next", "href": "..."}],
            },
        )

    with _build_client(handler) as client:
        items = list(client.paginate("/groups", max_items=2))

    assert len(items) == 2
    assert [i["n"] for i in items] == [1, 2]


def test_paginate_stops_when_no_links_present_at_all() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"id": "only"}]})

    with _build_client(handler) as client:
        items = list(client.paginate("/orgs"))

    assert items == [{"id": "only"}]


def test_paginate_next_link_rel_is_case_sensitive_and_not_matched_by_others() -> None:
    # Only "next" short-circuits advancement. "nextPage" must not.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [{"id": "a"}],
                "links": [{"rel": "self"}, {"rel": "nextPage", "href": "..."}],
            },
        )

    with _build_client(handler) as client:
        items = list(client.paginate("/orgs"))

    assert items == [{"id": "a"}]


def test_paginate_advances_pagenum_by_one_per_request() -> None:
    page_nums: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params.get("pageNum", "1"))
        page_nums.append(n)
        if n < 3:
            return httpx.Response(
                200,
                json={
                    "results": [{"page": n}],
                    "links": [{"rel": "next", "href": "..."}],
                },
            )
        return httpx.Response(200, json={"results": [{"page": 3}], "links": []})

    with _build_client(handler) as client:
        list(client.paginate("/orgs"))

    assert page_nums == [1, 2, 3]


def test_paginate_preserves_user_params() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("envelope"))
        return httpx.Response(200, json={"results": [], "links": []})

    with _build_client(handler) as client:
        list(client.paginate("/groups", params={"envelope": "true"}))

    assert seen == ["true"]


def test_paginate_hits_max_pages_safety_cap(caplog: pytest.LogCaptureFixture) -> None:
    import logging as _logging

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [{"id": "x"}],
                "links": [{"rel": "next", "href": "..."}],
            },
        )

    with caplog.at_level(_logging.WARNING, logger="atlas_admin_agent.atlas_client"):
        with _build_client(handler) as client:
            items = list(client.paginate("/groups"))

    # We yielded one item per page for every page we tried.
    assert len(items) == MAX_PAGES
    # And logged a warning so this doesn't silently hide from operators.
    assert any(
        "MAX_PAGES" in r.getMessage() or "stopped after" in r.getMessage() for r in caplog.records
    )


# --- poll -----------------------------------------------------------------


def test_poll_returns_when_predicate_true() -> None:
    responses = [
        {"stateName": "CREATING"},
        {"stateName": "UPDATING"},
        {"stateName": "IDLE"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    sleeps: list[float] = []
    client = _build_client(handler, sleep=sleeps.append)
    try:
        body = client.poll(
            "/groups/p/clusters/c",
            predicate=lambda b: b.get("stateName") == "IDLE",
            timeout_s=100,
            interval_s=10,
        )
    finally:
        client.close()

    assert body == {"stateName": "IDLE"}
    assert sleeps == [10, 10]


def test_poll_times_out_logs_describe(caplog: pytest.LogCaptureFixture) -> None:
    import logging as _logging

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stateName": "CREATING"})

    fake_clock = iter([0, 11])  # deadline = 10 from start; exceeded on first check

    client = _build_client(handler, sleep=lambda _s: None, monotonic=lambda: next(fake_clock))
    try:
        with caplog.at_level(_logging.WARNING, logger="atlas_admin_agent.atlas_client"):
            with pytest.raises(AtlasPollTimeout):
                client.poll(
                    "/groups/p/clusters/c",
                    predicate=lambda b: False,
                    timeout_s=10,
                    interval_s=1,
                    describe="waiting for cluster",
                )
    finally:
        client.close()

    # The descriptive warning should have been logged.
    assert any("waiting for cluster" in r.getMessage() for r in caplog.records)


def test_poll_times_out_and_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stateName": "CREATING"})

    fake_clock = iter([0, 5, 11, 17])  # deadline is 10

    client = _build_client(
        handler,
        sleep=lambda _s: None,
        monotonic=lambda: next(fake_clock),
    )
    try:
        with pytest.raises(AtlasPollTimeout) as excinfo:
            client.poll(
                "/groups/p/clusters/c",
                predicate=lambda b: b.get("stateName") == "IDLE",
                timeout_s=10,
                interval_s=1,
            )
    finally:
        client.close()

    assert excinfo.value.path == "/groups/p/clusters/c"
    assert excinfo.value.timeout_s == 10
    assert excinfo.value.last_response == {"stateName": "CREATING"}


def test_poll_propagates_api_errors_without_retry_suppression() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errorCode": "FORBIDDEN"})

    with _build_client(handler) as client:
        with pytest.raises(AtlasApiError) as excinfo:
            client.poll(
                "/groups/p/clusters/c",
                predicate=lambda b: False,
                timeout_s=5,
                interval_s=1,
            )
    assert excinfo.value.status == 403


def test_poll_predicate_true_on_first_try_does_not_sleep() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stateName": "IDLE"})

    sleeps: list[float] = []
    with _build_client(handler, sleep=sleeps.append) as client:
        client.poll(
            "/groups/p/clusters/c",
            predicate=lambda b: b.get("stateName") == "IDLE",
            timeout_s=10,
            interval_s=999,
        )
    assert sleeps == []


def test_poll_passes_query_params() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("envelope"))
        return httpx.Response(200, json={"ready": True})

    with _build_client(handler) as client:
        client.poll(
            "/foo",
            predicate=lambda b: bool(b.get("ready")),
            timeout_s=1,
            interval_s=0,
            params={"envelope": "true"},
        )
    assert seen == ["true"]


# --- from_env / config ----------------------------------------------------


def test_from_env_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("ATLAS_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("ATLAS_ORG_ID", raising=False)

    with pytest.raises(AtlasConfigError) as excinfo:
        from_env()

    msg = str(excinfo.value)
    assert "ATLAS_PUBLIC_KEY" in msg
    assert "ATLAS_PRIVATE_KEY" in msg
    assert "ATLAS_ORG_ID" in msg


def test_from_env_reports_single_missing_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_PUBLIC_KEY", "a")
    monkeypatch.setenv("ATLAS_PRIVATE_KEY", "b")
    monkeypatch.delenv("ATLAS_ORG_ID", raising=False)

    with pytest.raises(AtlasConfigError) as excinfo:
        from_env()

    msg = str(excinfo.value)
    assert "ATLAS_ORG_ID" in msg
    assert "ATLAS_PUBLIC_KEY" not in msg
    assert "ATLAS_PRIVATE_KEY" not in msg


def test_from_env_uses_defaults_when_base_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLAS_PUBLIC_KEY", "p")
    monkeypatch.setenv("ATLAS_PRIVATE_KEY", "k")
    monkeypatch.setenv("ATLAS_ORG_ID", "org")
    monkeypatch.delenv("ATLAS_API_BASE_URL", raising=False)

    client = from_env()
    try:
        assert client.config.base_url == DEFAULT_BASE_URL
    finally:
        client.close()


def test_from_env_trims_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_PUBLIC_KEY", "  p  ")
    monkeypatch.setenv("ATLAS_PRIVATE_KEY", "\tk\n")
    monkeypatch.setenv("ATLAS_ORG_ID", " org ")

    client = from_env()
    try:
        assert client.config.public_key == "p"
        assert client.config.private_key == "k"
        assert client.config.org_id == "org"
    finally:
        client.close()


# --- error envelope parser ------------------------------------------------


def test_parse_atlas_error_extracts_documented_fields() -> None:
    response = httpx.Response(
        400,
        json={
            "detail": "Cluster name too long.",
            "error": 400,
            "errorCode": "CLUSTER_NAME_TOO_LONG",
            "parameters": ["reallyreallyreally-long-name", 64],
            "reason": "Bad Request",
        },
    )
    parsed = _parse_atlas_error(response)
    assert parsed == {
        "detail": "Cluster name too long.",
        "errorCode": "CLUSTER_NAME_TOO_LONG",
        "reason": "Bad Request",
        "parameters": ["reallyreallyreally-long-name", 64],
    }


def test_parse_atlas_error_handles_html_response() -> None:
    response = httpx.Response(502, content=b"<html>gateway</html>")
    assert _parse_atlas_error(response) == {}


def test_parse_atlas_error_ignores_non_string_fields() -> None:
    response = httpx.Response(
        400, json={"errorCode": 123, "detail": {"nested": "x"}, "reason": "ok"}
    )
    parsed = _parse_atlas_error(response)
    assert "errorCode" not in parsed
    assert "detail" not in parsed
    assert parsed["reason"] == "ok"


def test_parse_atlas_error_handles_list_top_level() -> None:
    response = httpx.Response(400, json=["a", "b"])
    assert _parse_atlas_error(response) == {}


def test_extract_atlas_error_code_backcompat_wrapper() -> None:
    # Legacy helper still works for callers that only need errorCode.
    response = httpx.Response(400, json={"errorCode": "X"})
    assert _extract_atlas_error_code(response) == "X"
    response = httpx.Response(400, content=b"html")
    assert _extract_atlas_error_code(response) is None


# --- AtlasApiError construction -------------------------------------------


def test_atlas_api_error_message_prefers_errorcode_then_reason() -> None:
    err1 = AtlasApiError(404, "RESOURCE_NOT_FOUND", "body", "/url")
    assert "RESOURCE_NOT_FOUND" in str(err1)

    err2 = AtlasApiError(404, None, "body", "/url", reason="Not Found")
    assert "Not Found" in str(err2)

    err3 = AtlasApiError(0, None, "timeout", "/url")
    assert "http_error" in str(err3)


def test_atlas_api_error_truncates_long_bodies() -> None:
    long_body = "X" * 2000
    err = AtlasApiError(500, "INTERNAL", long_body, "/url")
    # message should not carry the full 2000-char body
    assert len(str(err)) < 1000


def test_atlas_api_error_is_transport_error_only_for_status_zero() -> None:
    assert AtlasApiError(0, None, "", "/u").is_transport_error
    assert not AtlasApiError(500, None, "", "/u").is_transport_error


# --- context manager ------------------------------------------------------


def test_atlas_client_is_a_context_manager() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with _build_client(handler) as client:
        assert client.request("GET", "/orgs") == {}

    # After exit, internal client is closed; calling it again would fail, but
    # we only assert that exit doesn't raise.


def test_config_property_returns_original_config() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    cfg = _fake_config("https://atlas.other/api/atlas/v2")
    client = AtlasClient(cfg, transport=httpx.MockTransport(handler))
    try:
        assert client.config is cfg
        assert client.config.base_url == "https://atlas.other/api/atlas/v2"
    finally:
        client.close()
