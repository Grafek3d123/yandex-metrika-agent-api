"""AI-инструменты по целям: CRUD и создание из описания.

``metrika_create_goal`` принимает либо ``description`` (фраза пользователя —
тип и значения извлечёт GoalPlanner), либо явный ``goal_type`` с параметрами.
Если существенного значения нет (например, имени JS-события для «цели на
отправку формы»), инструмент возвращает ``needs_input`` — выдумывать значение
запрещено. Создание идемпотентно: существующая такая же цель не дублируется.
"""

from __future__ import annotations

from typing import Any

from yandex_metrika_agent.ai_tools.base import (
    COUNTER_PARAM,
    Tool,
    ToolContext,
    ToolResult,
    strict_object,
)
from yandex_metrika_agent.errors import ValidationError
from yandex_metrika_agent.models import GOAL_TYPES

_LIST_SCHEMA: dict[str, Any] = strict_object({"counter": COUNTER_PARAM}, required=["counter"])

_GET_SCHEMA: dict[str, Any] = strict_object(
    {"counter": COUNTER_PARAM, "goal_id": {"type": "integer", "minimum": 1}},
    required=["counter", "goal_id"],
)

_CREATE_SCHEMA: dict[str, Any] = strict_object(
    {
        "counter": COUNTER_PARAM,
        "description": {
            "type": "string",
            "description": "Описание цели человеческим языком (например, визит на /thank-you).",
        },
        "goal_type": {
            "type": "string",
            "enum": sorted(GOAL_TYPES),
            "description": "Тип цели, если уже определён.",
        },
        "name": {"type": "string", "description": "Название цели."},
        "event": {"type": "string", "description": "Имя JS-события (action)."},
        "url": {"type": "string", "description": "URL/путь страницы (url)."},
        "phone": {"type": "string", "description": "Номер телефона (phone)."},
        "email": {"type": "string", "description": "Email-адрес (email)."},
        "filename": {"type": "string", "description": "Имя/расширение файла (file)."},
        "platform": {"type": "string", "description": "Платформа мессенджера/чата."},
        "param": {"type": "string", "description": "Параметр поиска (search)."},
        "network": {"type": "string", "description": "Соцсеть (social)."},
        "depth": {"type": "integer", "minimum": 2, "description": "Глубина просмотров (number)."},
        "seconds": {
            "type": "integer",
            "minimum": 1,
            "description": "Секунды визита (visit_duration).",
        },
        "price": {"type": "number", "minimum": 0, "description": "Цена цели (необязательно)."},
    },
    required=["counter"],
)

# is_favorite в схему не включаем: фактический API отвергает это поле в PUT
# (invalid_json, path: goal.is_favorite), а Goal.to_request его вырезает.
# Принимать параметр, который невозможно применить, — значит возвращать ложный
# status="ok"; поэтому он здесь просто отсутствует.
_UPDATE_SCHEMA: dict[str, Any] = strict_object(
    {
        "counter": COUNTER_PARAM,
        "goal_id": {"type": "integer", "minimum": 1},
        "name": {"type": "string", "minLength": 1},
        "price": {"type": "number", "minimum": 0},
    },
    required=["counter", "goal_id"],
)

_DELETE_SCHEMA = _GET_SCHEMA

#: Полеты create-инструмента, которые уходят планировщику как значения цели.
_PLANNER_FIELDS = (
    "event",
    "url",
    "phone",
    "email",
    "filename",
    "platform",
    "param",
    "network",
    "depth",
    "seconds",
)


async def _list_goals(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    goals = await context.goals.list(resolved.id)
    return ToolResult.ok(
        {
            "counter_id": resolved.id,
            "count": len(goals),
            "goals": [goal.model_dump(exclude_none=True) for goal in goals],
        }
    )


async def _get_goal(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    goal = await context.goals.get(resolved.id, int(arguments["goal_id"]))
    return ToolResult.ok(
        {**goal.model_dump(exclude_none=True), "description": goal.describe()}
    )


async def _create_goal(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    description = str(arguments.get("description") or "").strip()
    goal_type = arguments.get("goal_type")
    if not description and not goal_type:
        raise ValidationError(
            "Нужно описание цели (description) или её тип (goal_type).",
            details={"fields": ["description", "goal_type"]},
        )
    overrides = {name: arguments[name] for name in _PLANNER_FIELDS if name in arguments}
    plan = context.planner.plan(
        description,
        name=arguments.get("name"),
        goal_type=goal_type,
        **overrides,
    )
    if plan.status.value != "ready":
        return ToolResult.needs_input(
            question=plan.question or "Уточните параметры цели.",
            missing=plan.missing,
            reason=plan.reason,
            data=plan.to_dict(),
        )
    goal = context.planner.build_goal(plan)
    if arguments.get("price") is not None:
        goal = goal.model_copy(update={"default_price": float(arguments["price"])})
    result = await context.goals.ensure_goal(resolved.id, goal)
    return ToolResult.ok(result.to_dict())


async def _update_goal(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    goal = await context.goals.get(resolved.id, int(arguments["goal_id"]))
    updates: dict[str, Any] = {}
    if "name" in arguments:
        updates["name"] = str(arguments["name"])
    if "price" in arguments:
        updates["default_price"] = float(arguments["price"])
    if not updates:
        raise ValidationError(
            "Не указано ни одного поля для изменения (name, price).",
        )
    updated = await context.goals.update(resolved.id, goal.model_copy(update=updates))
    return ToolResult.ok(
        {**updated.model_dump(exclude_none=True), "description": updated.describe()}
    )


async def _delete_goal(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    goal_id = int(arguments["goal_id"])
    payload = await context.goals.delete(resolved.id, goal_id)
    return ToolResult.ok({"deleted": True, "goal_id": goal_id, "response": payload})


TOOLS: list[Tool] = [
    Tool(
        name="metrika_list_goals",
        description="Список целей счётчика.",
        input_schema=_LIST_SCHEMA,
        handler=_list_goals,
    ),
    Tool(
        name="metrika_get_goal",
        description="Одна цель по идентификатору (официальный endpoint goal/{goalId}).",
        input_schema=_GET_SCHEMA,
        handler=_get_goal,
    ),
    Tool(
        name="metrika_create_goal",
        description=(
            "Создать цель из описания или явного типа. Если существенного "
            "значения не хватает — вернёт needs_input с вопросом, а не "
            "придуманное значение. Повтор не создаёт дубликат."
        ),
        input_schema=_CREATE_SCHEMA,
        handler=_create_goal,
    ),
    Tool(
        name="metrika_update_goal",
        description="Изменить цель (название, цена).",
        input_schema=_UPDATE_SCHEMA,
        handler=_update_goal,
    ),
    Tool(
        name="metrika_delete_goal",
        description="Удалить цель по идентификатору.",
        input_schema=_DELETE_SCHEMA,
        handler=_delete_goal,
    ),
]

__all__ = ["TOOLS"]
