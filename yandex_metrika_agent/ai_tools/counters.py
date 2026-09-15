"""AI-инструменты по счётчикам: список и чтение.

Агент передаёт человекочитаемый ``counter`` (домен, название или id) —
разрешение в ``counterId`` делает сервисный слой. Несколько кандидатов —
``needs_input``, ноль — ошибка ``not_found``.
"""

from __future__ import annotations

from typing import Any

from yandex_metrika_agent.ai_tools.base import (
    COUNTER_PARAM,
    Tool,
    ToolContext,
    ToolResult,
    ToolSafety,
    strict_object,
)

_LIST_SCHEMA: dict[str, Any] = strict_object(
    {
        "search_string": {
            "type": "string",
            "description": "Необязательный фильтр по названию/адресу на стороне API.",
        },
    },
    required=[],
)

_GET_SCHEMA: dict[str, Any] = strict_object({"counter": COUNTER_PARAM}, required=["counter"])


async def _list_counters(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    counters = await context.counters.list(search_string=arguments.get("search_string"))
    return ToolResult.ok(
        {
            "count": len(counters),
            "counters": [counter.model_dump(exclude_none=True) for counter in counters],
        }
    )


async def _get_counter(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    return ToolResult.ok(resolved.model_dump(exclude_none=True))


TOOLS: list[Tool] = [
    Tool(
        name="metrika_list_counters",
        description="Список счётчиков, доступных текущему пользователю Метрики.",
        input_schema=_LIST_SCHEMA,
        handler=_list_counters,
        safety=ToolSafety.READ_ONLY,
    ),
    Tool(
        name="metrika_get_counter",
        description=(
            "Данные одного счётчика по id, домену (example.com, www, https://) "
            "или названию. При нескольких совпадениях просит уточнить."
        ),
        input_schema=_GET_SCHEMA,
        handler=_get_counter,
        safety=ToolSafety.READ_ONLY,
    ),
]

__all__ = ["TOOLS"]
