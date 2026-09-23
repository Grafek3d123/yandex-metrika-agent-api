"""Шифрование AES-GCM (256 бит) для токенов и конфигурации.

Используется cryptography (CFFI/backend OpenSSL). Формат: версия + nonce + tag + ciphertext.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from yandex_metrika_agent.errors import TokenStorageError

NONCE_SIZE = 12
KEY_SIZE = 32
_FORMAT_VERSION = b"\x01"
_HKDF_INFO = b"yandex-metrika/token-store"


def generate_key() -> str:
    """Сгенерировать AES-GCM ключ (64 hex символа)."""

    return secrets.token_hex(KEY_SIZE)


def derive_key(secret: str, *, salt: bytes | None = None) -> tuple[bytes, bytes]:
    """Вывести ключ из пароля/секрета (HKDF-SHA256). Возвращает (ключ, соль)."""

    if not secret:
        raise TokenStorageError("Невозможно вывести ключ из пустого секрета.")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    raw_salt = salt or os.urandom(KEY_SIZE)
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=raw_salt,
        info=_HKDF_INFO,
    )
    return hkdf.derive(secret.encode("utf-8")), raw_salt


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Зашифровать AES-GCM. Бросает TokenStorageError при некорректном ключе."""

    _validate_key(key)
    nonce = os.urandom(NONCE_SIZE)
    aad = _FORMAT_VERSION
    sealed = AESGCM(key).encrypt(nonce, plaintext, aad)
    return _FORMAT_VERSION + nonce + sealed


def decrypt(payload: bytes, key: bytes) -> bytes:
    """Расшифровать AES-GCM. Повреждённые данные — TokenStorageError."""

    _validate_key(key)
    overhead = len(_FORMAT_VERSION) + NONCE_SIZE
    if len(payload) <= overhead:
        raise TokenStorageError("Данные слишком короткие: зашифрованный блок повреждён.")
    if not payload.startswith(_FORMAT_VERSION):
        raise TokenStorageError("Неизвестная версия формата шифрования.")
    nonce = payload[len(_FORMAT_VERSION) : overhead]
    sealed = payload[overhead:]
    try:
        return AESGCM(key).decrypt(nonce, sealed, _FORMAT_VERSION)
    except InvalidTag as exc:
        raise TokenStorageError(
            "Не удалось расшифровать: ключ не подходит или данные повреждены.",
        ) from exc


def hmac_equal(actual: bytes, expected: bytes) -> bool:
    """Сравнение с постоянным временем (для HMAC/подписей)."""

    return hmac.compare_digest(actual, expected)


def fingerprint(value: str) -> str:
    """Стабильный отпечаток значения для логов (без обратного восстановления)."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _validate_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != KEY_SIZE:
        raise TokenStorageError(
            "Требуется AES-GCM ключ длиной 32 байта (64 hex символа).",
            details={"key_size": len(key) if isinstance(key, bytes) else None},
        )
