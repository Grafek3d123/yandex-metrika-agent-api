"""Destructive safety: confirmation guard для разрушающих AI-инструментов.

Гарантии, которые проверяются здесь (safety-контракт Task05):

* ``metrika_delete_goal`` без валидного подтверждения НЕ выполняет DELETE;
* первый вызов возвращает структурированный ``confirmation_required``;
* токен привязан к конкретной операции (connection_id / counter_id / goal_id);
* просроченный, повторный или «чужой» токен НЕ вызывает DELETE;
* корректный токен exact-операции вызывает ровно один DELETE;
* guard находится ниже handler'а: его нельзя обойти, передав ``confirmed=true``
  или любые другие аргументы;
* read-only и mutating-инструменты (create/update) под guard не попадают.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx

from yandex_metrika_agent.ai_tools import ALL_TOOLS, MetrikaTools
from yandex_metrika_agent.ai_tools.base import (
    ConfirmationPolicy,
    Tool,
    ToolContext,
    ToolRegistry,
    ToolSafety,
    strict_object,
)
from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.errors import ValidationError

BASE = "https://api-metrika.yandex.net"
COUNTERS_URL = f"{BASE}/management/v1/counters"
GOALS_A_URL = f"{BASE}/management/v1/counter/44147844/goals"
GOAL77_URL = f"{BASE}/management/v1/counter/44147844/goal/77"
GOAL99_URL = f"{BASE}/management/v1/counter/44147844/goal/99"
COUNTER55_GOAL77_URL = f"{BASE}/management/v1/counter/55555555/goal/77"

COUNTER_A = {
    "id": 44147844,
    "name": "Example",
    "site2": {"site": "https://example.com"},
    "permission": "rw",
}
COUNTER_B = {
    "id": 55555555,
    "name": "Other",
    "site2": {"site": "https://other.example"},
    "permission": "rw",
}


class Clock:
    """Детерминированные часы для тестов TTL (без реального сна)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _counters_json(*rows: dict[str, object]) -> dict[str, object]:
    return {"rows": len(rows), "counters": list(rows)}


def _tools(
    *, connection_id: str = "default", confirmations: ConfirmationPolicy | None = None
) -> MetrikaTools:
    client = MetrikaClient(token="test-token", base_url=BASE, connection_id=connection_id)
    return MetrikaTools(client, confirmations=confirmations)


def _delete_args(counter: object = "example.com", goal_id: int = 77) -> dict[str, object]:
    return {"counter": counter, "goal_id": goal_id}


def _with_token(args: dict[str, object], token: str) -> dict[str, object]:
    return {**args, "confirmation_token": token}


def _mock_read_only_routes() -> None:
    """Счётчики доступны для резолва; DELETE-ловушки добавляются отдельно."""

    respx.get(COUNTERS_URL).mock(
        return_value=httpx.Response(200, json=_counters_json(COUNTER_A, COUNTER_B))
    )


def _mock_delete(url: str) -> respx.Route:
    """Зарегистрировать DELETE-ловушку (считаем вызовы)."""

    return respx.delete(url).mock(return_value=httpx.Response(200, json={"success": True}))


def _approve(tools: MetrikaTools, first: dict[str, object]) -> str:
    """Хост подтверждает pending-операцию → одноразовый токен."""

    issued = tools.approve_confirmation(str(first["confirmation_id"]), approved_by="user")
    return str(issued["confirmation_token"])


# --- 1. delete_goal без confirmation → DELETE не вызывается -------------------


@pytest.mark.asyncio()
@respx.mock
async def test_delete_goal_without_confirmation_does_not_delete() -> None:
    _mock_read_only_routes()
    delete_route = _mock_delete(GOAL77_URL)
    tools = _tools()
    try:
        answer = await tools.call("metrika_delete_goal", _delete_args())
        assert answer["status"] == "confirmation_required"
        assert delete_route.call_count == 0
    finally:
        await tools.aclose()


# --- 2. первый вызов создаёт confirmation_required ----------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_first_call_returns_structured_confirmation_required() -> None:
    _mock_read_only_routes()
    _mock_delete(GOAL77_URL)
    tools = _tools()
    try:
        answer = await tools.call("metrika_delete_goal", _delete_args())
        assert answer["status"] == "confirmation_required"
        assert answer["confirmation_id"]
        assert answer["data"]["action"] == "metrika_delete_goal"
        assert answer["data"]["counter_id"] == 44147844
        assert answer["data"]["goal_id"] == 77
        assert answer["data"]["reversible"] is False
        # pending-операция видна хосту
        assert tools.pending_confirmations() == [answer["confirmation_id"]]
    finally:
        await tools.aclose()


# --- 3. confirmation другого goal → DELETE не вызывается ----------------------


