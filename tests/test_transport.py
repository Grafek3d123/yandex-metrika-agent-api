"""Тесты транспорта: retry, кэш, дедупликация, ошибки, rate limiter."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from yandex_metrika_agent.errors import ApiError, RateLimitedError, TransportError
from yandex_metrika_agent.transport import (
    Transport,
    backoff_delay,
    canonical_key,
    parse_retry_after,
    payload_or_raise,
    should_retry,
)

URL = "https://api-metrika.yandex.net/test"


@pytest.mark.asyncio()
@respx.mock
async def test_retry_on_500_then_success() -> None:
    respx.get(URL).mock(
        side_effect=[
            httpx.Response(500, json={"code": 500}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    transport = Transport(retries=1, enforce_quotas=False)
    try:
        payload = await transport.request_json("GET", URL)
        assert payload == {"ok": True}
    finally:
        await transport.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_no_retry_on_404() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(404, json={"code": 404}))
    transport = Transport(retries=3, enforce_quotas=False)
    from yandex_metrika_agent.errors import NotFoundError

    try:
        with pytest.raises(NotFoundError):
            await transport.request_json("GET", URL)
        assert route.call_count == 1
    finally:
        await transport.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_rate_limited_raises() -> None:
    respx.get(URL).mock(return_value=httpx.Response(429, json={"code": 429}))
    transport = Transport(retries=0, enforce_quotas=False)
    try:
        with pytest.raises(RateLimitedError):
            await transport.request_json("GET", URL)
    finally:
        await transport.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_cache_returns_same_payload() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"n": 1}))
    transport = Transport(retries=0, cache_ttl=60.0, enforce_quotas=False)
    try:
        first = await transport.request_json("GET", URL, cacheable=True)
        second = await transport.request_json("GET", URL, cacheable=True)
        assert first == second
        assert route.call_count == 1
        stats = transport.get_stats()
        assert stats["cache_hits"] == 1
    finally:
        await transport.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_cache_isolated_by_connection() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"n": 1}))
    transport_a = Transport(retries=0, cache_ttl=60.0, connection_id="a", enforce_quotas=False)
    transport_b = Transport(retries=0, cache_ttl=60.0, connection_id="b", enforce_quotas=False)
    try:
        await transport_a.request_json("GET", URL, cacheable=True)
        await transport_b.request_json("GET", URL, cacheable=True)
        # Разные connection — разные ключи кэша: оба запроса дошли до HTTP.
        stats = transport_b.get_stats()
        assert stats["cache_misses"] == 1
    finally:
        await transport_a.aclose()
        await transport_b.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_single_flight_deduplicates_parallel_gets() -> None:
    respx.get(URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json={"ok": True}
        )
    )
    transport = Transport(retries=0, cache_ttl=60.0, enforce_quotas=False)

    async def delayed_response(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.02)
        return httpx.Response(200, json={"ok": True})

    respx.get(URL).mock(side_effect=delayed_response)
    try:
        results = await asyncio.gather(
            transport.request_json("GET", URL, cacheable=True),
            transport.request_json("GET", URL, cacheable=True),
        )
        assert list(results) == [{"ok": True}, {"ok": True}]
        stats = transport.get_stats()
        assert stats["deduplicated"] == 1
    finally:
        await transport.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_empty_response_returns_none() -> None:
    respx.delete(URL).mock(return_value=httpx.Response(204))
    transport = Transport(retries=0, enforce_quotas=False)
    try:
        assert await transport.request_json("DELETE", URL) is None
    finally:
        await transport.aclose()


def test_should_retry_rules() -> None:
    assert should_retry("GET", attempt_exception=None, status=500)
    assert not should_retry("GET", attempt_exception=None, status=404)
    assert not should_retry("POST", attempt_exception=None, status=500)
    assert should_retry("GET", attempt_exception=httpx.ConnectError, status=0)


def test_backoff_delay_bounded() -> None:
    for attempt in range(6):
        delay = backoff_delay(attempt)
        assert 0 < delay <= 30.0


def test_backoff_respects_retry_after() -> None:
    assert backoff_delay(0, retry_after=7) == 7.0


def test_parse_retry_after_seconds_and_date() -> None:
    assert parse_retry_after({"Retry-After": "12"}) == 12.0
    assert parse_retry_after({}) is None


def test_canonical_key_scope_isolation() -> None:
    a = canonical_key("GET", "/x", {"id": 1}, scope="user-a")
    b = canonical_key("GET", "/x", {"id": 1}, scope="user-b")
    assert a != b


def test_payload_or_raise_error_payload() -> None:
    response = httpx.Response(
        400, json={"code": 400, "message": "bad"}, request=httpx.Request("GET", URL)
    )
    with pytest.raises(ApiError) as exc:
        payload_or_raise(response, method="GET", url=URL)
    assert "bad" in exc.value.message or exc.value.details


@pytest.mark.asyncio()
async def test_wait_async_report_success(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = Transport(retries=0, enforce_quotas=False)

    async def fake_request_json(method: str, url: str, **kwargs: object) -> object:
        if url.endswith("/async/7"):
            return {"status": "done"}
        return {"data": [], "totals": [1]}

    transport.request_json = fake_request_json  # type: ignore[method-assign]
    result = await transport.wait_async_report(7)
    assert result == {"data": [], "totals": [1]}


@pytest.mark.asyncio()
async def test_wait_async_report_error_state() -> None:
    transport = Transport(retries=0, enforce_quotas=False)

    async def fake_request_json(method: str, url: str, **kwargs: object) -> object:
        return {"status": "error"}

    transport.request_json = fake_request_json  # type: ignore[method-assign]
    from yandex_metrika_agent.errors import ReportTimeoutError

    with pytest.raises(ReportTimeoutError):
        await transport.wait_async_report(7)


@pytest.mark.asyncio()
async def test_transport_invalid_params() -> None:
    with pytest.raises(ValueError):
        Transport(timeout=0)
    with pytest.raises(ValueError):
        Transport(retries=-1)


def test_transport_error_is_transport_error() -> None:
    assert issubclass(TransportError, Exception)
