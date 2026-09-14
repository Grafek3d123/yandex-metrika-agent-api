"""Тесты CounterService на мок-ответах Management API (respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from yandex_metrika_agent.counters import CounterService, MetrikaCounter, normalize_host
from yandex_metrika_agent.errors import NotFoundError, ValidationError

COUNTERS_URL = "https://api-metrika.yandex.net/management/v1/counters"

RAW_COUNTER = {
    "id": 44147844,
    "name": "Мой сайт",
    "site2": {"site": "https://example.com"},
    "status": "Active",
    "owner_login": "vasya",
    "permission": "rw",
    "time_zone_name": "Europe/Moscow",
    "type": "simple",
    "favorite": False,
    "create_time": "2024-01-01T00:00:00",
}


def test_normalize_host() -> None:
    assert normalize_host("https://www.Example.com/path?q=1") == "example.com"
    assert normalize_host("WWW.EXAMPLE.COM.") == "example.com"
    assert normalize_host("") is None


def test_metrika_counter_from_site2() -> None:
    counter = MetrikaCounter.from_api(RAW_COUNTER)
    assert counter.site == "https://example.com"
    assert counter.domain == "example.com"
    assert counter.permission == "rw"
    assert counter.title == "Мой сайт"


def test_metrika_counter_fallback_site_and_mirrors() -> None:
    counter = MetrikaCounter.from_api({"id": 1, "site": "example.org"})
    assert counter.site == "example.org"
    mirror = MetrikaCounter.from_api(
        {
            "id": 2,
            "mirrors2": [{"site": "a.com", "is_main": False}, {"site": "b.com", "is_main": True}],
        }
    )
    assert mirror.site == "b.com"


@pytest.mark.asyncio()
@respx.mock
async def test_list_counters_new_format(client: object) -> None:
    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(200, json={"rows": 1, "counters": [RAW_COUNTER]})
    )
    service = CounterService(client)  # type: ignore[arg-type]
    counters = await service.list()
    assert len(counters) == 1
    assert counters[0].id == 44147844


@pytest.mark.asyncio()
@respx.mock
async def test_list_counters_legacy_content(client: object) -> None:
    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(
            200, json={"content": [{"counter": {**RAW_COUNTER, "id": 2}}]}
        )
    )
    service = CounterService(client)  # type: ignore[arg-type]
    counters = await service.list()
    assert [c.id for c in counters] == [2]


@pytest.mark.asyncio()
@respx.mock
async def test_get_counter(client: object) -> None:
    respx.get("https://api-metrika.yandex.net/management/v1/counter/44147844").mock(
        return_value=httpx.Response(200, json={"counter": RAW_COUNTER})
    )
    service = CounterService(client)  # type: ignore[arg-type]
    counter = await service.get(44147844)
    assert counter.owner_login == "vasya"


@pytest.mark.asyncio()
@respx.mock
async def test_get_counter_invalid_id(client: object) -> None:
    service = CounterService(client)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        await service.get(-1)


@pytest.mark.asyncio()
@respx.mock
async def test_resolve_one_exact_domain(client: object) -> None:
    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "rows": 2,
                "counters": [
                    RAW_COUNTER,
                    {**RAW_COUNTER, "id": 999, "name": "Другой", "site2": {"site": "other.org"}},
                ],
            },
        )
    )
    service = CounterService(client)  # type: ignore[arg-type]
    counter = await service.resolve_one("example.com")
    assert counter.id == 44147844


@pytest.mark.asyncio()
@respx.mock
async def test_resolve_one_not_found(client: object) -> None:
    respx.get(COUNTERS_URL).mock(return_value=httpx.Response(200, json={"rows": 0, "counters": []}))
    service = CounterService(client)  # type: ignore[arg-type]
    with pytest.raises(NotFoundError):
        await service.resolve_one("ghost.example")


@pytest.mark.asyncio()
@respx.mock
async def test_resolve_one_multiple_candidates(client: object) -> None:
    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "rows": 2,
                "counters": [
                    {**RAW_COUNTER, "id": 1, "name": "Магазин"},
                    {**RAW_COUNTER, "id": 2, "name": "Магазин", "site2": {"site": "shop2.org"}},
                ],
            },
        )
    )
    service = CounterService(client)  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as exc:
        await service.resolve_one("магазин")
    assert exc.value.details.get("candidates")


@pytest.mark.asyncio()
@respx.mock
async def test_resolve_by_numeric_id(client: object) -> None:
    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(200, json={"rows": 1, "counters": [RAW_COUNTER]})
    )
    service = CounterService(client)  # type: ignore[arg-type]
    matched = await service.resolve("44147844")
    assert len(matched) == 1
    assert matched[0].id == 44147844
