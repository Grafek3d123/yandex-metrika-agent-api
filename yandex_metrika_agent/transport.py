"""HTTP-транспорт с повторами, лимитом частоты, кэшем и дедупликацией.

Повторы: экспоненциальная задержка с джиттером для 420/429/5xx и сетевых ошибок,
учёт заголовка ``Retry-After``. Destructive-методы (POST, DELETE) повторяются
только при явном отказе сервиса в обработке запроса (420/429) или при сбое
соединения до передачи — повтор не должен создать дубликат цели или повторно
удалить объект.

Кэш и дедупликация нужны агенту: модель может дважды за одно диалог спросить
одинаковый отчёт. Одинаковый GET выполняется один раз (single-flight), ответ
короткое время отдаётся из кэша. Кэш изолирован по ``connection_id``: данные
пользователя A не могут достаться пользователю B.

Ограничения частоты (:class:`RateLimiter`) соответствуют официальным квотам
(``https://yandex.com/dev/metrika/ru/intro/quotas``):

* 30 запросов/сек с одного IP — контролируется консервативно на уровне
  приложения (другие процессы того же IP учёту не подлежат, это ограничение
  среды, а не библиотеки);
* 3 параллельных запроса на пользователя — семафор;
* 5000 запросов/сутки на пользователя — скользящее суточное окно;
* 200 запросов/5 минут к ``/stat/v1/data`` — отдельное окно группы
  ``report_read``.

Группы методов (client_read / client_write / report_read) разделяются по
URL и HTTP-методу: ``/stat/...`` — отчёты, ``/management/...`` — чтение или
запись.

Секреты в логи не пишутся: параметры проходят через ``anonymize_query``,
заголовок ``Authorization`` исключается полностью.
"""

from __future__ import annotations

import asyncio
import json as jsonlib
import random
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

import httpx

from yandex_metrika_agent.errors import (
    RATE_LIMIT_STATUS,
    RateLimitedError,
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

#: Официальные квоты API (intro/quotas).
QUOTA_PER_SECOND_PER_IP = 30
QUOTA_PARALLEL_PER_USER = 3
QUOTA_PER_DAY_PER_USER = 5000
QUOTA_REPORTS_PER_5MIN = 200
REPORT_WINDOW_SECONDS = 300.0
DAY_SECONDS = 86_400.0

#: Методы, повтор которых безопасен по смыслу HTTP (PUT идемпотентен).
IDEMPOTENT_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "PUT"})

#: Префиксы отчётных методов (группа ``report_read``).
_REPORT_PREFIXES: tuple[str, ...] = ("/stat/v1/data", "/stat/v1/async", "/stat/v1/visits")

RETRY_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)

#: Сбои до передачи запроса — повтор безопасен даже для POST/DELETE.
_PRE_SEND_EXCEPTIONS = (httpx.ConnectError,)

_LOGGER = get_logger("transport")


def is_report_request(method: str, url: str) -> bool:
    """Относится ли запрос к группе ``report_read`` (квота 200/5 мин)."""

    return any(prefix in url for prefix in _REPORT_PREFIXES)


def should_retry(
    method: str, *, attempt_exception: type[BaseException] | None, status: int
) -> bool:
    """Разрешён ли повтор запроса данного метода.

    Идемпотентные методы (GET/HEAD/OPTIONS/PUT) повторяются при любой
    повторямой ситуации. POST и DELETE — только когда сервис явно отказал
    в обработке (420/429) или соединение не было установлено: повтор не
    должен создать вторую цель или повторно удалить объект.
    """

    if status in RATE_LIMIT_STATUS:
        return True
    if method.upper() in IDEMPOTENT_METHODS:
        return is_retryable_status(status) or attempt_exception is not None
    if attempt_exception is not None:
        return issubclass(attempt_exception, _PRE_SEND_EXCEPTIONS)
    return False


def backoff_delay(attempt: int, *, retry_after: float | None = None) -> float:
    """Экспоненциальная задержка перед повтором (с джиттером)."""

    if attempt < 0:
        raise ValueError("attempt не может быть отрицательным")
    if retry_after is not None and retry_after > 0:
        return min(float(retry_after), MAX_DELAY)
    delay = BASE_DELAY * (2**attempt)
    # Джиттер backoff'а — не криптография, обычный random уместен.
    return float(min(delay * (1 + random.uniform(-JITTER, JITTER)), MAX_DELAY))  # noqa: S311


def parse_retry_after(headers: httpx.Headers | dict[str, str]) -> float | None:
    """Разобрать ``Retry-After`` (секунды или HTTP-date)."""

    raw: str | None = None
    for key, value in headers.items():
        if key.lower() == "retry-after":
            raw = value
            break
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
    return max(when.timestamp() - time.time(), 0.0)


