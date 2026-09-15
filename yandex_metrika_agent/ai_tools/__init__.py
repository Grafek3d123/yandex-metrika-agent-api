"""AI Tool Layer: инструменты с строгими JSON-схемами для агента.

Слой между AI-агентом и сервисами пакета. Агент видит только бизнес-операции
(``metrika_get_traffic`` с ``counter``/``date_from``), произвольных HTTP-
инструментов нет.

Использование::

    from yandex_metrika_agent import MetrikaClient
    from yandex_metrika_agent.ai_tools import MetrikaTools

    tools = MetrikaTools(MetrikaClient(token="..."))
    specs = tools.specs()                       # схемы для регистрации у агента
    answer = await tools.call(                  # JSON-конверт ok/needs_input/error
        "metrika_get_traffic",
        {"counter": "example.com", "date_from": "2026-09-01", "date_to": "2026-09-14"},
    )
"""

from __future__ import annotations

from typing import Any

from yandex_metrika_agent.ai_tools import analytics, counters, goals
from yandex_metrika_agent.ai_tools.base import (
    ConfirmationPolicy,
    Tool,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSafety,
    strict_object,
)
from yandex_metrika_agent.client import MetrikaClient

#: Все инструменты слоя (14 бизнес-операций).
ALL_TOOLS: list[Tool] = [*counters.TOOLS, *goals.TOOLS, *analytics.TOOLS]


class MetrikaTools:
    """Фасад AI-слоя: реестр инструментов поверх клиентских сервисов.

    Разрушающие инструменты (``safety=DESTRUCTIVE``) закрыты confirmation guard'ом
    в :class:`ToolRegistry`. Хост управляет подтверждениями через
    :meth:`approve_confirmation` — отдельный доверенный метод, который AI-агент
    вызвать не может (он не является инструментом).
    """

    def __init__(
        self,
        client: MetrikaClient,
        *,
        confirmation_ttl: float = 300.0,
        confirmation_secret: bytes | None = None,
        confirmations: ConfirmationPolicy | None = None,
    ) -> None:
        self.client = client
        self.context = ToolContext(client)
        self.confirmations = confirmations or ConfirmationPolicy(
            ttl_seconds=confirmation_ttl, secret=confirmation_secret
        )
        self.registry = ToolRegistry(ALL_TOOLS, confirmations=self.confirmations)

    def specs(self) -> list[dict[str, Any]]:
        """Схемы инструментов для регистрации у AI-агента."""

        return self.registry.specs()

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Вызвать инструмент, вернуть JSON-конверт."""

        return await self.registry.call(self.context, name, arguments or {})

    def approve_confirmation(self, confirmation_id: str, *, approved_by: str) -> dict[str, Any]:
        """Хост выдал согласие пользователя → одноразовый токен для операции.

        Вызывать ТОЛЬКО после явного подтверждения операции человеком.
        Недоступен из AI Tool Layer: это не инструмент.
        """

        return self.registry.approve(confirmation_id, approved_by=approved_by)

    def pending_confirmations(self) -> list[str]:
        """Идентификаторы операций, ожидающих подтверждения."""

        return self.registry.pending_confirmations()

    async def aclose(self) -> None:
        await self.client.aclose()


__all__ = [
    "ALL_TOOLS",
    "ConfirmationPolicy",
    "MetrikaTools",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "ToolSafety",
    "strict_object",
]