@pytest.mark.asyncio()
@respx.mock
async def test_confirmation_for_other_goal_does_not_delete() -> None:
    _mock_read_only_routes()
    delete77 = _mock_delete(GOAL77_URL)
    delete99 = _mock_delete(GOAL99_URL)
    tools = _tools()
    try:
        first = await tools.call("metrika_delete_goal", _delete_args(goal_id=77))
        token = _approve(tools, first)
        # Пробуем применить токен от цели 77 к цели 99.
        answer = await tools.call(
            "metrika_delete_goal", _with_token(_delete_args(goal_id=99), token)
        )
        assert answer["status"] == "confirmation_required"
        assert answer["reason"] == "mismatch"
        assert delete77.call_count == 0
        assert delete99.call_count == 0
    finally:
        await tools.aclose()


# --- 4. confirmation другого counter → DELETE не вызывается -------------------


@pytest.mark.asyncio()
@respx.mock
async def test_confirmation_for_other_counter_does_not_delete() -> None:
    _mock_read_only_routes()
    delete_a = _mock_delete(GOAL77_URL)
    delete_b = _mock_delete(COUNTER55_GOAL77_URL)
    tools = _tools()
    try:
        first = await tools.call("metrika_delete_goal", _delete_args(counter="example.com"))
        token = _approve(tools, first)
        # Токен от счётчика 44147844 применяем к счётчику 55555555.
        answer = await tools.call(
            "metrika_delete_goal", _with_token(_delete_args(counter="other.example"), token)
        )
        assert answer["status"] == "confirmation_required"
        assert answer["reason"] == "mismatch"
        assert delete_a.call_count == 0
        assert delete_b.call_count == 0
    finally:
        await tools.aclose()


# --- 5. confirmation другого connection → DELETE не вызывается ----------------


@pytest.mark.asyncio()
@respx.mock
async def test_confirmation_for_other_connection_does_not_delete() -> None:
    _mock_read_only_routes()
    delete_route = _mock_delete(GOAL77_URL)
    tools = _tools(connection_id="default")
    other_client = MetrikaClient(token="test-token", base_url=BASE, connection_id="other")
    other_context = ToolContext(other_client)
    try:
        first = await tools.call("metrika_delete_goal", _delete_args())
        token = _approve(tools, first)
        # Тот же токен, но подключение другое (connection_id входит в отпечаток).
        answer = await tools.registry.call(
            other_context,
            "metrika_delete_goal",
            _with_token(_delete_args(), token),
        )
        assert answer["status"] == "confirmation_required"
        assert answer["reason"] == "mismatch"
        assert delete_route.call_count == 0
    finally:
        await tools.aclose()
        await other_client.transport.aclose()


# --- 6. просроченный confirmation → DELETE не вызывается ----------------------


@pytest.mark.asyncio()
@respx.mock
async def test_expired_confirmation_does_not_delete() -> None:
    _mock_read_only_routes()
    delete_route = _mock_delete(GOAL77_URL)
    clock = Clock()
    tools = _tools(confirmations=ConfirmationPolicy(ttl_seconds=60, clock=clock))
    try:
        first = await tools.call("metrika_delete_goal", _delete_args())
        token = _approve(tools, first)
        clock.advance(61)  # TTL истёк
        answer = await tools.call("metrika_delete_goal", _with_token(_delete_args(), token))
        assert answer["status"] == "confirmation_required"
        assert answer["reason"] == "expired"
        assert delete_route.call_count == 0
    finally:
        await tools.aclose()


# --- 7. повторное использование confirmation → DELETE не вызывается -----------


@pytest.mark.asyncio()
@respx.mock
async def test_reused_confirmation_does_not_delete_twice() -> None:
    _mock_read_only_routes()
    delete_route = _mock_delete(GOAL77_URL)
    tools = _tools()
    try:
        first = await tools.call("metrika_delete_goal", _delete_args())
        token = _approve(tools, first)
        ok = await tools.call("metrika_delete_goal", _with_token(_delete_args(), token))
        assert ok["status"] == "ok"
        assert delete_route.call_count == 1
        # Повторное применение того же токена — отказ, повторного DELETE нет.
        reuse = await tools.call("metrika_delete_goal", _with_token(_delete_args(), token))
        assert reuse["status"] == "error"
        assert reuse["error"]["details"]["reason"] == "reused"
        assert delete_route.call_count == 1
    finally:
        await tools.aclose()


# --- 8. корректное подтверждение exact-операции → ровно один DELETE -----------


@pytest.mark.asyncio()
@respx.mock
async def test_correct_confirmation_executes_exactly_one_delete() -> None:
    _mock_read_only_routes()
    delete_route = _mock_delete(GOAL77_URL)
    tools = _tools()
    try:
        first = await tools.call("metrika_delete_goal", _delete_args())
        token = _approve(tools, first)
        answer = await tools.call("metrika_delete_goal", _with_token(_delete_args(), token))
        assert answer["status"] == "ok"
        assert answer["data"]["deleted"] is True
        assert delete_route.call_count == 1
        # После успешного выполнения pending больше нет.
        assert tools.pending_confirmations() == []
    finally:
        await tools.aclose()


