"""AI-инструменты аналитики: отчёты Reports API.

Все инструменты принимают бизнес-параметры (счётчик, период, лимит) и
человеческие имена метрик/измерений (``visits``, ``traffic_source``);
``ym:s:...`` и строку ``filters`` собирает сервисный слой.
"""

from __future__ import annotations

from typing import Any

from yandex_metrika_agent.ai_tools.base import (
    COUNTER_PARAM,
    DATE_PARAM,
    Tool,
    ToolContext,
    ToolResult,
    ToolSafety,
    strict_object,
)
from yandex_metrika_agent.filters import Operator

#: Операторы фильтра для JSON-схемы (официальный список Метрики).
_OPERATOR_VALUES = sorted(op.value for op in Operator)

_FILTER_ITEM: dict[str, Any] = strict_object(
    {
        "field": {
            "type": "string",
            "description": "Измерение: человекочитаемое имя (traffic_source) или ym:s:...",
        },
        "operator": {"type": "string", "enum": _OPERATOR_VALUES},
        "value": {
            "description": "Скаляр или список значений (для in/not_in).",
        },
        "negate": {"type": "boolean", "description": "Обернуть условие в NOT(...)."},
    },
    required=["field", "operator"],
)

_PERIOD: dict[str, Any] = {
    "type": "array",
    "prefixItems": [DATE_PARAM, DATE_PARAM],
    "minItems": 2,
    "maxItems": 2,
    "description": "Период [date_from, date_to].",
}

_GET_REPORT_SCHEMA: dict[str, Any] = strict_object(
    {
        "counter": COUNTER_PARAM,
        "metrics": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 20,
            "description": "Метрики: visits, users, bounce_rate или ym:s:...",
        },
        "dimensions": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 10,
            "description": "Измерения: date, traffic_source, page или ym:s:...",
        },
        "date_from": DATE_PARAM,
        "date_to": DATE_PARAM,
        "filters": {"type": "array", "items": _FILTER_ITEM, "maxItems": 20},
        "sort": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Сортировка: visits или -visits.",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
    },
    required=["counter", "metrics"],
)

_COUNTER_DATES_SCHEMA: dict[str, Any] = strict_object(
    {"counter": COUNTER_PARAM, "date_from": DATE_PARAM, "date_to": DATE_PARAM},
    required=["counter"],
)

_TOP_SCHEMA: dict[str, Any] = strict_object(
    {
        "counter": COUNTER_PARAM,
        "date_from": DATE_PARAM,
        "date_to": DATE_PARAM,
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
    },
    required=["counter"],
)

_GOAL_STATS_SCHEMA: dict[str, Any] = strict_object(
    {
        "counter": COUNTER_PARAM,
        "goal_id": {"type": "integer", "minimum": 1},
        "date_from": DATE_PARAM,
        "date_to": DATE_PARAM,
        "by_source": {"type": "boolean", "description": "Разбить по источникам трафика."},
    },
    required=["counter", "goal_id"],
)

_COMPARE_SCHEMA: dict[str, Any] = strict_object(
    {
        "counter": COUNTER_PARAM,
        "period_a": _PERIOD,
        "period_b": _PERIOD,
        "metrics": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 20,
        },
    },
    required=["counter", "period_a", "period_b"],
)


