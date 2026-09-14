"""Зашифрованное хранилище OAuth-токенов.

Один файл на подключение (``connection_id``): пользователь или сайт, к которому
подключена Метрика. Файлы лежат в каталоге из :func:`yandex_metrika_agent.config.token_dir`,
содержимое шифруется AES-GCM (:mod:`yandex_metrika_agent.crypto`).

Токены не попадают в логи и в представления: :meth:`TokenRecord.public`
возвращает только неверительную информацию (срок действия, права, отпечаток).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from yandex_metrika_agent import crypto
from yandex_metrika_agent.errors import AuthError, TokenStorageError
from yandex_metrika_agent.log import get_logger

#: Считаем токен требующим обновления, если до истечения осталось меньше секунд.
REFRESH_LEEWAY = 120.0

_PAYLOAD_VERSION = 1
_LOGGER = get_logger("tokens")


@dataclass(frozen=True)
class TokenRecord:
    """Пара токенов и метаданные подключения.

    Поля с токенами намеренно скрыты из ``repr`` — случайный вывод в лог или
    в ``repr()`` не должен раскрывать секреты.
    """

    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    token_type: str = "bearer"  # noqa: S105  - это OAuth token_type, не пароль
    expires_at: float | None = None
    scopes: tuple[str, ...] = ()
    connection_id: str = "default"
    user_login: str | None = None
    created_at: float = field(default_factory=time.time)

    @property
    def expires_in(self) -> float | None:
        """Секунд до истечения доступа (``None`` — срок не известен)."""

        if self.expires_at is None:
            return None
        return self.expires_at - time.time()

    def is_expired(self, *, leeway: float = REFRESH_LEEWAY) -> bool:
        """Нужен ли обновление: истёк либо скоро истечёт (с запасом)."""

        remaining = self.expires_in
        if remaining is None:
            return False
        return remaining <= leeway

    def with_tokens(
        self,
        *,
        access_token: str,
        refresh_token: str | None,
        expires_at: float | None,
        scopes: tuple[str, ...] | None = None,
    ) -> TokenRecord:
        """Новая запись с заменёнными токенами (полезно после refresh)."""

        return replace(
            self,
            access_token=access_token,
            refresh_token=refresh_token if refresh_token is not None else self.refresh_token,
            expires_at=expires_at,
            scopes=scopes if scopes is not None else self.scopes,
        )

    def public(self) -> dict[str, Any]:
        """Неверительное описание подключения: для CLI и ответов агенту."""

        return {
            "connection_id": self.connection_id,
            "user_login": self.user_login,
            "scopes": list(self.scopes),
            "token_type": self.token_type,
            "access_token_fingerprint": crypto.fingerprint(self.access_token),
            "has_refresh_token": bool(self.refresh_token),
            "expires_at": self.expires_at,
            "expires_in": None if self.expires_at is None else round(self.expires_in or 0),
            "expired": self.is_expired(leeway=0.0),
            "needs_refresh": self.is_expired(),
        }

    def to_payload(self) -> dict[str, Any]:
        """Словарь для шифрования на диске."""

        return {
            "version": _PAYLOAD_VERSION,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
            "scopes": list(self.scopes),
            "connection_id": self.connection_id,
            "user_login": self.user_login,
            "created_at": self.created_at,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TokenRecord:
        """Разобрать словарь с диска. Бросает :class:`TokenStorageError` при мусоре."""

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise TokenStorageError("В сохранённом токене нет access_token.")
        scopes_raw = payload.get("scopes") or ()
        if not isinstance(scopes_raw, (list, tuple)):
            raise TokenStorageError("Поле scopes в сохранённом токене повреждено.")
        expires_raw = payload.get("expires_at")
        return cls(
            access_token=access_token,
            refresh_token=_optional_str(payload.get("refresh_token")),
            token_type=_optional_str(payload.get("token_type")) or "bearer",
            expires_at=None if expires_raw is None else float(expires_raw),
            scopes=tuple(str(item) for item in scopes_raw),
            connection_id=_optional_str(payload.get("connection_id")) or "default",
            user_login=_optional_str(payload.get("user_login")),
            created_at=float(payload.get("created_at") or time.time()),
        )


def _optional_str(value: Any) -> str | None:
    """Строка или ``None`` (пустые значения считаем отсутствующими)."""

    if isinstance(value, str) and value:
        return value
    return None


class TokenStore(Protocol):
    """Интерфейс хранилища токенов.

    Реализации: файл на диске (сейчас), БД или KMS — позже. Клиент зависит
    только от этого протокола.
    """

    def get(self, connection_id: str) -> TokenRecord | None: ...

    def save(self, record: TokenRecord) -> None: ...

    def delete(self, connection_id: str) -> bool: ...

    def list_connections(self) -> list[str]: ...


class EncryptedFileStore:
    """Хранилище токенов в файлах, шифрованных AES-GCM.

    Args:
        directory: каталог для файлов (создаётся при необходимости).
        key: 32-байтный ключ. Если не задан, используется переменная
            окружения ``METRIKA_TOKEN_KEY`` (hex, 64 символа).
    """

    def __init__(self, directory: Path | str, key: bytes | None = None) -> None:
        self.directory = Path(directory).expanduser()
        self._key = key if key is not None else crypto_env_key()
        self._cache: dict[str, TokenRecord] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # --- Публичный интерфейс -------------------------------------------------

    def get(self, connection_id: str) -> TokenRecord | None:
        """Запись подключения или ``None``, если подключения нет."""

        cached = self._cache.get(connection_id)
        if cached is not None:
            return cached
        path = self._path(connection_id)
        if not path.exists():
            return None
        record = self._read(path, connection_id)
        self._cache[connection_id] = record
        return record

    def save(self, record: TokenRecord) -> None:
        """Записать (или перезаписать) токен подключения."""

        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(record.connection_id)
        plaintext = json.dumps(record.to_payload(), ensure_ascii=False).encode("utf-8")
        payload = crypto.encrypt(plaintext, self._key)
        _atomic_write(path, payload)
        self._cache[record.connection_id] = record
        _LOGGER.info("Токен сохранён: connection=%s", record.connection_id)

    def delete(self, connection_id: str) -> bool:
        """Удалить подключение. ``True`` — файл существовал."""

        self._cache.pop(connection_id, None)
        path = self._path(connection_id)
        if not path.exists():
            return False
        path.unlink()
        _LOGGER.info("Токен удалён: connection=%s", connection_id)
        return True

    def list_connections(self) -> list[str]:
        """Идентификаторы сохранённых подключений."""

        if not self.directory.exists():
            return []
        # Обратная карта: fingerprint(connection_id) -> connection_id.
        fingerprints = {crypto.fingerprint(cid): cid for cid in self._cache}
        found: list[str] = []
        for path in sorted(self.directory.glob("*.token")):
            found.append(fingerprints.get(path.stem, path.stem))
        return found

    # --- Автоматическое продление -------------------------------------------

    async def get_valid(
        self,
        connection_id: str,
        refresh: Callable[[TokenRecord], TokenRecord] | Callable[[TokenRecord], Any],
    ) -> TokenRecord:
        """Вернуть действующий токен, при необходимости обновив его.

        Args:
            connection_id: подключение.
            refresh: функция, получающая запись с просроченным access-токеном и
                возвращающая (возможно, await-ом) новую запись.

        Raises:
            AuthError: токена нет, либо он истёк и refresh-токена нет.
        """

        record = self.get(connection_id)
        if record is None:
            raise AuthError(
                f"Подключение {connection_id!r} не авторизовано.",
                details={"connection_id": connection_id},
            )
        if not record.is_expired():
            return record
        if not record.refresh_token:
            raise AuthError(
                "OAuth-токен истёк, refresh-токена нет. Требуется повторная авторизация.",
                details={"connection_id": connection_id, **record.public()},
            )
        async with self.lock(connection_id):
            current = self.get(connection_id) or record
            if not current.is_expired():  # продлили параллельно
                return current
            updated = refresh(current)
            if asyncio.iscoroutine(updated):
                updated = await updated
            if not isinstance(updated, TokenRecord):
                raise TokenStorageError("refresh вернул не TokenRecord.")
            self.save(updated)
            return updated

    def lock(self, connection_id: str) -> asyncio.Lock:
        """Мьютекс продления для одного подключения."""

        lock = self._locks.get(connection_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[connection_id] = lock
        return lock

    # --- Внутреннее ----------------------------------------------------------

    def _path(self, connection_id: str) -> Path:
        """Путь файла подключения по отпечатку идентификатора."""

        if not connection_id or not connection_id.strip():
            raise TokenStorageError("Пустой идентификатор подключения.")
        digest = crypto.fingerprint(connection_id.strip())
        return self.directory / f"{digest}.token"

    def _read(self, path: Path, connection_id: str) -> TokenRecord:
        try:
            payload = crypto.decrypt(path.read_bytes(), self._key)
        except TokenStorageError as exc:
            raise TokenStorageError(
                f"Не удалось прочитать токен подключения {connection_id!r}.",
                details={"path": str(path), "reason": exc.message},
            ) from exc
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TokenStorageError(
                f"Файл токена подключения {connection_id!r} повреждён.",
                details={"path": str(path)},
            ) from exc
        if not isinstance(data, dict):
            raise TokenStorageError(f"Файл токена подключения {connection_id!r} повреждён.")
        record = TokenRecord.from_payload(data)
        return record if record.connection_id == connection_id else replace(
            record, connection_id=connection_id
        )


def crypto_env_key() -> bytes:
    """Ключ шифрования из ``METRIKA_TOKEN_KEY`` (hex 64 символа)."""

    from yandex_metrika_agent.config import parse_token_key

    key = parse_token_key(os.environ.get("METRIKA_TOKEN_KEY"))
    if key is None:
        raise TokenStorageError(
            "Для хранилища токенов нужен ключ: задайте METRIKA_TOKEN_KEY "
            "(64 hex символа) или передайте key в EncryptedFileStore.",
        )
    return key


def default_store(
    directory: Path | str | None = None, key: bytes | None = None
) -> EncryptedFileStore:
    """Хранилище по умолчанию: каталог и ключ из настроек пакета."""

    from yandex_metrika_agent.config import token_dir

    return EncryptedFileStore(Path(directory) if directory is not None else token_dir(), key=key)


def save_token(
    record: TokenRecord,
    *,
    store: TokenStore | None = None,
    directory: Path | str | None = None,
) -> None:
    """Сохранить токен в хранилище.

    Args:
        record: запись с токенами.
        store: готовое хранилище; по умолчанию — зашифрованные файлы в
            каталоге настроек.
        directory: явный каталог хранилища (удобно в тестах).
    """

    (store or default_store(directory)).save(record)


def get_token(
    connection_id: str = "default",
    *,
    store: TokenStore | None = None,
    directory: Path | str | None = None,
) -> TokenRecord | None:
    """Прочитать токен подключения (``None`` — подключения нет)."""

    return (store or default_store(directory)).get(connection_id)


def _atomic_write(path: Path, payload: bytes) -> None:
    """Запись через временный файл + rename, права 0600 где это возможно."""

    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows может не поддержать chmod — молча пропускаем.
        with contextlib.suppress(OSError):
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp_path, path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise TokenStorageError(
            f"Не удалось записать файл токена {path.name}.",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


__all__ = [
    "REFRESH_LEEWAY",
    "EncryptedFileStore",
    "TokenRecord",
    "TokenStore",
    "crypto_env_key",
    "default_store",
    "get_token",
    "save_token",
]
