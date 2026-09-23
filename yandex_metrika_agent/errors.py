"""Типизированные ошибки клиента Yandex Metrika.

Все ошибки — подклассы :class:`MetrikaError`, чтобы вызывающий код мог перехватить
их одним ``except`` и при этом различать категории. Ошибки несут структурированные
детали (код статуса, request-id, тело ответа).
"""

from __future__ import annotations

from typing import Any

#: Статусы, при которых запрос можно повторить (сбои на стороне сервиса).
RETRYABLE_STATUS: frozenset[int] = frozenset({500, 502, 503, 504})

#: Статусы превышения частоты запросов.
RATE_LIMIT_STATUS: frozenset[int] = frozenset({420, 429})

_REQUEST_ID_HEADERS: tuple[str, ...] = (
    "x-request-id",
    "x-requestid",
    "x-trace-id",
    "request-id",
)


class MetrikaError(Exception):
    """Базовая ошибка клиента Metrika."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление для вывода в JSON."""

        return {
            "error": type(self).__name__,
            "message": self.message,
            "details": self.details,
        }

    def __str__(self) -> str:
        return self.message


class ConfigError(MetrikaError):
    """Отсутствует или некорректна конфигурация (клиент, ключ, URI)."""


class ValidationError(MetrikaError):
    """Данные не прошли проверку перед отправкой в API."""


class AuthError(MetrikaError):
    """OAuth-ошибка: некорректный код, токена нет/он истёк/отозван, 401/403."""


class TokenStorageError(MetrikaError):
    """Ошибка хранилища токенов: нет ключа шифрования, файл повреждён."""


class NotFoundError(MetrikaError):
    """Объект не найден или недоступен текущему пользователю (404)."""


class ScopeError(MetrikaError):
    """Недостаточно прав/скоупов либо операция недоступна по условиям API."""


class TransportError(MetrikaError):
    """HTTP-ошибка после исчерпания повторов (таймаут, 5xx, соединение)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        attempts: int = 0,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.status_code = status_code
        self.attempts = attempts


class ApiError(MetrikaError):
    """Metrica вернула ошибку бизнес-логики (4xx с описанием)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        request_id: str | None = None,
        body: Any = None,
    ) -> None:
        details: dict[str, Any] = {"status_code": status_code}
        if request_id:
            details["request_id"] = request_id
        if body is not None:
            details["body"] = body
        super().__init__(message, details=details)
        self.status_code = status_code
        self.request_id = request_id
        self.body = body


class RateLimitedError(ApiError):
    """Превышен лимит запросов (420/429)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 420,
        retry_after: float | None = None,
        request_id: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message, status_code=status_code, request_id=request_id, body=body)
        self.retry_after = retry_after
        self.details["retry_after"] = retry_after


class ReportTimeoutError(TransportError):
    """Отчёт не сформирован за отведённое время (async-задача не завершилась)."""


def is_retryable_status(status_code: int) -> bool:
    """Повторять ли запрос при таком статусе."""

    return status_code in RETRYABLE_STATUS or status_code in RATE_LIMIT_STATUS


def extract_request_id(headers: dict[str, str]) -> str | None:
    """Достать идентификатор запроса из заголовков ответа."""

    lowered = {str(key).lower(): value for key, value in headers.items()}
    for name in _REQUEST_ID_HEADERS:
        value = lowered.get(name)
        if value:
            return str(value)
    return None


def extract_retry_after(headers: dict[str, str]) -> float | None:
    """Разобрать заголовок ``Retry-After`` (секунды)."""

    lowered = {str(key).lower(): value for key, value in headers.items()}
    raw = lowered.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return None


# --- Извлечение сообщения об ошибке из ответа ----------------------------------

_MESSAGE_PATHS: tuple[tuple[str, ...], ...] = (
    ("errors", "0", "message"),
    ("errors", "0", "description"),
    ("error", "message"),
    ("error", "description"),
    ("description",),
    ("message",),
    ("detail",),
)

_CODE_PATHS: tuple[tuple[str, ...], ...] = (
    ("errors", "0", "code"),
    ("error", "code"),
    ("code",),
)


def _dig(payload: Any, path: tuple[str, ...]) -> Any:
    current = payload
    for part in path:
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def extract_error_code(payload: Any) -> str | None:
    """Машинный код ошибки Metrica, если он есть в ответе."""

    for path in _CODE_PATHS:
        value = _dig(payload, path)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return None


def extract_error_message(payload: Any, *, fallback: str) -> str:
    """Самое понятное сообщение об ошибке из известных форматов ответа."""

    for path in _MESSAGE_PATHS:
        value = _dig(payload, path)
        if isinstance(value, str) and value.strip():
            code = extract_error_code(payload)
            return f"{value.strip()} (code: {code})" if code else value.strip()
    return fallback


def error_from_response(
    *,
    status_code: int,
    headers: dict[str, str],
    payload: Any,
    method: str,
    url: str,
) -> MetrikaError:
    """Преобразовать неуспешный ответ Metrica в ошибку нужной категории."""

    request_id = extract_request_id(headers)
    fallback = f"{method} {url} вернул {status_code}"
    message = extract_error_message(payload, fallback=fallback)

    if status_code in RATE_LIMIT_STATUS:
        return RateLimitedError(
            message,
            status_code=status_code,
            retry_after=extract_retry_after(headers),
            request_id=request_id,
            body=payload,
        )
    if status_code in (401, 403):
        return AuthError(
            f"{message} Проверьте срок действия токена и скоупы OAuth-клиента.",
            details={"status_code": status_code, "request_id": request_id, "body": payload},
        )
    if status_code == 404:
        return NotFoundError(
            message,
            details={"status_code": status_code, "request_id": request_id, "body": payload},
        )
    return ApiError(message, status_code=status_code, request_id=request_id, body=payload)