async def _get_report(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    command: dict[str, Any] = {
        "counter_id": resolved.id,
        "metrics": arguments["metrics"],
        "dimensions": arguments.get("dimensions") or [],
        "filters": arguments.get("filters") or [],
        "sort_by": arguments.get("sort") or [],
    }
    if arguments.get("date_from"):
        command["date1"] = arguments["date_from"]
    if arguments.get("date_to"):
        command["date2"] = arguments["date_to"]
    if arguments.get("limit") is not None:
        command["limit"] = arguments["limit"]
    report = await context.reports.get_report(command)
    return ToolResult.ok(
        {
            "counter_id": resolved.id,
            "metrics": report.metric_names,
            "dimensions": report.dimension_names,
            "totals": report.totals,
            "sampled": report.sampled,
            "rows": context.reports.rows_as_dicts(report),
        }
    )


async def _get_traffic(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    summary = await context.reports.get_traffic(
        resolved.id,
        date_from=arguments.get("date_from"),
        date_to=arguments.get("date_to"),
    )
    return ToolResult.ok(summary)


async def _get_traffic_by_day(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    rows = await context.reports.get_traffic_by_day(
        resolved.id,
        date_from=arguments.get("date_from"),
        date_to=arguments.get("date_to"),
    )
    return ToolResult.ok({"counter_id": resolved.id, "rows": rows})


async def _get_sources(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    rows = await context.reports.get_sources(
        resolved.id,
        date_from=arguments.get("date_from"),
        date_to=arguments.get("date_to"),
        limit=int(arguments.get("limit", 10)),
    )
    return ToolResult.ok({"counter_id": resolved.id, "rows": rows})


async def _get_top_pages(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    rows = await context.reports.get_top_pages(
        resolved.id,
        date_from=arguments.get("date_from"),
        date_to=arguments.get("date_to"),
        limit=int(arguments.get("limit", 10)),
    )
    return ToolResult.ok({"counter_id": resolved.id, "rows": rows})


async def _get_goal_stats(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    stats = await context.reports.get_goal_stats(
        resolved.id,
        int(arguments["goal_id"]),
        date_from=arguments.get("date_from"),
        date_to=arguments.get("date_to"),
        by_source=bool(arguments.get("by_source", False)),
    )
    return ToolResult.ok(stats)


async def _compare_periods(context: ToolContext, arguments: dict[str, Any]) -> Any:
    resolved = await context.resolve_counter(arguments["counter"])
    if isinstance(resolved, ToolResult):
        return resolved
    rows = await context.reports.compare_periods(
        resolved.id,
        period_a=tuple(arguments["period_a"]),
        period_b=tuple(arguments["period_b"]),
        metrics=list(arguments.get("metrics") or ["visits", "users"]),
    )
    return ToolResult.ok(
        {
            "counter_id": resolved.id,
            "rows": [row.model_dump(exclude_none=True) for row in rows],
        }
    )


TOOLS: list[Tool] = [
    Tool(
        name="metrika_get_report",
        description=(
            "Произвольный отчёт: метрики/измерения человеческими именами, "
            "фильтры структурированные, ym:s: строит сервис."
        ),
        input_schema=_GET_REPORT_SCHEMA,
        handler=_get_report,
        safety=ToolSafety.READ_ONLY,
    ),
    Tool(
        name="metrika_get_traffic",
        description="Сводка посещаемости: визиты, посетители, просмотры, отказы, время.",
        input_schema=_COUNTER_DATES_SCHEMA,
        handler=_get_traffic,
        safety=ToolSafety.READ_ONLY,
    ),
    Tool(
        name="metrika_get_traffic_by_day",
        description="Динамика посещаемости по дням.",
        input_schema=_COUNTER_DATES_SCHEMA,
        handler=_get_traffic_by_day,
        safety=ToolSafety.READ_ONLY,
    ),
    Tool(
        name="metrika_get_sources",
        description="Источники трафика по посещаемости (убывание).",
        input_schema=_TOP_SCHEMA,
        handler=_get_sources,
        safety=ToolSafety.READ_ONLY,
    ),
    Tool(
        name="metrika_get_top_pages",
        description="Популярные страницы по просмотрам.",
        input_schema=_TOP_SCHEMA,
        handler=_get_top_pages,
        safety=ToolSafety.READ_ONLY,
    ),
    Tool(
        name="metrika_get_goal_stats",
        description="Достижения и конверсия цели (сводно или по источникам).",
        input_schema=_GOAL_STATS_SCHEMA,
        handler=_get_goal_stats,
        safety=ToolSafety.READ_ONLY,
    ),
    Tool(
        name="metrika_compare_periods",
        description="Сравнение метрик за два периода (delta, delta_percent).",
        input_schema=_COMPARE_SCHEMA,
        handler=_compare_periods,
        safety=ToolSafety.READ_ONLY,
    ),
]

__all__ = ["TOOLS"]
