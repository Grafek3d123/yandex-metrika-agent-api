"""Конфигурация агента.

Значения берутся из переменных окружения (и .env, если установлен python-dotenv).
Пути конфигурации — по XDG, а не в домашней директории.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from yandex_metrika_agent.errors import ConfigError

DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3
DEFAULT_REDIRECT_URI = "http://localhost:8765/callback"

_HEX_DIGITS = set("0123456789abcdefABCDEF")


def _load_dotenv() -> None:
    """Загрузить .env из текущей директории, если python-dotenv доступен."""

    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - зависимость опциональна
        return
    load_dotenv(override=False)


def config_dir() -> Path:
    """Каталог конфигурации (XDG_CONFIG_HOME или ~/.config)."""

    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "metrika-agent"


def token_dir() -> Path:
    """Каталог зашифрованных токенов."""

    override = os.environ.get("METRIKA_TOKEN_DIR")
    if override:
        return Path(override).expanduser()
    return config_dir() / "tokens"


def parse_token_key(raw: str | None) -> bytes | None:
    """Разобрать AES-GCM ключ из hex (32 байта). Пустое значение — None."""

    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) != 64 or not set(value) <= _HEX_DIGITS:
        raise ConfigError(
            "METRIKA_TOKEN_KEY должен содержать 64 hex символа (AES-GCM ключ).",
            details={"length": len(value)},
        )
    return bytes.fromhex(value)


@dataclass(frozen=True)
class Settings:
    """Настройки агента."""

    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = DEFAULT_REDIRECT_URI
    token_key: bytes | None = field(default=None, repr=False, compare=False)
    token_dir: Path = field(default_factory=token_dir)
    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    log_level: str = "INFO"
    env: str = "production"

    @property
    def is_configured(self) -> bool:
        """Есть ли минимум для OAuth (клиент)."""

        return bool(self.client_id)

    def require_oauth_client(self) -> None:
        """Ошибка, если OAuth-клиент не настроен."""

        if not self.client_id:
            raise ConfigError(
                "Не задан YANDEX_CLIENT_ID. Зарегистрируйте собственный OAuth клиент "
                "и укажите его в .env.",
            )
        if not self.client_secret:
            raise ConfigError(
                "Не задан YANDEX_CLIENT_SECRET для OAuth клиента.",
            )

    def require_token_key(self) -> None:
        """Ошибка, если нет ключа шифрования хранилища."""

        if self.token_key is None:
            raise ConfigError(
                "Не задан METRIKA_TOKEN_KEY. Сгенерируйте ключ: "
                "python -m yandex_metrika_agent.tools genkey",
            )


def _parse_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должен быть числом.", details={"value": raw}) from exc
    if value <= 0:
        raise ConfigError(f"{name} должен быть больше 0.", details={"value": raw})
    return value


def _parse_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должен быть целым числом.", details={"value": raw}) from exc
    if value < 0:
        raise ConfigError(f"{name} не может быть отрицательным.", details={"value": raw})
    return value


def load_settings(*, dotenv: bool = True) -> Settings:
    """Собрать настройки из окружения."""

    if dotenv:
        _load_dotenv()

    return Settings(
        client_id=os.environ.get("YANDEX_CLIENT_ID", "").strip(),
        client_secret=os.environ.get("YANDEX_CLIENT_SECRET", "").strip(),
        redirect_uri=os.environ.get("YANDEX_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip(),
        token_key=parse_token_key(os.environ.get("METRIKA_TOKEN_KEY")),
        token_dir=token_dir(),
        timeout=_parse_float("METRIKA_HTTP_TIMEOUT", DEFAULT_TIMEOUT),
        retries=_parse_int("METRIKA_HTTP_RETRIES", DEFAULT_RETRIES),
        log_level=os.environ.get("METRIKA_LOG_LEVEL", "INFO").strip().upper(),
        env=os.environ.get("METRIKA_ENV", "production").strip().lower(),
    )
