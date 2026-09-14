"""HTTP-транспорт с повторами, лимитом частоты, кэшем и дедупликацией.

Повторы: экспоненциальная задержка с джиттером для 420/429/5xx и сетевых ошибок,
учёт заголовка ``Retry-After``.

Кэш и дедупликация нужны агенту: модель может дважды за один диалог спросить
одинаковый отчёт. Одинаковый GET выполняется один раз (single-flight), ответ
короткое время отдаётся из кэша.

Секреты в логи не пишутся: параметры проходят через ``anonymize_query``,
заголовок ``Authorization`` исключается полностью.
"""

from __future__ import annotations

import asyncio
import json as jsonlib
import random
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import httpx

from yandex_metrika_agent.errors import (
    RATE_LIMIT_STATUS,
    ReportTimeoutError,
    TransportError,
    error_from_response,
    is_retryable_status,
)
from yandex_metrika_agent.log import anonymize_query, get_logger

DEFAULT_TIMEOUT = 30.0
#: Асинхронные отчёты Метрики формируются дольше обычных запросов.
REPORT_TIMEOUT = 180.0
DEFAULT_RETRIES = 3
BASE_DELAY = 0.5
MAX_DELAY = 30.0
JITTER = 0.25

#: Минимальный интервал опроса задачи асинхронного отчёта, сек.
REPORT_POLL_INTERVAL = 3.0

RETRY_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)

_LOGGER = get_logger("transport")


def backoff_delay(attempt: int, *, retry_after: float | None = None) -> float:
    """Экспоненциальная задержка перед повтором (с джиттером)."""

    if attempt < 0:
        raise ValueError("attempt не может быть отрицательным")
    if retry_after is not None and retry_after > 0:
        return min(float(retry_after), MAX_DELAY)
    delay = BASE_DELAY * (2**attempt)
    return min(delay * (1 + random.uniform(-JITTER, JITTER)), MAX_DELAY)


def parse_retry_after(headers: httpx.Headers | dict[str, str]) -> float | None:
    """Разобрать ``Retry-After`` (секунды или HTTP-date)."""

    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    if not raw:
        return None
    value = str(raw).strip()
    if value.isdigit():
        return float(value)
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(when.timestamp() - time.time(), 0.0)


def canonical_key(method: str, url: str, params: dict[str, Any] | None = None) -> str:
    """Устойчивый ключ запроса для кэша и дедупликации."""

    if not params:
        return f"{method.upper()}|{url}"
    encoded = jsonlib.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return f"{method.upper()}|{url}|{encoded}"


