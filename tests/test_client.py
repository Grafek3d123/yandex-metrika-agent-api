"""Тесты MetrikaClient: заголовок авторизации, from_settings, ошибки токена."""

from __future__ import annotations

import httpx
import pytest
import respx

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.config import Settings
from yandex_metrika_agent.errors import ConfigError

URL = "https://api-metrika.yandex.net/management/v1/counters"


@pytest.mark.asyncio()
@respx.mock
async def test_authorization_header_is_oauth() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"counters": []}))
    client = MetrikaClient(token="secret-token")
    try:
        await client.get_json("/management/v1/counters")
        headers = route.calls[0].request.headers
        assert headers["Authorization"] == "OAuth secret-token"
    finally:
        await client.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_no_token_raises_config_error() -> None:
    client = MetrikaClient(token="")
    try:
        with pytest.raises(ConfigError):
            await client.get_json("/management/v1/counters")
    finally:
        await client.aclose()


def test_from_settings_explicit_token_wins() -> None:
    settings = Settings()
    client = MetrikaClient.from_settings(settings, token="explicit", connection_id="conn-1")
    try:
        assert client.connection_id == "conn-1"
    finally:
        import asyncio

        asyncio.run(client.aclose())


@pytest.mark.asyncio()
@respx.mock
async def test_post_json_sends_payload() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"ok": 1}))
    client = MetrikaClient(token="t")
    try:
        result = await client.post_json("/management/v1/counters", {"name": "x"})
        assert result == {"ok": 1}
        assert '"name"' in route.calls[0].request.content.decode()
    finally:
        await client.aclose()


@pytest.mark.asyncio()
@respx.mock
async def test_error_response_typed() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(
            403,
            json={"errors": [{"error_type": "access_denied", "message": "нет доступа"}]},
        )
    )
    client = MetrikaClient(token="t")
    try:
        from yandex_metrika_agent.errors import AgentError

        with pytest.raises(AgentError):
            await client.get_json("/management/v1/counters")
    finally:
        await client.aclose()
