"""Сервис счётчиков (Management API v1).

Методы API (проверено по официальной документации ``management/openapi``):

* ``GET /management/v1/counters`` — список счётчиков, ответ
  ``{"rows": N, "counters": [CounterBrief, ...]}`` (старый формат
  ``{"content": [{"counter": {...}}]}`` тоже поддерживается);
* ``GET /management/v1/counter/{counterId}`` — один счётчик, ответ
  ``{"counter": {...}}``.

Поле ``site`` в актуальном API вернулось как объект ``site2: {"site": ...}``;
сервис понимает оба варианта. Владелец — строка ``owner_login``, права —
строка ``permission`` (``rw``/``w``/``r``/``none``).

Отдельная задача — выбрать счётчик по словам пользователя («покажи статистику
example.com»). :meth:`CounterService.resolve` возвращает все подходящие
счётчики: если найден один — агент использует его, если несколько — просит
уточнить. Пользователя не нужно заставлять вводить ``counterId``.
"""

from __future__ import annotations

import builtins
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.errors import NotFoundError, ValidationError
from yandex_metrika_agent.log import get_logger

#: Дополнительные объекты, которые можно запросить параметром ``field``
#: (``GET /management/v1/counter/{id}``): goals, mirrors, grants, filters,
#: operations, counter_flags, measurement_tokens.
COUNTER_EXTRA_FIELDS: tuple[str, ...] = ("goals", "mirrors", "grants")

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
    type: str | None = None
    favorite: bool | None = None
    created_at: str | None = None

    @property
    def title(self) -> str:
        """Название или понятный суррогат."""

        return self.name or (self.site or f"Счётчик {self.id}")

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> MetrikaCounter:
        """Собрать нормализованный счётчик из фрагмента ответа API."""

        site = _site_from_raw(raw)
        return cls(
            id=int(raw["id"]),
            name=_as_str(raw.get("name")),
            site=site,
            domain=_domain_from_site(site),
            status=_as_str(raw.get("status")),
            owner_login=_as_str(raw.get("owner_login")),
            permission=_permission_summary(raw.get("permission")),
            timezone=_as_str(raw.get("time_zone_name")) or _as_str(raw.get("timezone")),
            type=_as_str(raw.get("type")),
            favorite=raw.get("favorite") if isinstance(raw.get("favorite"), bool) else None,
            created_at=_as_str(raw.get("create_time")),
        )


def _site_from_raw(raw: dict[str, Any]) -> str | None:
    """Значение сайта счётчика: новый ``site2.site``, старый ``site``, зеркало."""

    site2 = raw.get("site2")
    if isinstance(site2, dict):
        site = _as_str(site2.get("site"))
        if site:
            return site
    site = _as_str(raw.get("site"))
    if site:
        return site
    mirrors = raw.get("mirrors2")
    if isinstance(mirrors, list):
        for mirror in mirrors:
            if isinstance(mirror, dict) and mirror.get("is_main"):
                main = _as_str(mirror.get("site"))
                if main:
                    return main
        for mirror in mirrors:
            if isinstance(mirror, dict):
                first = _as_str(mirror.get("site"))
                if first:
                    return first
    return None


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def normalize_host(value: str) -> str | None:
    """Хост без протокола, пути и ``www.`` — канон для сопоставления сайтов."""

    candidate = value.strip().lower()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = "http://" + candidate
    host = urlparse(candidate).hostname or candidate.replace("http://", "")
    host = host.lower().rstrip(".")
    return host.removeprefix("www.") or None


def _domain_from_site(site: str | None) -> str | None:
    """Выделить домен из значения ``site`` (может быть с протоколом и путём)."""

    return normalize_host(site) if site else None


def _permission_summary(permission: Any) -> str | None:
    """Свести права Метрики к короткому слову: ``rw``/``r``/``w``/``none``.

    Актуальный API отдаёт строку (``rw``), старый — объект
    ``{"operator": {"type": "..."}}``.
    """

    if isinstance(permission, str) and permission.strip():
        return permission.strip().lower()
    if isinstance(permission, dict):
        operator = permission.get("operator")
        if isinstance(operator, dict):
            perm_type = operator.get("type")
            if isinstance(perm_type, str) and perm_type.strip():
                return perm_type.strip().lower()
    return None