@dataclass
class Transport:
    """Переиспользуемый HTTP-клиент Метрики.

    Args:
        timeout: таймаут обычных запросов, сек.
        retries: число повторов после первой попытки.
        base_url: базовый адрес, например ``https://api-metrika.yandex.net``.
        default_headers: заголовки для всех запросов.
        client: готовый :class:`httpx.AsyncClient` для тестов и стендов.
        cache_ttl: секунд, в течение которых повторный GET отдаётся из кэша
            (``0`` — кэш выключен).
        cache_max_entries: размер LRU-кэша.
    """

    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    base_url: str | None = None
    default_headers: dict[str, str] = field(default_factory=dict)
    client: httpx.AsyncClient | None = field(default=None, repr=False)
    cache_ttl: float = 0.0
    cache_max_entries: int = 256

    _owned: httpx.AsyncClient | None = field(default=None, repr=False)
    _cache: OrderedDict[str, tuple[float, Any]] = field(default_factory=OrderedDict, repr=False)
    _inflight: dict[str, asyncio.Task[Any]] = field(default_factory=dict, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def __post_init__(self) -> None:
        if self.timeout <= 0:
            raise ValueError("timeout должен быть больше 0")
        if self.retries < 0:
            raise ValueError("retries не может быть отрицательным")
        if self.cache_ttl < 0:
            raise ValueError("cache_ttl не может быть отрицательным")
        if self.cache_max_entries < 1:
            raise ValueError("cache_max_entries должен быть больше 0")

    # --- Жизненный цикл ------------------------------------------------------

    async def start(self) -> None:
        """Открыть пул соединений (инжектированный клиент не пересоздаётся)."""

        if self.client is not None:
            return
        if self._owned is None or self._owned.is_closed:
            self._owned = httpx.AsyncClient(
                timeout=self.timeout,
                base_url=self.base_url or "",
                headers=self.default_headers,
            )
        self.client = self._owned

    async def aclose(self) -> None:
        """Закрыть собственный пул соединений."""

        if self._owned is not None and not self._owned.is_closed:
            await self._owned.aclose()
        self._owned = None
        self.client = None

    async def __aenter__(self) -> Transport:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    # --- Запросы -------------------------------------------------------------

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: httpx.BasicAuth | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Выполнить запрос с повторами.

        Возвращает ответ, в том числе с кодом 4xx (кроме 420/429 и 5xx,
        которые повторяются). Бросает :class:`TransportError` при сетевых
        сбоях и :class:`RateLimitedError`, когда лимит не отступил.
        """

        await self.start()
        if self.client is None:  # pragma: no cover - start() гарантирует клиент
            raise TransportError("HTTP-клиент не инициализирован.")

        attempts = self.retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = await self.client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    data=data,
                    headers=headers,
                    auth=auth,
                    timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
                )
            except RETRY_EXCEPTIONS as exc:
                last_error = exc
                _LOGGER.warning(
                    "%s %s: %s (попытка %s/%s)",
                    method,
                    url,
                    type(exc).__name__,
                    attempt + 1,
                    attempts,
                )
                if attempt + 1 >= attempts:
                    break
                await asyncio.sleep(backoff_delay(attempt))
                continue

            if is_retryable_status(response.status_code):
                retry_after = parse_retry_after(response.headers)
                if attempt + 1 < attempts:
                    delay = backoff_delay(attempt, retry_after=retry_after)
                    _LOGGER.warning(
                        "%s %s -> %s, повтор через %.1f с (попытка %s/%s)",
                        method,
                        url,
                        response.status_code,
                        delay,
                        attempt + 1,
                        attempts,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise error_from_response(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    payload=safe_body(response),
                    method=method,
                    url=url,
                )

            return response

        raise TransportError(
            f"{method} {url} не выполнен после {attempts} попыток.",
            attempts=attempts,
            details={
                "reason": type(last_error).__name__ if last_error else "retryable status",
                "query": anonymize_query(params),
            },
        )

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        cacheable: bool = False,
    ) -> Any:
        """Выполнить запрос и вернуть разобранный JSON.

        Args:
            cacheable: разрешить кэш и дедупликацию (только для GET).

        Raises:
            AgentError: любая ошибка Метрики уже типизирована
                :func:`yandex_metrika_agent.errors.error_from_response`.
        """

        if cacheable and method.upper() == "GET":
            return await self._cached_json(url, params, headers, timeout)
        response = await self.request(
            method,
            url,
            params=params,
            json=json,
            headers=headers,
            timeout=timeout,
        )
        return payload_or_raise(response, method=method, url=url)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)

    def clear_cache(self) -> None:
        """Сбросить кэш ответов."""

        self._cache.clear()

    # --- Кэш и дедупликация --------------------------------------------------

    async def _cached_json(
        self,
        url: str,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        timeout: float | None,
    ) -> Any:
        """GET через LRU-кэш и single-flight."""

        key = canonical_key("GET", url, params)
        async with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                expires_at, cached = hit
                if expires_at > time.monotonic():
                    self._cache.move_to_end(key)
                    _LOGGER.debug("Кэш: %s", url)
                    return cached
                self._cache.pop(key, None)
            task = self._inflight.get(key)
            fresh = task is None or task.done()
            if fresh:
                task = asyncio.create_task(
                    self._fetch_json(key, url, params, headers, timeout),
                    name=f"metrika-get-{len(self._inflight)}",
                )
                self._inflight[key] = task
        if fresh:
            task.add_done_callback(lambda finished: self._forget(key, finished))
        return await asyncio.shield(task)

    def _forget(self, key: str, task: asyncio.Task[Any]) -> None:
        """Убрать завершённую задачу из реестра single-flight."""

        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)

    async def _fetch_json(
        self,
        key: str,
        url: str,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        timeout: float | None,
    ) -> Any:
        response = await self.request("GET", url, params=params, headers=headers, timeout=timeout)
        payload = payload_or_raise(response, method="GET", url=url)
        if self.cache_ttl > 0:
            async with self._lock:
                self._cache[key] = (time.monotonic() + self.cache_ttl, payload)
                self._cache.move_to_end(key)
                while len(self._cache) > self.cache_max_entries:
                    self._cache.popitem(last=False)
        return payload

    # --- Асинхронные отчёты --------------------------------------------------

    async def wait_async_report(
        self,
        task_id: str | int,
        *,
        timeout: float = REPORT_TIMEOUT,
        interval: float = REPORT_POLL_INTERVAL,
    ) -> Any:
        """Дождаться задачи асинхронного отчёта и вернуть его тело.

        Пакетный режим Метрики возвращает ``id`` задачи; статус читается
        ``GET /stat/v1/async/{id}``, результат — ``GET /stat/v1/async/{id}/result``.
        """

        deadline = time.monotonic() + timeout
        attempts = 0
        state = ""
        while True:
            attempts += 1
            status = await self.request_json("GET", f"/stat/v1/async/{task_id}")
            state = str(dig(status, ("status",)) or dig(status, ("state",)) or "").lower()
            if state in {"ready", "done", "finished", "success", "ok"}:
                return await self.request_json("GET", f"/stat/v1/async/{task_id}/result")
            if state in {"error", "failed", "canceled", "cancelled"}:
                raise ReportTimeoutError(
                    f"Задача отчёта {task_id} завершилась со статусом {state}.",
                    details={"task_id": task_id, "status": state},
                )
            if time.monotonic() >= deadline:
                raise ReportTimeoutError(
                    f"Отчёт {task_id} не сформирован за {timeout:.0f} с.",
                    details={
                        "task_id": task_id,
                        "status": state or "pending",
                        "attempts": attempts,
                    },
                )
            await asyncio.sleep(interval)


def safe_body(response: httpx.Response) -> Any:
    """Тело ответа: JSON, либо текст, либо ``None``."""

    try:
        return response.json()
    except ValueError:
        text = response.text
        return text[:2000] if text else None


def payload_or_raise(response: httpx.Response, *, method: str, url: str) -> Any:
    """Тело успешного ответа либо типизированная ошибка.

    Пустой ответ (204, пустое тело) — ``None``: Метрика так отвечает на
    изменение и удаление объектов.
    """

    body = safe_body(response)
    if response.status_code >= 400:
        raise error_from_response(
            status_code=response.status_code,
            headers=dict(response.headers),
            payload=body,
            method=method,
            url=url,
        )
    if response.status_code == 204 or not response.content:
        return None
    return body


def dig(payload: Any, path: tuple[str, ...]) -> Any:
    """Безопасно достать вложенное значение из разобранного JSON."""

    current = payload
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


__all__ = [
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT",
    "RATE_LIMIT_STATUS",
    "REPORT_TIMEOUT",
    "RETRY_EXCEPTIONS",
    "Transport",
    "backoff_delay",
    "canonical_key",
    "dig",
    "parse_retry_after",
    "payload_or_raise",
    "safe_body",
]
