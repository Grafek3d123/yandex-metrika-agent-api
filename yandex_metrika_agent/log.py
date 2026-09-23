"""Логирование без секретов.

Модуль называется ``log``, а не ``logging``: одноимённый модуль в пакете
затенял бы стандартную библиотеку и ломал импорты у всех, кто импортирует пакет.

Правила:

* в лог не попадают OAuth-токены, ``client_secret``, коды авторизации и пароли;
* значения параметров запросов проходят через :func:`anonymize_query`;
* заголовки ``Authorization`` в лог не пишутся вообще.
"""

from __future__ import annotations

import logging as std_logging
from collections.abc import Mapping
from typing import Any

#: Ключи запросов/форм, значения которых нельзя писать в лог.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "access_token",
        "refresh_token",
        "revoke_token",
        "token",
        "client_secret",
        "code",
        "code_verifier",
        "password",
        "secret",
        "authorization",
        "set-cookie",
    }
)

MASK = "***"
MAX_VALUE_LENGTH = 160

_configured = False


def setup_logging(level: str = "INFO") -> None:
    """Настроить формат вывода один раз для процесса.

    Уровень берётся из конфигурации; неизвестное значение приводит к INFO.
    """

    global _configured
    numeric = std_logging.getLevelNamesMapping().get(level.upper(), std_logging.INFO)
    std_logging.basicConfig(
        level=numeric,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Сторонние библиотеки не должны засыпать вывод своими debug-строками.
    std_logging.getLogger("httpx").setLevel(std_logging.WARNING)
    std_logging.getLogger("httpcore").setLevel(std_logging.WARNING)
    _configured = True


def get_logger(name: str) -> std_logging.Logger:
    """Достать логгер пакета (с ленивой инициализацией настроек)."""

    if not _configured:
        setup_logging()
    return std_logging.getLogger(f"yandex_metrika.{name}")


def is_sensitive_key(key: str) -> bool:
    """Относится ли имя параметра к секретам."""

    lowered = str(key).lower()
    if lowered in SENSITIVE_KEYS:
        return True
    return any(marker in lowered for marker in ("token", "secret", "password"))


def _mask_value(value: Any) -> Any:
    text = value if isinstance(value, str) else str(value)
    if len(text) > MAX_VALUE_LENGTH:
        return f"{text[:MAX_VALUE_LENGTH]}…"
    return value


def anonymize_query(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Копия параметров запроса, пригодная для журнала.

    Секретные значения заменяются на ``***``, остальные укорачиваются.
    """

    if not params:
        return {}
    safe: dict[str, Any] = {}
    for key, value in params.items():
        if is_sensitive_key(str(key)):
            safe[str(key)] = MASK
        elif isinstance(value, (list, tuple, set)):
            safe[str(key)] = [_mask_value(item) for item in value]
        else:
            safe[str(key)] = _mask_value(value)
    return safe


def anonymize_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Копия заголовков для журнала: ``Authorization`` и куки скрыты."""

    if not headers:
        return {}
    safe: dict[str, str] = {}
    for key, value in headers.items():
        name = str(key)
        safe[name] = MASK if is_sensitive_key(name) else _mask_value(value)
    return safe
