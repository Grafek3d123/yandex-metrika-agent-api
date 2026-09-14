"""Тесты OAuth: state, PKCE, разбор callback, обмен кода, скоупы."""

from __future__ import annotations

import base64
import hashlib

import httpx
import pytest
import respx

from yandex_metrika_agent.errors import AuthError, ScopeError, ValidationError
from yandex_metrika_agent.oauth import (
    AUTHORIZE_URL,
    TOKEN_URL,
    OAuthClient,
    OAuthFlow,
    build_authorize_url,
    new_code_verifier,
    new_state,
    parse_callback_url,
    pkce_challenge,
)

# --- PKCE --------------------------------------------------------------------


def test_pkce_challenge_is_s256_of_verifier() -> None:
    verifier = "test-verifier-string"
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    assert pkce_challenge(verifier) == expected


def test_code_verifier_length_in_rfc_range() -> None:
    for _ in range(5):
        assert 43 <= len(new_code_verifier()) <= 128


def test_code_verifier_is_random() -> None:
    assert new_code_verifier() != new_code_verifier()


def test_state_is_random() -> None:
    assert len(new_state()) >= 24
    assert new_state() != new_state()


# --- build_authorize_url -----------------------------------------------------


def test_authorize_url_contains_pkce_params() -> None:
    url = build_authorize_url(
        "client-id",
        redirect_uri="http://localhost:8765/callback",
        state="st-1",
        code_challenge="challenge-x",
    )
    assert "code_challenge=challenge-x" in url
    assert "code_challenge_method=S256" in url
    assert "state=st-1" in url
    assert url.startswith(AUTHORIZE_URL)


def test_authorize_url_requires_client_id() -> None:
    with pytest.raises(ValidationError):
        build_authorize_url("", redirect_uri="http://localhost/callback")


# --- parse_callback_url ------------------------------------------------------


def test_parse_callback_extracts_code() -> None:
    code = parse_callback_url(
        "http://localhost:8765/callback?code=abc&state=st", expected_state="st"
    )
    assert code == "abc"


def test_parse_callback_state_mismatch_rejected() -> None:
    with pytest.raises(AuthError):
        parse_callback_url("http://localhost/callback?code=abc&state=evil", expected_state="st")


def test_parse_callback_error_propagates() -> None:
    with pytest.raises(AuthError):
        parse_callback_url("http://localhost/callback?error=access_denied")


def test_parse_callback_without_code_rejected() -> None:
    with pytest.raises(AuthError):
        parse_callback_url("http://localhost/callback?state=st")


# --- OAuthClient: обмен кода (respx) -----------------------------------------


def _client(**kwargs: object) -> OAuthClient:
    return OAuthClient("cid", "secret", **kwargs)  # type: ignore[arg-type]


@respx.mock
@pytest.mark.asyncio()
async def test_exchange_code_sends_pkce_verifier() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
        )
    )
    client = _client()
    record = await client.exchange_code(
        "code-1",
        redirect_uri="http://localhost:8765/callback",
        code_verifier="verifier-1",
    )
    assert record.access_token == "at"
    sent = route.calls[0].request.content.decode()
    assert "code_verifier=verifier-1" in sent
    assert "grant_type=authorization_code" in sent


@respx.mock
@pytest.mark.asyncio()
async def test_exchange_code_without_verifier_omits_field() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "at"})
    )
    client = _client()
    await client.exchange_code("code-1")
    sent = route.calls[0].request.content.decode()
    assert "code_verifier" not in sent


@respx.mock
@pytest.mark.asyncio()
async def test_token_error_raises_auth_error() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "bad_verification_code"})
    )
    client = _client()
    with pytest.raises(AuthError):
        await client.exchange_code("bad")


def test_record_scopes_parsed() -> None:
    client = _client()
    record = client._record(
        {"access_token": "at", "scope": "metrika:read metrika:write"},
        connection_id="default",
    )
    assert record.scopes == ("metrika:read", "metrika:write")


# --- OAuthFlow: скоупы --------------------------------------------------------


def _flow(store: object) -> OAuthFlow:
    return OAuthFlow(client=_client(), store=store)


class _Store:
    def __init__(self, record: object) -> None:
        self._record = record
        self.saved: object = None

    def get(self, connection_id: str) -> object:
        return self._record

    def save(self, record: object) -> None:
        self.saved = record

    def delete(self, connection_id: str) -> bool:
        return True

    def list_connections(self) -> list[str]:
        return []


def test_connect_rejects_token_without_read_scope() -> None:
    from yandex_metrika_agent.tokens import TokenRecord

    record = TokenRecord(
        access_token="at",
        refresh_token="rt",
        expires_at=None,
        scopes=("metrika:write",),  # нет metrika:read
    )
    with pytest.raises(ScopeError):
        OAuthFlow._ensure_scopes(record)


def test_ensure_scopes_passes_with_read_scope() -> None:
    from yandex_metrika_agent.tokens import TokenRecord

    record = TokenRecord(
        access_token="at",
        scopes=("metrika:read",),
    )
    OAuthFlow._ensure_scopes(record)  # не бросает


def test_get_access_token_uses_valid_record() -> None:
    from yandex_metrika_agent.tokens import TokenRecord

    record = TokenRecord(
        access_token="at-valid",
        refresh_token="rt",
        expires_at=None,
        scopes=("metrika:read", "metrika:write"),
    )
    flow = _flow(_Store(record))
    import asyncio

    assert asyncio.run(flow.get_access_token("default")) == "at-valid"