def canonical_key(
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    scope: str = "",
) -> str:
    """Устойчивый ключ запроса для кэша и дедупликации.

    ``scope`` (``connection_id``) обязателен для изоляции: кэш одного
    подключения не должен отдавать ответы другому пользователю.
    """

    base = f"{method.upper()}|{url}"
    if params:
        encoded = jsonlib.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        base = f"{base}|{encoded}"
    return f"{scope}::{base}" if scope else base


class RateLimiter:
    """Ограничитель частоты запросов по официальным квотам Метрики.

    Args:
        per_second: запросов в секунду (консервативно от IP-квоты 30).
        max_concurrent: параллельных запросов на пользователя (квота 3).
        per_day: запросов в сутки на пользователя (квота 5000).
        reports_per_window: запросов к Reports API за окно (квота 200).
        window_seconds: длительность окна отчётной квоты, сек.
        clock: источник времени (для тестов подменяется).

    Примечание: квота 30/сек считается Метрикой по IP. Надёжно учесть
    запросы других процессов того же IP из приложения невозможно, поэтому
    лимитер защищает лишь своё приложение и выставлен консервативно.
    """

    def __init__(
        self,
        *,
        per_second: int = QUOTA_PER_SECOND_PER_IP - 5,
        max_concurrent: int = QUOTA_PARALLEL_PER_USER,
        per_day: int = QUOTA_PER_DAY_PER_USER,
        reports_per_window: int = QUOTA_REPORTS_PER_5MIN - 20,
        window_seconds: float = REPORT_WINDOW_SECONDS,
        clock: Any = time.monotonic,
    ) -> None:
        if per_second < 1 or max_concurrent < 1 or per_day < 1 or reports_per_window < 1:
            raise ValueError("Лимиты ограничителя должны быть положительными")
        self.per_second = per_second
        self.per_day = per_day
        self.reports_per_window = reports_per_window
        self.window_seconds = window_seconds
        self._clock = clock
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()
        self._recent: deque[float] = deque()
        self._daily: deque[float] = deque()
        self._reports: deque[float] = deque()

    async def acquire(self, *, report: bool) -> None:
        """Разрешить один запрос; ждать при превышении секундного окна.

        Raises:
            RateLimitedError: суточная квота исчерпана (ждать до полуночи
                бессмысленно) или отчётная квота 200/5 мин превышена —
                в ``retry_after`` — сколько ждать.
        """

        async with self._lock:
            now = self._clock()
            self._prune(self._recent, now, 1.0)
            self._prune(self._daily, now, DAY_SECONDS)
            self._prune(self._reports, now, self.window_seconds)
            if len(self._daily) >= self.per_day:
                wait = self._daily[0] + DAY_SECONDS - now
                raise RateLimitedError(
                    f"Суточная квота API ({self.per_day} запросов) исчерпана.",
                    retry_after=round(wait, 1),
                    body={"quota": "per_day", "limit": self.per_day},
                )
            if report and len(self._reports) >= self.reports_per_window:
                wait = self._reports[0] + self.window_seconds - now
                raise RateLimitedError(
                    f"Квота Reports API ({self.reports_per_window} за "
                    f"{self.window_seconds:.0f} с) исчерпана.",
                    retry_after=round(wait, 1),
                    body={"quota": "reports_per_5min", "limit": self.reports_per_window},
                )
            if len(self._recent) >= self.per_second:
                wait = self._recent[0] + 1.0 - now
                if wait > 0:
                    await asyncio.sleep(wait)
                    now = self._clock()
                    self._prune(self._recent, now, 1.0)
            self._recent.append(now)
            self._daily.append(now)
            if report:
                self._reports.append(now)

    def release(self) -> None:
        """Освободить слот параллельности."""

        self._semaphore.release()

    async def slot(self, *, report: bool) -> None:
        """Занять параллельный слот и пройти частотные окна."""

        await self._semaphore.acquire()
        try:
            await self.acquire(report=report)
        except BaseException:
            self.release()
            raise

    def _prune(self, window: deque[float], now: float, span: float) -> None:
        while window and now - window[0] >= span:
            window.popleft()

    def snapshot(self) -> dict[str, Any]:
        """Текущая заполненность окон (для диагностики)."""

        return {
            "recent_1s": len(self._recent),
            "daily": len(self._daily),
            "reports_window": len(self._reports),
            "per_second": self.per_second,
            "per_day": self.per_day,
            "reports_per_window": self.reports_per_window,
        }


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
        connection_id: подключение (пользователь) — изолирует кэш и входит в
            ключ single-flight.
        rate_limiter: ограничитель частоты; по умолчанию создаётся с
            консервативными квотами Метрики.
        enforce_quotas: выключить создание ограничителя (``False``) — только
            для тестов и стендов.
    """

    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    base_url: str | None = None
    default_headers: dict[str, str] = field(default_factory=dict)
    client: httpx.AsyncClient | None = field(default=None, repr=False)
    cache_ttl: float = 0.0
    cache_max_entries: int = 256
    connection_id: str = "default"
    rate_limiter: RateLimiter | None = field(default=None, repr=False)
    enforce_quotas: bool = True

    _owned: httpx.AsyncClient | None = field(default=None, repr=False)
    _cache: OrderedDict[str, tuple[float, Any]] = field(default_factory=OrderedDict, repr=False)
    _inflight: dict[str, asyncio.Task[Any]] = field(default_factory=dict, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _stats: dict[str, int] = field(
        default_factory=lambda: {
            "requests": 0,
            "retries": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "deduplicated": 0,
        },
        repr=False,
    )
    _total_requests: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if self.timeout <= 0:
            raise ValueError("timeout должен быть больше 0")
        if self.retries < 0:
            raise ValueError("retries не может быть отрицательным")
        if self.cache_ttl < 0:
            raise ValueError("cache_ttl не может быть отрицательным")
        if self.cache_max_entries < 1:
            raise ValueError("cache_max_entries должен быть больше 0")
        if self.rate_limiter is None and self.enforce_quotas:
            self.rate_limiter = RateLimiter()

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
        report = is_report_request(method, url)

        for attempt in range(attempts):
            if self.rate_limiter is not None:
                await self.rate_limiter.slot(report=report)
            self._total_requests += 1
            try:
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
                finally:
                    if self.rate_limiter is not None:
                        self.rate_limiter.release()
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
                retryable = should_retry(method, attempt_exception=type(exc), status=0)
                if attempt + 1 >= attempts or not retryable:
                    break
                self._stats["retries"] += 1
                await asyncio.sleep(backoff_delay(attempt))
                continue

            if is_retryable_status(response.status_code):
                retry_after = parse_retry_after(response.headers)
                retryable = should_retry(
                    method, attempt_exception=None, status=response.status_code
                )
                if attempt + 1 < attempts and retryable:
                    delay = backoff_delay(attempt, retry_after=retry_after)
                    self._stats["retries"] += 1
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
        """GET через LRU-кэш и single-flight (ключ изолирован по подключению)."""

        key = canonical_key("GET", url, params, scope=self.connection_id)
        async with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                expires_at, cached = hit
                if expires_at > time.monotonic():
                    self._cache.move_to_end(key)
                    self._stats["cache_hits"] += 1
                    _LOGGER.debug("Кэш: %s", url)
                    return cached
                self._cache.pop(key, None)
            task = self._inflight.get(key)
            fresh = task is None or task.done()
            if fresh:
                self._stats["cache_misses"] += 1
                task = asyncio.create_task(
                    self._fetch_json(key, url, params, headers, timeout),
                    name=f"metrika-get-{len(self._inflight)}",
                )
                self._inflight[key] = task
            else:
                # Одинаковый GET уже выполняется: дожидаемся того же запроса.
                self._stats["deduplicated"] += 1
        if task is None:  # pragma: no cover - под lock задача создана
            raise TransportError("Не удалось поставить GET в очередь single-flight.")
        if fresh:
            task.add_done_callback(lambda finished: self._forget(key, finished))
        return await asyncio.shield(task)

    def get_stats(self) -> dict[str, int]:
        """Сводка для диагностики: повторы, попадания в кэш, дедупликации.

        Ключи: ``requests`` (все выполненные HTTP-запросы), ``retries``
        (повторы после ошибок), ``cache_hits``/``cache_misses`` (попадания и
        промахи кэша GET), ``deduplicated`` (запросы, дождавшиеся уже
        выполняющегося одинакового GET).
        """

        self._stats["requests"] = self._total_requests
        return dict(self._stats)

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
    "DAY_SECONDS",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT",
    "IDEMPOTENT_METHODS",
    "QUOTA_PARALLEL_PER_USER",
    "QUOTA_PER_DAY_PER_USER",
    "QUOTA_PER_SECOND_PER_IP",
    "QUOTA_REPORTS_PER_5MIN",
    "RATE_LIMIT_STATUS",
    "REPORT_TIMEOUT",
    "REPORT_WINDOW_SECONDS",
    "RETRY_EXCEPTIONS",
    "RateLimiter",
    "Transport",
    "backoff_delay",
    "canonical_key",
    "dig",
    "is_report_request",
    "parse_retry_after",
    "payload_or_raise",
    "safe_body",
    "should_retry",
]
