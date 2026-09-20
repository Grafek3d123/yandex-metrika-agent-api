"""Тесты GoalService на мок-ответах Management API (respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from yandex_metrika_agent.errors import ValidationError
from yandex_metrika_agent.goals import GoalService, action_goal, url_goal
from yandex_metrika_agent.models import Goal

BASE = "https://api-metrika.yandex.net/management/v1/counter/441"

GOAL_JSON = {
    "id": 100,
    "name": "Отправка формы",
    "type": "action",
    "conditions": [{"type": "exact", "url": "submitForm"}],
}


def _mock_goals(payload: object) -> None:
    respx.get(f"{BASE}/goals").mock(return_value=httpx.Response(200, json=payload))


@pytest.mark.asyncio()
@respx.mock
async def test_list_goals(client: object) -> None:
    _mock_goals({"goals": [GOAL_JSON]})
    goals = await GoalService(client).list(441)  # type: ignore[arg-type]
    assert len(goals) == 1
    assert goals[0].id == 100
    assert goals[0].type == "action"


@pytest.mark.asyncio()
@respx.mock
async def test_get_goal(client: object) -> None:
    respx.get(f"{BASE}/goal/100").mock(
        return_value=httpx.Response(200, json={"goal": GOAL_JSON})
    )
    goal = await GoalService(client).get(441, 100)  # type: ignore[arg-type]
    assert goal.name == "Отправка формы"


@pytest.mark.asyncio()
@respx.mock
async def test_get_goal_missing(client: object) -> None:
    respx.get(f"{BASE}/goal/404").mock(
        return_value=httpx.Response(404, json={"errors": [{"code": "NOT_FOUND"}]})
    )
    with pytest.raises(Exception):  # noqa: B017 - ApiError/NotFoundError из error_from_response
        await GoalService(client).get(441, 404)  # type: ignore[arg-type]


@pytest.mark.asyncio()
@respx.mock
async def test_create_goal_wraps_payload(client: object) -> None:
    route = respx.post(f"{BASE}/goals").mock(
        return_value=httpx.Response(200, json={"goal": {**GOAL_JSON, "id": 101}})
    )
    goal = action_goal(name="Отправка формы", event="submitForm")
    created = await GoalService(client).create(441, goal)  # type: ignore[arg-type]
    assert created.id == 101
    body = route.calls[0].request.content.decode()
    assert '"goal"' in body


@pytest.mark.asyncio()
@respx.mock
async def test_ensure_goal_skips_exact_duplicate(client: object) -> None:
    _mock_goals({"goals": [GOAL_JSON]})
    result = await GoalService(client).ensure_goal(  # type: ignore[arg-type]
        441, action_goal(name="Отправка формы", event="submitForm")
    )
    assert result.created is False
    assert result.goal.id == 100


@pytest.mark.asyncio()
@respx.mock
async def test_ensure_goal_creates_new(client: object) -> None:
    _mock_goals({"goals": []})
    respx.post(f"{BASE}/goals").mock(
        return_value=httpx.Response(200, json={"goal": {**GOAL_JSON, "id": 102}})
    )
    result = await GoalService(client).ensure_goal(  # type: ignore[arg-type]
        441, url_goal(name="Страница спасибо", url="/thank-you")
    )
    assert result.created is True
    assert result.goal.id == 102


@pytest.mark.asyncio()
@respx.mock
async def test_ensure_goal_same_name_different_conditions_warns(client: object) -> None:
    _mock_goals({"goals": [GOAL_JSON]})
    respx.post(f"{BASE}/goals").mock(
        return_value=httpx.Response(200, json={"goal": {**GOAL_JSON, "id": 103}})
    )
    result = await GoalService(client).ensure_goal(  # type: ignore[arg-type]
        441, action_goal(name="Отправка формы", event="otherEvent")
    )
    assert result.created is True
    assert result.warnings, "должно быть предупреждение о совпадении имени"


@pytest.mark.asyncio()
@respx.mock
async def test_update_goal_requires_id(client: object) -> None:
    goal = action_goal(name="x", event="y")  # без id
    with pytest.raises(ValidationError):
        await GoalService(client).update(441, goal)  # type: ignore[arg-type]


@pytest.mark.asyncio()
@respx.mock
async def test_update_goal(client: object) -> None:
    respx.put(f"{BASE}/goal/100").mock(
        return_value=httpx.Response(200, json={"goal": {**GOAL_JSON, "name": "Новое имя"}})
    )
    goal = Goal.model_validate(GOAL_JSON).model_copy(update={"name": "Новое имя"})
    updated = await GoalService(client).update(441, goal)  # type: ignore[arg-type]
    assert updated.name == "Новое имя"


@pytest.mark.asyncio()
@respx.mock
async def test_delete_goal(client: object) -> None:
    respx.delete(f"{BASE}/goal/100").mock(return_value=httpx.Response(204))
    payload = await GoalService(client).delete(441, 100)  # type: ignore[arg-type]
    assert payload == {}


def test_signature_distinguishes_conditions() -> None:
    a = action_goal(name="A", event="submitForm")
    b = action_goal(name="A", event="submitForm")
    c = action_goal(name="A", event="other")
    assert a.signature() == b.signature()
    assert a.signature() != c.signature()


# --- Устойчивое чтение автоцелей Метрики (реальный аккаунт вскрыл баг) --------

# Типы автоцелей, которые Метрика создаёт сама (goal_source="auto") и которых
# нет в нашем перечислении создаваемых типов. Встречены в реальном аккаунте.
AUTO_GOAL_JSON = {
    "id": 545418480,
    "name": "Автоцель: заполнил контактные данные",
    "type": "contact_data",
    "default_price": 0.0,
    "goal_source": "auto",
    "status": "Active",
}


def test_goal_model_reads_unknown_type() -> None:
    """Модель читает цель неизвестного типа, не падая (read-tolerant)."""

    goal = Goal.model_validate(AUTO_GOAL_JSON)
    assert goal.type == "contact_data"
    assert goal.id == 545418480
    # describe/title не падают на неизвестном типе.
    assert "contact_data" in goal.describe()


@pytest.mark.asyncio()
@respx.mock
async def test_list_goals_tolerates_auto_types(client: object) -> None:
    """Список целей с автоцелью неизвестного типа читается целиком."""

    _mock_goals({"goals": [GOAL_JSON, AUTO_GOAL_JSON]})
    goals = await GoalService(client).list(441)  # type: ignore[arg-type]
    assert len(goals) == 2
    assert {g.type for g in goals} == {"action", "contact_data"}


@pytest.mark.asyncio()
@respx.mock
async def test_create_rejects_unknown_type(client: object) -> None:
    """Создать цель неизвестного типа нельзя (защита от выдуманного типа)."""

    goal = Goal.model_validate({"name": "Странная", "type": "contact_data"})
    with pytest.raises(ValidationError):
        await GoalService(client).create(441, goal)  # type: ignore[arg-type]


# Телефонная цель в списке API приходит без conditions (сокращённый объект).
PHONE_GOAL_LIST_JSON = {
    "id": 585281057,
    "name": "Клик по телефону",
    "type": "phone",
    "hide_phone_number": False,
    "status": "Active",
}


def test_goal_model_reads_phone_without_conditions() -> None:
    """Список целей отдаёт phone-цель без conditions — модель читает её."""

    goal = Goal.model_validate(PHONE_GOAL_LIST_JSON)
    assert goal.type == "phone"
    assert goal.conditions is None


@pytest.mark.asyncio()
@respx.mock
async def test_create_phone_without_conditions_rejected(client: object) -> None:
    """При создании phone-цель без conditions отклоняется (инвариант записи)."""

    goal = Goal.model_validate(PHONE_GOAL_LIST_JSON)  # id не помешает, create его игнорирует
    with pytest.raises(ValidationError):
        await GoalService(client).create(441, goal)  # type: ignore[arg-type]


# Условие автоцели соцсети: тип all_social отсутствует в CONDITION_TYPES.
SOCIAL_AUTO_GOAL_JSON = {
    "id": 600000001,
    "name": "Автоцель: переход в соцсеть",
    "type": "social",
    "conditions": [{"type": "all_social"}],
    "status": "Active",
}


def test_goal_condition_reads_unknown_type() -> None:
    """Модель читает условие неизвестного типа (all_social), не падая."""

    goal = Goal.model_validate(SOCIAL_AUTO_GOAL_JSON)
    assert goal.conditions is not None
    assert goal.conditions[0].type == "all_social"


@pytest.mark.asyncio()
@respx.mock
async def test_list_goals_tolerates_social_auto_goal(client: object) -> None:
    """Список целей с автоцелью all_social читается целиком."""

    _mock_goals({"goals": [GOAL_JSON, SOCIAL_AUTO_GOAL_JSON]})
    goals = await GoalService(client).list(441)  # type: ignore[arg-type]
    assert len(goals) == 2


@pytest.mark.asyncio()
@respx.mock
async def test_create_rejects_unknown_condition_type(client: object) -> None:
    """Создать цель с условием неизвестного типа нельзя (защита на записи)."""

    goal = Goal.model_validate({"name": "Соцсеть", "type": "social", "conditions": [{"type": "all_social"}]})
    with pytest.raises(ValidationError):
        await GoalService(client).create(441, goal)  # type: ignore[arg-type]