def _unwrap_counters(payload: Any) -> list[dict[str, Any]]:
    """Достать список объектов счётчика из обёртки ответа API.

    Поддерживает актуальный формат ``{"rows": N, "counters": [...]}``,
    одиночный ``{"counter": {...}}`` и старый ``{"content": [{"counter": ...}]}``.
    """

    if isinstance(payload, dict):
        counters = payload.get("counters")
        if isinstance(counters, list):
            return [item for item in counters if isinstance(item, dict)]
        counter = payload.get("counter")
        if isinstance(counter, dict):
            return [counter]
        content = payload.get("content")
        if isinstance(content, list):
            found: list[dict[str, Any]] = []
            for item in content:
                if isinstance(item, dict):
                    inner = item.get("counter")
                    if isinstance(inner, dict):
                        found.append(inner)
                    elif "id" in item:
                        found.append(item)
            return found
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


class CounterService:
    """Чтение счётчиков и выбор счётчика по сайту/домену.

    Args:
        client: клиент API Метрики.
        per_page: размер страницы при перечислении (API максимум 1000).
    """

    def __init__(self, client: MetrikaClient, *, per_page: int = 1000) -> None:
        self.client = client
        self.per_page = per_page

    async def list(
        self,
        *,
        search_string: str | None = None,
        extra_fields: tuple[str, ...] = (),
    ) -> list[MetrikaCounter]:
        """Список доступных счётчиков пользователя.

        Args:
            search_string: фильтр по названию/адресу на стороне API.
            extra_fields: дополнительные объекты параметра ``field``
                (``goals``, ``mirrors``, ...).
        """

        params: dict[str, Any] = {"per_page": self.per_page}
        if search_string:
            params["search_string"] = search_string
        for name in extra_fields:
            params.setdefault("field", []).append(name)
        payload = await self.client.get_json("/management/v1/counters", params=params)
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
        payload = await self.client.get_json(f"/management/v1/counter/{counter_id}")
        raw = _unwrap_counters(payload)
        if not raw:
            raise NotFoundError(
                f"Счётчик {counter_id} не найден или недоступен.",
                details={"counter_id": counter_id},
            )
        return MetrikaCounter.from_api(raw[0])

    async def resolve(self, query: str) -> builtins.list[MetrikaCounter]:
        """Найти счётчики по названию сайта, домену, URL или идентификатору.

        Возвращает все совпадения, точные совпаства — первыми и отдельным
        приоритетом: если есть точное совпадение по домену/имени/id, список
        ограничивается им. Смысл в том, чтобы решение принимал вызывающий:

        * ноль — уточнить у пользователя;
        * один — использовать;
        * несколько — показать список и попросить выбрать.
        """

        needle = (query or "").strip().lower()
        if not needle:
            raise ValidationError("Пустой запрос для поиска счётчика.")
        counters = await self.list()
        # Числовой запрос считаем идентификатором.
        if needle.isdigit():
            wanted = int(needle)
            return [counter for counter in counters if counter.id == wanted]
        host = normalize_host(needle)
        exact: builtins.list[MetrikaCounter] = []
        partial: builtins.list[MetrikaCounter] = []
        for counter in counters:
            score = _counter_match_score(counter, needle, host)
            if score == 2:
                exact.append(counter)
            elif score == 1:
                partial.append(counter)
        return exact or partial

    async def resolve_one(self, query: str) -> MetrikaCounter:
        """Найти ровно один счётчик.

        Raises:
            NotFoundError: совпадений нет.
            ValidationError: совпадений несколько — нужно уточнение.
        """

        matched = await self.resolve(query)
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


def _counter_match_score(counter: MetrikaCounter, needle: str, host: str | None) -> int:
    """2 — точное совпадение, 1 — частичное, 0 — не совпал."""

    best = 0
    for candidate in (counter.domain, counter.site, counter.name):
        if not candidate:
            continue
        value = candidate.strip().lower()
        normalized = normalize_host(value) or value
        if host and normalized == host:
            return 2
        if value == needle:
            return 2
        if host and normalized and (host in normalized or normalized in host):
            best = max(best, 1)
        if needle in value or (len(needle) >= 4 and value in needle):
            best = max(best, 1)
    return best


__all__ = [
    "COUNTER_EXTRA_FIELDS",
    "CounterService",
    "MetrikaCounter",
    "normalize_host",
]
