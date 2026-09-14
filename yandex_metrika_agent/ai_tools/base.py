"""Реестр AI-инструментов и общий конверт ответов.

Инструмент — это не HTTP-запрос, а бизнес-операция со строгой JSON-схемой:
агент передаёт ``{"counter": "example.com", "date_from": "..."}``, а низкоуровневые
параметры (``id``, ``ym:s:...``, ``filters``) строит сервисный слой.

Конверт ответа единый для всех инструментов:

* ``status="ok"`` — ``data`` с результатом;
* ``status="needs_input"`` — агенту нужно уточнение (не хватает значения или
  счётчиков подходит несколько): ``question``, ``missing``, ``candidates``;
* ``status="error"`` — типизированная ошибка ``error`` (``AgentError.to_dict``).

AI не должен конструировать произвольные HTTP-запросы — инструментов уровня
HTTP здесь нет и не будет.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.counters import CounterService, MetrikaCounter
from yandex_metrika_agent.errors import AgentError, NotFoundError, ValidationError
from yandex_metrika_agent.goals import GoalService
from yandex_metrika_agent.log import get_logger
from yandex_metrika_agent.models import Goal
from yandex_metrika_agent.planner import GoalPlanner
from yandex_metrika_agent.reports import ReportService

_LOGGER = get_logger("ai_tools")

#: Схема параметра-счётчика: идентификатор или человекочитаемый запрос.
COUNTER_PARAM: dict[str, Any] = {
    "type": ["integer", "string"],
    "description": (
        "Счётчик: идентификатор, домен (example.com, www.example.com, "
        "https://example.com) или название счётчика."
    ),
}

#: Схема даты: ISO или относительное значение Reports API.
DATE_PARAM: dict[str, Any] = {
    "type": "string",
    "pattern": r"^(\d{4}-\d{2}-\d{2}|today|yesterday|\d+ ?days? ?ago|\d+ ?daysAgo)$",
    "description": "Дата YYYY-MM-DD или today / yesterday / NdaysAgo.",
}


def strict_object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Строгая JSON-схема объекта: неизвестные параметры запрещены."""

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


@dataclass
class ToolResult:
    """Единый ответ инструмента агенту."""

    status: str  # ok | needs_input | error
    data: Any = None
    question: str | None = None
    missing: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-открытка для агента (только заполненные поля)."""

        payload: dict[str, Any] = {"status": self.status}
        if self.data is not None:
            payload["data"] = self.data
        if self.question:
            payload["question"] = self.question
        if self.missing:
            payload["missing"] = self.missing
        if self.candidates:
            payload["candidates"] = self.candidates
        if self.reason:
            payload["reason"] = self.reason
        if self.error:
            payload["error"] = self.error
        return payload

    @classmethod
    def ok(cls, data: Any) -> ToolResult:
        return cls(status="ok", data=data)

    @classmethod
    def needs_input(
        cls,
        question: str,
        *,
        missing: list[str] | None = None,
        candidates: list[dict[str, Any]] | None = None,
        reason: str | None = None,
        data: Any = None,
    ) -> ToolResult:
        return cls(
            status="needs_input",
            data=data,
            question=question,
            missing=missing or [],
            candidates=candidates or [],
            reason=reason,
        )

    @classmethod
    def failed(cls, error: AgentError) -> ToolResult:
        return cls(status="error", error=error.to_dict(), reason=error.message)


@dataclass
class ToolContext:
    """Сервисы, доступные инструментам."""

    client: MetrikaClient
    counters: CounterService = field(init=False)
    goals: GoalService = field(init=False)
    reports: ReportService = field(init=False)
    planner: GoalPlanner = field(default_factory=GoalPlanner)

    def __post_init__(self) -> None:
        self.counters = CounterService(self.client)
        self.goals = GoalService(self.client)
        self.reports = ReportService(self.client)

    async def resolve_counter(self, value: int | str) -> MetrikaCounter | ToolResult:
        """Разрешить параметр ``counter`` в один счётчик.

        0 совпадений — ошибка NotFound (статус error), 1 — счётчик,
        несколько — ``needs_input`` со списком кандидатов: пользователь
        выбирает, агент не угадывает.
        """

        if isinstance(value, bool):
            raise ValidationError("counter не может быть булевым значением.")
        if isinstance(value, int):
            if value <= 0:
                raise ValidationError(
                    "counter_id должен быть положительным.",
                    details={"counter": value},
                )
            return await self.counters.get(value)
        text = str(value or "").strip()
        if not text:
            raise ValidationError("Пустой счётчик в запросе.")
        if text.isdigit():
            return await self.counters.get(int(text))
        try:
            return await self.counters.resolve_one(text)
        except NotFoundError as exc:
            raise exc
        except ValidationError as exc:
            candidates = exc.details.get("candidates") or []
            if candidates:
                return ToolResult.needs_input(
                    question=(
                        f"По запросу {text!r} найдено несколько счётчиков. "
                        "Уточните, какой имеется в виду."
                    ),
                    missing=["counter"],
                    candidates=list(candidates),
                    reason=exc.message,
                )
            raise


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class Tool:
    """Инструмент: имя, описание, строгая схема, обработчик."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler

    def spec(self) -> dict[str, Any]:
        """Открытка инструмента для реестра AI-агента (MCP-совместимая)."""

        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class ToolRegistry:
    """Реестр инструментов с вызовом по имени."""

    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValidationError(
                    f"Дубликат инструмента: {tool.name}.",
                    details={"tool": tool.name},
                )
            self._tools[tool.name] = tool

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ValidationError(
                f"Неизвестный инструмент: {name!r}.",
                details={"tool": name, "allowed": self.names},
            ) from exc

    def specs(self) -> list[dict[str, Any]]:
        """Список инструментов со строгими схемами для AI-агента."""

        return [self._tools[name].spec() for name in self.names]

    async def call(
        self, context: ToolContext, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Вызвать инструмент и вернуть JSON-конверт.

        Ошибки агента превращаются в ``status="error"``, неожиданные —
        в ``ValidationError``: агенту не нужны стеки трейсов.
        """

        tool = self.get(name)
        try:
            result = tool.handler(context, dict(arguments or {}))
            if inspect.isawaitable(result):
                result = await result
            return _as_envelope(result)
        except AgentError as exc:
            _LOGGER.info("Инструмент %s: %s", name, exc.message)
            return ToolResult.failed(exc).to_dict()


def _as_envelope(result: Any) -> dict[str, Any]:
    """Привести ответ обработчика к конверту."""

    if isinstance(result, ToolResult):
        return result.to_dict()
    if isinstance(result, Goal):
        return ToolResult.ok(result.model_dump(exclude_none=True)).to_dict()
    if isinstance(result, dict) and "status" in result and result.get("status") in {
        "ok",
        "needs_input",
        "error",
    }:
        return result
    return ToolResult.ok(result).to_dict()


__all__ = [
    "COUNTER_PARAM",
    "DATE_PARAM",
    "Handler",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "strict_object",
]
