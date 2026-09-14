"""Низкоуровневый клиент API Яндекс Метрики.

Отвечает за базовый адрес, заголовок ``Authorization: OAuth <token>``, разбор
ответа в JSON и приведение ошибок к типизированным. Про цели, отчёты и счётчики
здесь ничего не известно — это уровень сервисов.

Авторизация токеном описана в справке Метрики:
``https://yandex.ru/dev/metrika/ru/intro/authorization``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from yandex_metrika_agent.config import Settings, load_settings
from yandex_metrika_agent.errors import ConfigError
from yandex_metrika_agent.log import get_logger
from yandex_metrika_agent.tokens import EncryptedFileStore, TokenRecord, TokenStore
from yandex_metrika_agent.transport import (
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    REPORT_TIMEOUT,
    Transport,
)

#: Продуктивный адрес API.
BASE_URL = "https://api-metrika.yandex.net"
#: Метрика ожидает свой подтип JSON (обычный ``application/json`` тоже проходит).
JSON_CONTENT_TYPE = "application/x-yametrika+json"

_LOGGER = get_logger("client")


@runtime_checkable
class AccessTokenSource(Protocol):
    """Источник действующего access-токена."""

    async def access_token(self) -> str:
        """Вернуть токен, при необходимости обновив его."""
        ...


class StaticToken:
    """Токен из конфигурации (``METRIKA_OAUTH_TOKEN``) — без продления."""

    def __init__(self, token: str) -> None:
        if not token:
            raise ConfigError("Пустой OAuth-токен.")
        self._token = token

    async def access_token(self) -> str:
        return self._token


class StoredToken:
    """Токен из зашифрованного хранилища с автоматическим продлением.

    Args:
        store: хранилище (:class:`EncryptedFileStore` или совместимое).
        connection_id: подключение.
        refresh: async-функция продления по записи с refresh-токеном.
    """

    def __init__(
        self,
        store: TokenStore,
        connection_id: str = "default",
        refresh: Callable[[TokenRecord], Awaitable[TokenRecord]] | None = None,
    ) -> None:
        self.store = store
        self.connection_id = connection_id
        self.refresh = refresh

    async def access_token(self) -> str:
        """Действующий access-токен подключения."""

        if self.refresh is None:
            record = self.store.get(self.connection_id)
            if record is None:
                raise ConfigError(
                    f"Подключение {self.connection_id!r} не авторизовано: "
                    "выполните `ymetrika auth`.",
                )
            return record.access_token
        getter = getattr(self.store, "get_valid", None)
        if getter is not None:
            updated = getter(self.connection_id, self.refresh)
            if asyncio.iscoroutine(updated):
                updated = await updated
            return str(updated.access_token)
        record = self.store.get(self.connection_id)
        if record is None:
            raise ConfigError(f"Подключение {self.connection_id!r} не авторизовано.")
        return record.access_token

    async def current(self) -> TokenRecord | None:
        """Текущая запись токена (для отладки и ``ymetrika auth status``)."""

        return self.store.get(self.connection_id)


@dataclass
class MetrikaClient:
    """Клиент HTTP API Метрики.

    Args:
        token: строка токена либо источник токена.
        base_url: адрес API (для тестов подменяется).
        timeout: таймаут обычных запросов, сек.
        report_timeout: таймаут формирования отчётов, сек.
        retries: число повторов.
        cache_ttl: секунд кэширования одинаковых GET-запросов (0 — выключено).
        transport: готовый транспорт (иначе создаётся сам).
    """

    token: str | AccessTokenSource = ""
    base_url: str = BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    report_timeout: float = REPORT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    cache_ttl: float = 0.0
    connection_id: str = "default"
    transport: Transport = field(init=False)
    user_agent: str = "metrika-agent/0.1 (+https://github.com/; AI assistant integration)"

    def __post_init__(self) -> None:
        source: AccessTokenSource
        if isinstance(self.token, str):
            source = StaticToken(self.token) if self.token else _NoToken()
        elif isinstance(self.token, AccessTokenSource):
            source = self.token
        else:  # pragma: no cover - защита от неверного типа
            raise ConfigError("token должен быть строкой или источником токена.")
        self._source = source
        self.transport = Transport(
            timeout=self.timeout,
            retries=self.retries,
            base_url=self.base_url,
            default_headers={"Content-Type": JSON_CONTENT_TYPE, "User-Agent": self.user_agent},
            cache_ttl=self.cache_ttl,
            connection_id=self.connection_id,
        )

    # --- Создание ------------------------------------------------------------

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        token: str | None = None,
        connection_id: str = "default",
        store: TokenStore | None = None,
        refresh: Callable[[TokenRecord], Awaitable[TokenRecord]] | None = None,
        cache_ttl: float = 0.0,
        **overrides: Any,
    ) -> MetrikaClient:
        """Собрать клиент из настроек окружения.

        Приоритет токена: явный аргумент ``token`` → переменная окружения
        ``METRIKA_OAUTH_TOKEN`` → зашифрованное хранилище.
        """

        import os

        cfg = settings or load_settings()
        resolved: str | AccessTokenSource
        env_token = os.environ.get("METRIKA_OAUTH_TOKEN", "").strip()
        if token:
            resolved = token
        elif env_token:
            resolved = env_token
        else:
            token_store: TokenStore = store or EncryptedFileStore(cfg.token_dir, cfg.token_key)
            resolved = StoredToken(token_store, connection_id, refresh)
        params: dict[str, Any] = {
            "timeout": cfg.timeout,
            "retries": cfg.retries,
            "cache_ttl": cache_ttl,
            "connection_id": connection_id,
            **overrides,
        }
        return cls(token=resolved, **params)

    # --- Жизненный цикл ------------------------------------------------------

    async def start(self) -> None:
        """Открыть соединения."""

        await self.transport.start()

    async def aclose(self) -> None:
        """Закрыть соединения."""

        await self.transport.aclose()

    async def __aenter__(self) -> MetrikaClient:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # --- Выполнение запросов -------------------------------------------------

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
        cacheable: bool = False,
    ) -> Any:
        """Выполнить запрос к API Метрики и вернуть JSON.

        Args:
            method: HTTP-метод.
            path: путь от корня (``/management/v1/counters``) или абсолютный URL.
            params: параметры запроса.
            payload: тело запроса (JSON).
            cacheable: можно ли отдать кэшированный/параллельный GET.

        Raises:
            AgentError: любая ошибка (см. :mod:`yandex_metrika_agent.errors`).
        """

        headers = {"Authorization": f"OAuth {await self._source.access_token()}"}
        return await self.transport.request_json(
            method.upper(),
            path,
            params=params,
            json=payload,
            headers=headers,
            timeout=timeout,
            cacheable=cacheable,
        )

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        cacheable: bool = True,
        timeout: float | None = None,
    ) -> Any:
        """GET c кэшем/дедупликацией по умолчанию."""

        return await self.request_json(
            "GET", path, params=params, cacheable=cacheable, timeout=timeout
        )

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        return await self.request_json(
            "POST", path, params=params, payload=payload, timeout=timeout
        )

    async def put_json(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return await self.request_json("PUT", path, params=params, payload=payload)

    async def delete_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return await self.request_json("DELETE", path, params=params)


class _NoToken:
    """Токен не задан: ошибка в момент первого запроса, а не при создании."""

    async def access_token(self) -> str:
        raise ConfigError(
            "Нет OAuth-токена. Задайте METRIKA_OAUTH_TOKEN или выполните `ymetrika auth`.",
        )


__all__ = [
    "BASE_URL",
    "JSON_CONTENT_TYPE",
    "AccessTokenSource",
    "MetrikaClient",
    "StaticToken",
    "StoredToken",
]
