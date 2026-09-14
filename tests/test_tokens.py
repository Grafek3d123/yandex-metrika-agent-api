"""Тесты хранилища токенов: шифрование на диске, wrong-key, продление."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from yandex_metrika_agent.crypto import generate_key
from yandex_metrika_agent.errors import AuthError, TokenStorageError
from yandex_metrika_agent.tokens import EncryptedFileStore, TokenRecord


def _record(connection_id: str = "default", **overrides: object) -> TokenRecord:
    data: dict[str, object] = {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "expires_at": None,
        "connection_id": connection_id,
    }
    data.update(overrides)
    return TokenRecord(**data)  # type: ignore[arg-type]


def _refreshed(rec: TokenRecord, token: str) -> TokenRecord:
    return rec.with_tokens(
        access_token=token,
        refresh_token=rec.refresh_token,
        expires_at=time.time() + 3600,
    )


def test_save_and_get_roundtrip(store: EncryptedFileStore) -> None:
    record = _record(user_login="vasya")
    store.save(record)
    loaded = store.get("default")
    assert loaded is not None
    assert loaded.access_token == "at-1"
    assert loaded.user_login == "vasya"


def test_file_is_encrypted_on_disk(store: EncryptedFileStore, tmp_path: Path) -> None:
    store.save(_record())
    files = list((tmp_path / "tokens").glob("*.token"))
    assert len(files) == 1
    raw = files[0].read_bytes()
    assert b"at-1" not in raw
    assert b"rt-1" not in raw


def test_missing_connection_returns_none(store: EncryptedFileStore) -> None:
    assert store.get("nope") is None


def test_delete(store: EncryptedFileStore) -> None:
    store.save(_record())
    assert store.delete("default") is True
    assert store.delete("default") is False
    assert store.get("default") is None


def test_list_connections(store: EncryptedFileStore) -> None:
    store.save(_record("a"))
    store.save(_record("b"))
    assert sorted(store.list_connections()) == ["a", "b"]


def test_wrong_key_cannot_read(tmp_path: Path, token_key: bytes) -> None:
    store = EncryptedFileStore(tmp_path / "tokens", key=token_key)
    store.save(_record())
    other_store = EncryptedFileStore(tmp_path / "tokens", key=bytes.fromhex(generate_key()))
    with pytest.raises(TokenStorageError):
        other_store.get("default")


def test_empty_connection_id_rejected(store: EncryptedFileStore) -> None:
    with pytest.raises(TokenStorageError):
        store.get("  ")


def test_get_valid_returns_fresh_token(store: EncryptedFileStore) -> None:
    store.save(_record(expires_at=time.time() + 3600))
    result = asyncio.run(store.get_valid("default", lambda rec: rec))
    assert result.access_token == "at-1"


def test_get_valid_refreshes_expired(store: EncryptedFileStore) -> None:
    store.save(_record(expires_at=time.time() - 10))

    def refresh(rec: TokenRecord) -> TokenRecord:
        return _refreshed(rec, "at-2")

    result = asyncio.run(store.get_valid("default", refresh))
    assert result.access_token == "at-2"
    reloaded = store.get("default")
    assert reloaded is not None
    assert reloaded.access_token == "at-2"


def test_get_valid_expired_without_refresh_token(store: EncryptedFileStore) -> None:
    store.save(_record(refresh_token=None, expires_at=time.time() - 10))
    with pytest.raises(AuthError):
        asyncio.run(store.get_valid("default", lambda rec: rec))


def test_get_valid_not_authorized(store: EncryptedFileStore) -> None:
    with pytest.raises(AuthError):
        asyncio.run(store.get_valid("ghost", lambda rec: rec))


def test_get_valid_async_refresh(store: EncryptedFileStore) -> None:
    store.save(_record(expires_at=time.time() - 10))

    async def refresh(rec: TokenRecord) -> TokenRecord:
        return _refreshed(rec, "at-async")

    result = asyncio.run(store.get_valid("default", refresh))
    assert result.access_token == "at-async"


def test_get_valid_deduplicates_parallel_refresh(store: EncryptedFileStore) -> None:
    store.save(_record(expires_at=time.time() - 10))
    calls = 0

    async def refresh(rec: TokenRecord) -> TokenRecord:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return _refreshed(rec, f"at-{calls}")

    async def main() -> list[str]:
        results = await asyncio.gather(
            store.get_valid("default", refresh),
            store.get_valid("default", refresh),
        )
        return [item.access_token for item in results]

    tokens = asyncio.run(main())
    assert calls == 1, "параллельное продление должно выполниться один раз"
    assert tokens == ["at-1", "at-1"]


def test_is_expired_with_leeway() -> None:
    soon = _record(expires_at=time.time() + 300)
    assert not soon.is_expired()
    assert soon.is_expired(leeway=3600)


def test_repr_hides_secrets() -> None:
    record = _record()
    rendered = repr(record)
    assert "at-1" not in rendered
    assert "rt-1" not in rendered


def test_public_hides_secrets() -> None:
    public = _record().public()
    assert "at-1" not in str(public)
    assert public["has_refresh_token"] is True
    assert public["connection_id"] == "default"
