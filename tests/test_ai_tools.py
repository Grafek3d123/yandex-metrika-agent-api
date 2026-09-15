"""Тесты AI Tool Layer: схемы, конверты, needs_input, ошибки."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from yandex_metrika_agent.ai_tools import MetrikaTools
from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.errors import ValidationError

COUNTERS_URL = "https://api-metrika.yandex.net/management/v1/counters"
DATA_URL = "https://api-metrika.yandex.net/stat/v1/data"
GOAL77_URL = "https://api-metrika.yandex.net/management/v1/counter/44147844/goal/77"

RAW_COUNTER = {
    "id": 44147844,
    "name": "Мой сайт",
    "site2": {"site": "https://example.com"},
    "owner_login": "vasya",
    "permission": "rw",
}


def _tools() -> MetrikaTools:
    return MetrikaTools(
        MetrikaClient(token="test-token", base_url="https://api-metrika.yandex.net")
    )


# --- Реестр и схемы -----------------------------------------------------------


def test_14_tools_registered() -> None:
    tools = _tools()
    assert len(tools.specs()) == 14


def test_schemas_are_strict() -> None:
    tools = _tools()
    for spec in tools.specs():
        schema = spec["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_unknown_tool_rejected() -> None:
    tools = _tools()

    async def check() -> None:
        with pytest.raises(ValidationError):
            await tools.call("metrika_hack_the_planet", {})

    import asyncio

    asyncio.run(check())


# --- Счётчики -----------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_list_counters_envelope() -> None:
    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(200, json={"rows": 1, "counters": [RAW_COUNTER]})
    )
    tools = _tools()
    try:
        answer = await tools.call("metrika_list_counters", {})
        assert answer["status"] == "ok"
        assert answer["data"]["count"] == 1
        assert answer["data"]["counters"][0]["domain"] == "example.com"
    finally:
        await tools.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_get_counter_by_domain() -> None:
    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(200, json={"rows": 1, "counters": [RAW_COUNTER]})
    )
    tools = _tools()
    try:
        answer = await tools.call("metrika_get_counter", {"counter": "example.com"})
        assert answer["status"] == "ok"
        assert answer["data"]["id"] == 44147844
    finally:
        await tools.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_get_counter_multiple_candidates_needs_input() -> None:
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
    tools = _tools()
    try:
        answer = await tools.call("metrika_get_counter", {"counter": "магазин"})
        assert answer["status"] == "needs_input"
        assert answer["missing"] == ["counter"]
        assert len(answer["candidates"]) == 2
    finally:
        await tools.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_get_counter_not_found_is_error() -> None:
    respx.get(COUNTERS_URL).mock(return_value=httpx.Response(200, json={"rows": 0, "counters": []}))
    tools = _tools()
    try:
        answer = await tools.call("metrika_get_counter", {"counter": "ghost.example"})
        assert answer["status"] == "error"
        assert answer["error"]["error"] == "NotFoundError"
    finally:
        await tools.aclose()


# --- Цели ---------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_create_goal_from_description_ready() -> None:
    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(200, json={"rows": 1, "counters": [RAW_COUNTER]})
    )
    respx.get("https://api-metrika.yandex.net/management/v1/counter/44147844/goals").mock(
        return_value=httpx.Response(200, json={"goals": []})
    )
    respx.post("https://api-metrika.yandex.net/management/v1/counter/44147844/goals").mock(
        return_value=httpx.Response(
            200,
            json={
                "goal": {
                    "id": 77,
                    "name": "thank-you",
                    "type": "url",
                    "conditions": [{"type": "contain", "url": "/thank-you"}],
                }
            },
        )
    )
    tools = _tools()
    try:
        answer = await tools.call(
            "metrika_create_goal",
            {"counter": "example.com", "description": "цель при попадании на /thank-you"},
        )
        assert answer["status"] == "ok"
        assert answer["data"]["goal"]["id"] == 77
    finally:
        await tools.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_create_goal_missing_value_needs_input() -> None:
    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(200, json={"rows": 1, "counters": [RAW_COUNTER]})
    )
    tools = _tools()
    try:
        answer = await tools.call(
            "metrika_create_goal",
            {"counter": "example.com", "description": "цель на отправку формы"},
        )
        assert answer["status"] == "needs_input"
        assert answer["missing"] == ["event"]
        assert answer["question"]
    finally:
        await tools.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_create_goal_without_description_or_type_rejected() -> None:
    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(200, json={"rows": 1, "counters": [RAW_COUNTER]})
    )
    tools = _tools()
    try:
        answer = await tools.call("metrika_create_goal", {"counter": "example.com"})
        assert answer["status"] == "error"
    finally:
        await tools.aclose()


# --- Аналитика ----------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_get_traffic_tool() -> None:
    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(200, json={"rows": 1, "counters": [RAW_COUNTER]})
    )
    respx.get(DATA_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "query": {},
                "data": [],
                "totals": [100, 80, 300, 25.5, 120, 10],
                "metric_names": [
                    "ym:s:visits",
                    "ym:s:users",
                    "ym:s:pageviews",
                    "ym:s:bounceRate",
                    "ym:s:avgVisitDurationSeconds",
                    "ym:s:newUsers",
                ],
                "dimension_names": [],
            },
        )
    )
    tools = _tools()
    try:
        answer = await tools.call(
            "metrika_get_traffic",
            {"counter": "example.com", "date_from": "2026-09-01", "date_to": "2026-09-07"},
        )
        assert answer["status"] == "ok"
        assert answer["data"]["visits"] == 100
    finally:
        await tools.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_get_report_tool_invalid_metric_is_error() -> None:
    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(200, json={"rows": 1, "counters": [RAW_COUNTER]})
    )
    tools = _tools()
    try:
        answer = await tools.call(
            "metrika_get_report", {"counter": "example.com", "metrics": ["nope"]}
        )
        assert answer["status"] == "error"
        assert answer["error"]["error"] == "ValidationError"
    finally:
        await tools.aclose()


# --- Regression: is_favorite в metrika_update_goal ----------------------------
#
# Фактический API отвергает read-only поле is_favorite в PUT (invalid_json,
# path: goal.is_favorite), Goal.to_request его вырезает. Инструмент update не
# должен принимать is_favorite вообще: иначе он вернул бы ложный status="ok",
# ничего не изменив.


def _goal77_response() -> dict[str, object]:
    """Ответ GET цели: API возвращает read-only is_favorite в ответе чтения."""

    return {
        "goal": {
            "id": 77,
            "name": "Старое имя",
            "type": "url",
            "conditions": [{"type": "contain", "url": "/thank-you"}],
            "is_favorite": True,
            "status": "active",
        }
    }


def test_update_goal_schema_has_no_is_favorite() -> None:
    tools = _tools()
    schema = tools.registry.get("metrika_update_goal").input_schema
    assert "is_favorite" not in schema["properties"]


@pytest.mark.asyncio()
@respx.mock
async def test_update_goal_is_favorite_only_is_error_no_put() -> None:
    """Одиночный is_favorite игнорируется: PUT не отправляется, ответ error."""

    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(200, json={"rows": 1, "counters": [RAW_COUNTER]})
    )
    respx.get(GOAL77_URL).mock(return_value=httpx.Response(200, json=_goal77_response()))
    put_route = respx.put(GOAL77_URL).mock(
        return_value=httpx.Response(200, json=_goal77_response())
    )
    tools = _tools()
    try:
        answer = await tools.call(
            "metrika_update_goal",
            {"counter": "example.com", "goal_id": 77, "is_favorite": True},
        )
        assert answer["status"] == "error"
        assert answer["error"]["error"] == "ValidationError"
        assert put_route.call_count == 0
    finally:
        await tools.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_update_goal_name_price_works_without_is_favorite() -> None:
    """name/price применяются; тело PUT не содержит is_favorite даже из GET."""

    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(200, json={"rows": 1, "counters": [RAW_COUNTER]})
    )
    respx.get(GOAL77_URL).mock(return_value=httpx.Response(200, json=_goal77_response()))
    put_route = respx.put(GOAL77_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "goal": {
                    "id": 77,
                    "name": "Новое имя",
                    "type": "url",
                    "conditions": [{"type": "contain", "url": "/thank-you"}],
                    "default_price": 100,
                }
            },
        )
    )
    tools = _tools()
    try:
        answer = await tools.call(
            "metrika_update_goal",
            {"counter": "example.com", "goal_id": 77, "name": "Новое имя", "price": 100},
        )
        assert answer["status"] == "ok"
        assert put_route.call_count == 1
        sent = json.loads(put_route.calls.last.request.content.decode("utf-8"))
        goal_payload = sent["goal"]
        assert "is_favorite" not in goal_payload
        assert goal_payload["name"] == "Новое имя"
        assert goal_payload["default_price"] == 100
    finally:
        await tools.aclose()
