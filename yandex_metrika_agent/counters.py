"""Сервис счётчиков (Management API v1).

Методы API:

* ``GET /management/v1/counters`` — список доступных счётчиков;
* ``GET /management/v1/counter/{counterId}`` — один счётчик.

Ответ Метрики завёрнут в ``{"content": [{"counter": {...}}, ...]}``, поля —
в camelCase. :class:`MetrikaCounter` нормализует их до понятий, нужных
AI-агенту: сайт, домен, владелец, права.

Отдельная задача — выбрать счётчик по словам пользователя («покажи статистику
example.com»). :meth:`CounterService.resolve` возвращает все подходящие
счётчики: если найден один — агент использует его, если несколько — просит
уточнить. Пользователя не нужно заставлять вводить ``counterId``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.errors import NotFoundError, ValidationError
from yandex_metrika_agent.log import get_logger

#: Поля счётчика, которые стоит запросить у API.
COUNTER_FIELDS = (
    "id,name,site,status,type,first_watch_date,timezone,owner,permission,counter_type"
)

_LOGGER = get_logger("counters")


class MetrikaCounter(BaseModel):
    """Нормализованный счётчик для ответов агенту.

    Поля соответствуют интерфейсу из задания: идентификатор, название, сайт,
    домен, статус, владелец, права, часовой пояс.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: int
    name: str | None = None
    site: str | None = None
    domain: str | None = None
    status: str | None = None
    owner_login: str | None = None
    permission: str | None = None
    timezone: str | None = None

    @property
    def title(self) -> str:
        """Название или понятный суррогат."""

        return self.name or (self.site or f"Счётчик {self.id}")

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> MetrikaCounter:
        """Собрать нормализованный счётчик из фрагмента ответа API (camelCase)."""

        site = _as_str(raw.get("site"))
        owner = raw.get("owner")
        owner_login = _as_str(owner.get("login")) if isinstance(owner, dict) else None
        return cls(
            id=int(raw["id"]),
            name=_as_str(raw.get("name")),
            site=site,
            domain=_domain_from_site(site),
            status=_as_str(raw.get("status")),
            owner_login=owner_login,
            permission=_permission_summary(raw.get("permission")),
            timezone=_as_str(raw.get("timezone")),
        )


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _domain_from_site(site: str | None) -> str | None:
    """Выделить домен из значения ``site`` (может быть с протоколом и путём)."""

    if not site:
        return None
    candidate = site.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate
    host = urlparse(candidate).hostname or candidate
    host = host.lower()
    return host.removeprefix("www.") or None


def _permission_summary(permission: Any) -> str | None:
    """Свести объект прав Метрики к короткому слову: ``owner``/``read``/``none``."""

    if not isinstance(permission, dict):
        return None
    operator = permission.get("operator")
    if isinstance(operator, dict):
        perm_type = operator.get("type")
        if isinstance(perm_type, str) and perm_type.strip():
            return perm_type.strip().lower()
    return None


def _unwrap_counters(payload: Any) -> list[dict[str, Any]]:
    """Достать список объектов счётчика из обёртки ``content``."""

    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, list):
            found: list[dict[str, Any]] = []
            for item in content:
                if isinstance(item, dict):
                    counter = item.get("counter")
                    if isinstance(counter, dict):
                        found.append(counter)
                    elif "id" in item:
                        found.append(item)
            return found
        if "counter" in payload and isinstance(payload["counter"], dict):
            return [payload["counter"]]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


class CounterService:
    """Чтение счётчиков и выбор счётчика по сайту/домену.

    Args:
        client: клиент API Метрики.
        fields: какие поля запрашивать у API.
    """

    def __init__(self, client: MetrikaClient, *, fields: str = COUNTER_FIELDS) -> None:
        self.client = client
        self.fields = fields

    async def list(self, *, fields: str | None = None) -> list[MetrikaCounter]:
        """Список доступных счётчиков пользователя."""

        payload = await self.client.get_json(
            "/management/v1/counters",
            params={"fields": fields or self.fields},
        )
        counters = [MetrikaCounter.from_api(raw) for raw in _unwrap_counters(payload)]
        _LOGGER.debug("Счётчиков: %s", len(counters))
        return counters

    async def get(self, counter_id: int) -> MetrikaCounter:
        """Один счётчик по идентификатору."""

        if not isinstance(counter_id, int) or counter_id <= 0:
            raise ValidationError(
                "counter_id должен быть положительным целым числом.",
                details={"counter_id": counter_id},
            )
        payload = await self.client.get_json(
            f"/management/v1/counter/{counter_id}",
            params={"fields": self.fields},
        )
        raw = _unwrap_counters(payload)
        if not raw:
            raise NotFoundError(
                f"Счётчик {counter_id} не найден или недоступен.",
                details={"counter_id": counter_id},
            )
        return MetrikaCounter.from_api(raw[0])

    async def resolve(
        self,
        query: str,
        *,
        fields: str | None = None,
    ) -> list[MetrikaCounter]:
        """Найти счётчики по названию сайта, домену или идентификатору.

        Возвращает все совпадения. Смысл в том, чтобы решение принимал вызывающий:

        * ноль — уточнить у пользователя;
        * один — использовать;
        * несколько — показать список и попросить выбрать.
        """

        needle = (query or "").strip().lower()
        if not needle:
            raise ValidationError("Пустой запрос для поиска счётчика.")
        counters = await self.list(fields=fields)
        # Числовой запрос считаем идентификатором.
        if needle.isdigit():
            wanted = int(needle)
            return [counter for counter in counters if counter.id == wanted]
        domain = _domain_from_site(needle)
        matched: list[MetrikaCounter] = []
        for counter in counters:
            if _counter_matches(counter, needle, domain):
                matched.append(counter)
        return matched

    async def resolve_one(self, query: str, *, fields: str | None = None) -> MetrikaCounter:
        """Найти ровно один счётчик.

        Raises:
            NotFoundError: совпадений нет.
            ValidationError: совпадений несколько — нужно уточнение.
        """

        matched = await self.resolve(query, fields=fields)
        if not matched:
            raise NotFoundError(
                f"Не найден счётчик по запросу {query!r}.",
                details={"query": query},
            )
        if len(matched) > 1:
            raise ValidationError(
                f"По запросу {query!r} найдено несколько счётчиков — нужно уточнить.",
                details={
                    "query": query,
                    "candidates": [
                        {"id": c.id, "name": c.name, "site": c.site, "domain": c.domain}
                        for c in matched
                    ],
                },
            )
        return matched[0]


def _counter_matches(counter: MetrikaCounter, needle: str, domain: str | None) -> bool:
    """Совпадает ли счётчик с запросом по домену, сайту или названию."""

    for candidate in (counter.domain, counter.site, counter.name):
        if not candidate:
            continue
        value = candidate.lower()
        value = _domain_from_site(value) or value
        if value == needle or (domain and value == domain):
            return True
        if needle in value or (domain and domain in value):
            return True
    return False


__all__ = [
    "COUNTER_FIELDS",
    "CounterService",
    "MetrikaCounter",
]
