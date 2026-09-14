"""Тесты шифрования AES-GCM."""

from __future__ import annotations

import pytest

from yandex_metrika_agent import crypto
from yandex_metrika_agent.errors import TokenStorageError


def test_generate_key_is_hex64() -> None:
    key = crypto.generate_key()
    assert len(key) == 64
    int(key, 16)  # не бросает исключение => это hex


def test_encrypt_decrypt_roundtrip() -> None:
    key = bytes.fromhex(crypto.generate_key())
    plaintext = "секретные данные".encode()
    payload = crypto.encrypt(plaintext, key)
    assert payload != plaintext
    assert crypto.decrypt(payload, key) == plaintext


def test_decrypt_with_wrong_key_fails(token_key: bytes) -> None:
    payload = crypto.encrypt(b"data", token_key)
    other = bytes.fromhex(crypto.generate_key())
    with pytest.raises(TokenStorageError):
        crypto.decrypt(payload, other)


def test_decrypt_tampered_payload_fails(token_key: bytes) -> None:
    payload = bytearray(crypto.encrypt(b"data", token_key))
    payload[-1] ^= 0xFF  # повредили шифротекст
    with pytest.raises(TokenStorageError):
        crypto.decrypt(bytes(payload), token_key)


def test_invalid_key_size_rejected() -> None:
    with pytest.raises(TokenStorageError):
        crypto.encrypt(b"data", b"short")


def test_fingerprint_stable_and_short() -> None:
    assert crypto.fingerprint("abc") == crypto.fingerprint("abc")
    assert crypto.fingerprint("abc") != crypto.fingerprint("abd")
    assert len(crypto.fingerprint("abc")) == 12


def test_hmac_equal() -> None:
    assert crypto.hmac_equal(b"a", b"a")
    assert not crypto.hmac_equal(b"a", b"b")
