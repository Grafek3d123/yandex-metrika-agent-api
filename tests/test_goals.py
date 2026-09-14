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
