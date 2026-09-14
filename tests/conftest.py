"""Общие фикстуры тестов: ключи, хранилище, клиент с мок-транспортом."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.crypto import generate_key
from yandex_metrika_agent.tokens import EncryptedFileStore, TokenRecord

TEST_TOKEN = "test-oauth-token-abc123"


@pytest.fixture()
def token_key() -> bytes:
    """Валидный AES-GCM ключ (32 байта)."""

    return bytes.fromhex(generate_key())


@pytest.fixture()
def store(token_key: bytes, tmp_path: Path) -> EncryptedFileStore:
    """Зашифрованное файловое хранилище во временном каталоге."""

    return EncryptedFileStore(tmp_path / "tokens", key=token_key)


@pytest.fixture()
def token_record() -> TokenRecord:
    """Свежая запись токена (не истёкшая)."""

    return TokenRecord(
        access_token=TEST_TOKEN,
        refresh_token="refresh-token-1",
        expires_at=None,
        scopes=("metrika:read", "metrika:write"),
        connection_id="default",
        user_login="test-user",
    )


@pytest.fixture()
def client() -> Iterator[MetrikaClient]:
    """Клиент со статическим токеном (HTTP мокается respx в тестах).

    Пул соединений не открывается: respx перехватывает запросы до сети.
    """

    yield MetrikaClient(token=TEST_TOKEN)