# --- 9. read-only инструменты не требуют confirmation ------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_read_only_tools_need_no_confirmation() -> None:
    _mock_read_only_routes()
    respx.get(GOALS_A_URL).mock(return_value=httpx.Response(200, json={"goals": []}))
    tools = _tools()
    try:
        answer = await tools.call("metrika_list_goals", {"counter": "example.com"})
        assert answer["status"] == "ok"
        assert "confirmation_id" not in answer
        assert tools.pending_confirmations() == []
    finally:
        await tools.aclose()


# --- 10. create/update (MUTATING) не попадают под destructive guard -----------


@pytest.mark.asyncio()
@respx.mock
async def test_mutating_tools_are_not_gated_as_destructive() -> None:
    _mock_read_only_routes()
    updated = {
        "goal": {
            "id": 77,
            "name": "Новое",
            "type": "url",
            "conditions": [{"type": "contain", "url": "/thank-you"}],
        }
    }
    respx.get(GOAL77_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "goal": {
                    "id": 77,
                    "name": "Старое",
                    "type": "url",
                    "conditions": [{"type": "contain", "url": "/thank-you"}],
                }
            },
        )
    )
    respx.put(GOAL77_URL).mock(return_value=httpx.Response(200, json=updated))
    tools = _tools()
    try:
        answer = await tools.call(
            "metrika_update_goal", {"counter": "example.com", "goal_id": 77, "name": "Новое"}
        )
        assert answer["status"] == "ok"
        assert answer["status"] != "confirmation_required"
        assert tools.pending_confirmations() == []
    finally:
        await tools.aclose()


# --- Guard нельзя обойти через confirmed=true (требование #3) -----------------


@pytest.mark.asyncio()
@respx.mock
async def test_confirmed_true_argument_does_not_bypass_guard() -> None:
    _mock_read_only_routes()
    delete_route = _mock_delete(GOAL77_URL)
    tools = _tools()
    try:
        # Агент пытается сам подтвердить, передав confirmed=true.
        answer = await tools.call(
            "metrika_delete_goal", {**_delete_args(), "confirmed": True, "approve": True}
        )
        assert answer["status"] == "confirmation_required"
        assert delete_route.call_count == 0
    finally:
        await tools.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_forged_confirmation_token_is_rejected() -> None:
    _mock_read_only_routes()
    delete_route = _mock_delete(GOAL77_URL)
    tools = _tools()
    try:
        # Выдуманный токен неизвестного значения — отказ, DELETE нет.
        forged = _with_token(_delete_args(), "deadbeef.cafe")
        answer = await tools.call("metrika_delete_goal", forged)
        assert answer["status"] in ("confirmation_required", "error")
        assert delete_route.call_count == 0
    finally:
        await tools.aclose()


# --- Инварианты реестра и классификации ---------------------------------------


def test_only_delete_goal_is_destructive() -> None:
    destructive = {t.name for t in ALL_TOOLS if t.safety is ToolSafety.DESTRUCTIVE}
    assert destructive == {"metrika_delete_goal"}


def test_mutating_tools_are_create_and_update_only() -> None:
    mutating = {t.name for t in ALL_TOOLS if t.safety is ToolSafety.MUTATING}
    assert mutating == {"metrika_create_goal", "metrika_update_goal"}


def test_tool_requires_explicit_safety() -> None:
    """Без safety конструирование Tool невозможно (нет значения по умолчанию)."""

    async def _noop(context: ToolContext, arguments: dict[str, Any]) -> None:
        return None

    with pytest.raises(TypeError):
        Tool(  # type: ignore[call-arg]  # намеренно без safety
            name="metrika_x",
            description="d",
            input_schema=strict_object({}, []),
            handler=_noop,
        )


def test_registry_rejects_destructive_without_policy() -> None:
    destructive_tool = next(t for t in ALL_TOOLS if t.safety is ToolSafety.DESTRUCTIVE)
    with pytest.raises(ValidationError):
        ToolRegistry([destructive_tool], confirmations=None)


def test_approve_confirmation_is_not_a_tool() -> None:
    """Метод подтверждения недоступен из AI Tool Layer (не в specs, не tool)."""

    tools = _tools()
    names = {spec["name"] for spec in tools.specs()}
    assert "approve_confirmation" not in names
    assert "metrika_approve_confirmation" not in names

    async def call_as_tool() -> None:
        args = {"confirmation_id": "x", "approved_by": "u"}
        with pytest.raises(ValidationError):
            await tools.call("approve_confirmation", args)

    asyncio.run(call_as_tool())


def test_approve_requires_actor() -> None:
    tools = _tools()
    with pytest.raises(ValidationError):
        tools.approve_confirmation("nonexistent", approved_by="   ")
