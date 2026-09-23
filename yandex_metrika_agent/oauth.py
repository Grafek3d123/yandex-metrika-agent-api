"""OAuth Яндекс ID: получение и продление токенов для Метрики.

Приложение никогда не видит пароль пользователя: вход выполняет Яндекс в браузере.

Поддержаны три способа получить токен (выбираются по возможностям окружения):

``browser``
    Authorization Code + локальный callback-сервер. Пользователь подтверждает
    доступ в браузере, код приходит на ``http://localhost:<port>/callback`` —
    копировать ничего не нужно. Требует зарегистрированный Redirect URI.

``code``
    Резерв для headless/удалённых окружений: ``Redirect URI =
    https://oauth.yandex.ru/verification_code``, пользователь вводит
    одноразовый код в приложении.

``device``
    Код устройства: пользователь вводит короткий ``user_code`` на странице
    ``https://oauth.yandex.ru/device`` (удобно на телевизорах/терминалах).

Документация: https://yandex.ru/dev/id/doc/ru/
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

from yandex_metrika_agent.errors import AuthError, ScopeError, ValidationError
from yandex_metrika_agent.log import anonymize_headers, anonymize_query, get_logger
from yandex_metrika_agent.tokens import TokenRecord

OAUTH_HOST = "https://oauth.yandex.ru"
AUTHORIZE_URL = f"{OAUTH_HOST}/authorize"
TOKEN_URL = f"{OAUTH_HOST}/token"
REVOKE_TOKEN_URL = f"{OAUTH_HOST}/revoke_token"
DEVICE_CODE_URL = f"{OAUTH_HOST}/device/code"
DEVICE_VERIFY_URL = f"{OAUTH_HOST}/device"
VERIFICATION_CODE_URL = f"{OAUTH_HOST}/verification_code"
USER_INFO_URL = "https://login.yandex.ru/info"

#: Скоупы Метрики (справка: https://yandex.ru/dev/metrika/).
METRIKA_READ = "metrika:read"
METRIKA_WRITE = "metrika:write"
METRIKA_ALL_SCOPES: tuple[str, ...] = (METRIKA_READ, METRIKA_WRITE)

#: Сколько секунд ждать подтверждения в браузере.
DEFAULT_BROWSER_TIMEOUT = 300.0

_LOGGER = get_logger("oauth")


class AuthorizationPendingError(AuthError):
    """Пользователь ещё не подтвердил доступ (polling device-потока)."""


@dataclass(frozen=True)
class OAuthEndpoints:
    """Адреса Яндекс OAuth (нужны для тестов и стендов)."""

    authorize: str = AUTHORIZE_URL
    token: str = TOKEN_URL
    revoke: str = REVOKE_TOKEN_URL
    device_code: str = DEVICE_CODE_URL
    device_verify: str = DEVICE_VERIFY_URL
    verification_code: str = VERIFICATION_CODE_URL
    user_info: str = USER_INFO_URL


@dataclass(frozen=True)
class DeviceCode:
    """Пара кодов device-потока."""

    device_code: str = field(repr=False)
    user_code: str
    verification_url: str
    interval: float
    expires_at: float

    @property
    def instructions(self) -> str:
        """Подсказка пользователю."""

        return (
            f"Откройте {self.verification_url} и введите код {self.user_code}. "
            "Код действует до истечения 5 минут."
        )


def new_state() -> str:
    """Стохастический state для защиты от подмены ответа callback."""

    return secrets.token_urlsafe(24)


def new_code_verifier() -> str:
    """PKCE code_verifier (RFC 7636: 43..128 символов)."""

    return secrets.token_urlsafe(48)[:128]


def pkce_challenge(verifier: str) -> str:
    """code_challenge для метода S256: base64url(SHA-256(verifier)) без padding."""

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def new_device_id(prefix: str = "yandex-metrika") -> str:
    """Идентификатор устройства для device-потока (6..50 печатных ASCII)."""

    return f"{prefix}-{secrets.token_hex(8)}"[:50]


def build_authorize_url(
    client_id: str,
    *,
    redirect_uri: str,
    scopes: Sequence[str] = METRIKA_ALL_SCOPES,
    state: str | None = None,
    response_type: str = "code",
    code_challenge: str | None = None,
    code_challenge_method: str = "S256",
) -> str:
    """Собрать URL страницы подтверждения доступа."""

    if not client_id:
        raise ValidationError("Не передан client_id OAuth-приложения.")
    params: dict[str, str] = {"response_type": response_type, "client_id": client_id}
    if redirect_uri:
        params["redirect_uri"] = redirect_uri
    if scopes:
        params["scope"] = " ".join(scopes)
    if state:
        params["state"] = state
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = code_challenge_method
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def parse_callback_url(url: str, *, expected_state: str | None = None) -> str:
    """Достать ``code`` из URL, на который Яндекс переадресовал браузер.

    Raises:
        AuthError: пользователь запретил доступ, state не совпал или кода нет.
    """

    parsed = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qs(parsed.query)

    def value(name: str) -> str | None:
        values = params.get(name)
        return values[0] if values else None

    error = value("error")
    if error:
        raise AuthError(
            f"Яндекс отклонил авторизацию: {error}",
            details={"error": error, "error_description": value("error_description")},
        )
    state = value("state")
    if expected_state and state != expected_state:
        raise AuthError(
            "Не совпал параметр state: ответ callback не соответствует запросу.",
        )
    code = value("code")
    if not code:
        raise AuthError("В ответе callback нет параметр code.")
    return code


class OAuthClient:
    """Низкоуровний клиент Яндекс OAuth.

    Args:
        client_id: идентификатор OAuth-приложения.
        client_secret: секретный ключ (пусто — для приложений без секрета).
        endpoints: адреса OAuth (для тестов).
        transport: готовый :class:`httpx.AsyncClient` или None (создаётся сам).
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str = "",
        *,
        endpoints: OAuthEndpoints | None = None,
        transport: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not client_id:
            raise ValidationError("Не передан client_id OAuth-приложения.")
        self.client_id = client_id
        self.client_secret = client_secret
        self.endpoints = endpoints or OAuthEndpoints()
        self._external = transport
        self._own: httpx.AsyncClient | None = None
        self._timeout = timeout

    async def __aenter__(self) -> OAuthClient:
        await self._client()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Закрыть только собственный HTTP-клиент."""

        if self._own is not None and not self._own.is_closed:
            await self._own.aclose()
        self._own = None

    async def _client(self) -> httpx.AsyncClient:
        if self._external is not None:
            return self._external
        if self._own is None or self._own.is_closed:
            self._own = httpx.AsyncClient(timeout=self._timeout)
        return self._own

    # --- Построение запросов -------------------------------------------------

    def authorize_url(
        self,
        *,
        redirect_uri: str,
        scopes: Sequence[str] = METRIKA_ALL_SCOPES,
        state: str | None = None,
        code_challenge: str | None = None,
    ) -> str:
        """URL авторизации для открытия в браузере."""

        return build_authorize_url(
            self.client_id,
            redirect_uri=redirect_uri,
            scopes=scopes,
            state=state,
            code_challenge=code_challenge,
        )

    def verification_code_url(
        self,
        *,
        scopes: Sequence[str] = METRIKA_ALL_SCOPES,
        device_id: str | None = None,
    ) -> str:
        """URL страницы с одноразовым кодом (headless-резерв)."""

        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self.client_id,
            "force_confirm": "yes",
        }
        if scopes:
            params["scope"] = " ".join(scopes)
        if device_id:
            params["device_id"] = device_id
        return f"{self.endpoints.verification_code}?{urllib.parse.urlencode(params)}"

    # --- Обмен кодов на токен ------------------------------------------------

    async def exchange_code(
        self,
        code: str,
        *,
        redirect_uri: str = "",
        code_verifier: str = "",
        connection_id: str = "default",
    ) -> TokenRecord:
        """Обменять authorization code на токены.

        Args:
            code: код подтверждения из callback/verification_code.
            redirect_uri: тот же адрес, что и в запросе кода.
            code_verifier: PKCE-верификатор, если код запрашивался с
                ``code_challenge`` (browser-поток).
        """

        data: dict[str, str] = {"grant_type": "authorization_code", "code": code}
        if redirect_uri:
            data["redirect_uri"] = redirect_uri
        if code_verifier:
            data["code_verifier"] = code_verifier
        return self._record(await self._token_request(data), connection_id=connection_id)

    async def request_device_code(
        self,
        *,
        scopes: Sequence[str] = METRIKA_ALL_SCOPES,
        device_id: str | None = None,
        device_name: str = "yandex-metrika",
    ) -> DeviceCode:
        """Запросить пару кодов device-потока."""

        data: dict[str, str] = {"client_id": self.client_id, "device_name": device_name}
        if device_id:
            data["device_id"] = device_id
        if scopes:
            data["scope"] = " ".join(scopes)
        payload = await self._post(self.endpoints.device_code, data, context="device code")
        device_code = payload.get("device_code")
        user_code = payload.get("user_code")
        if not isinstance(device_code, str) or not isinstance(user_code, str):
            raise AuthError("Яндекс вернул ответ device-потока без кодов.")
        interval = float(payload.get("interval") or 5)
        return DeviceCode(
            device_code=device_code,
            user_code=user_code,
            verification_url=str(payload.get("verification_url") or self.endpoints.device_verify),
            interval=max(interval, 1.0),
            expires_at=time.time() + float(payload.get("expires_in") or 300),
        )

    async def exchange_device_code(
        self,
        device_code: str,
        *,
        connection_id: str = "default",
    ) -> TokenRecord:
        """Обменять ``device_code`` на токены.

        Raises:
            AuthorizationPendingError: пользователь ещё не ввёл код.
        """

        return self._record(
            await self._token_request(
                {"grant_type": "device_code", "code": device_code},
                pending_is_error=True,
            ),
            connection_id=connection_id,
        )

    async def refresh(self, refresh_token: str, *, connection_id: str = "default") -> TokenRecord:
        """Продлить доступ по refresh-токену."""

        if not refresh_token:
            raise AuthError("Нет refresh-токена для продления доступа.")
        return self._record(
            await self._token_request(
                {"grant_type": "refresh_token", "refresh_token": refresh_token}
            ),
            connection_id=connection_id,
        )

    async def revoke(self, token: str) -> None:
        """Отозвать токен на стороне Яндекса."""

        await self._post(
            self.endpoints.revoke,
            {"token": token, **self._credentials()},
            context="отзыв токена",
        )

    async def fetch_user_login(self, access_token: str) -> str | None:
        """Логин пользователя по токену (для подписи подключения)."""

        client = await self._client()
        try:
            response = await client.get(
                self.endpoints.user_info,
                params={"format": "json"},
                headers={"Authorization": f"OAuth {access_token}"},
            )
        except httpx.HTTPError as exc:  # сеть не должна ронять авторизацию
            _LOGGER.warning("Не удалось получить логин пользователя: %s", type(exc).__name__)
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if isinstance(payload, dict):
            login = payload.get("login") or payload.get("display_name")
            return str(login) if login else None
        return None

    # --- Внутреннее ----------------------------------------------------------

    def _credentials(self) -> dict[str, str]:
        """Пара клиентских учётных данных для тела запроса."""

        data = {"client_id": self.client_id}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        return data

    def _basic_auth(self) -> httpx.BasicAuth | None:
        """Basic auth вместо секретa в теле (Яндекс поддерживает оба варианта)."""

        if not self.client_secret:
            return None
        return httpx.BasicAuth(self.client_id, self.client_secret)

    async def _token_request(
        self,
        data: dict[str, str],
        *,
        pending_is_error: bool = False,
    ) -> dict[str, Any]:
        payload = await self._post(
            self.endpoints.token,
            {**data, **self._credentials()},
            context="обмен токена",
            basic_auth=True,
            pending_is_error=pending_is_error,
        )
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise AuthError("Яндекс вернул ответ без access_token.")
        return payload

    async def _post(
        self,
        url: str,
        data: dict[str, str],
        *,
        context: str,
        basic_auth: bool = False,
        pending_is_error: bool = False,
    ) -> dict[str, Any]:
        client = await self._client()
        basic = self._basic_auth() if basic_auth else None
        body = {key: value for key, value in data.items() if value}
        safe = anonymize_query(body)
        try:
            response = (
                await client.post(url, data=body, auth=basic)
                if basic is not None
                else await client.post(url, data=body)
            )
        except httpx.HTTPError as exc:
            raise AuthError(
                f"{context}: запрос к Яндекс OAuth не выполнен ({type(exc).__name__}).",
                details={"url": url, "request": safe},
            ) from exc

        payload: Any = None
        if response.content:
            try:
                payload = response.json()
            except ValueError:
                payload = {"error_description": response.text[:500]}
        if not isinstance(payload, dict):
            payload = {}

        error = payload.get("error")
        if response.status_code >= 400 or error:
            code = str(error or response.status_code)
            description = str(payload.get("error_description") or "ошибка не описана")
            if code == "authorization_pending" and pending_is_error:
                raise AuthorizationPendingError(description, details={"error": code})
            raise AuthError(
                f"{context}: Яндекс OAuth отказал ({code}). {description}",
                details={
                    "status_code": response.status_code,
                    "error": code,
                    "request": safe,
                    "headers": anonymize_headers(dict(response.headers)),
                },
            )
        result: dict[str, Any] = payload
        return result

    def _record(self, payload: dict[str, Any], *, connection_id: str) -> TokenRecord:
        """Собрать :class:`TokenRecord` из ответа Яндекс OAuth."""

        expires_in = payload.get("expires_in")
        expires_at = (
            time.time() + float(expires_in) if isinstance(expires_in, (int, float)) else None
        )
        scopes_raw = payload.get("scope")
        scopes = tuple(str(scopes_raw).split()) if isinstance(scopes_raw, str) else ()
        refresh_token = payload.get("refresh_token")
        return TokenRecord(
            access_token=str(payload["access_token"]),
            refresh_token=str(refresh_token) if refresh_token else None,
            token_type=str(payload.get("token_type") or "bearer"),
            expires_at=expires_at,
            scopes=scopes,
            connection_id=connection_id,
        )


class LoopbackCallback:
    """Одноразовый HTTP-сервер для приёма кода на ``http://localhost:<port>/...``."""

    def __init__(self, redirect_uri: str, *, expected_state: str | None = None) -> None:
        parts = urllib.parse.urlsplit(redirect_uri)
        if parts.scheme not in ("http", "") or parts.hostname not in ("localhost", "127.0.0.1", ""):
            raise ValidationError(
                "Для browser-потока Redirect URI должен вести на http://localhost:<port>/...",
                details={"redirect_uri": redirect_uri},
            )
        self.path = parts.path or "/callback"
        self.port = parts.port or 8765
        self.expected_state = expected_state
        self.code: str | None = None
        self.error: str | None = None
        self._event = threading.Event()
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        """Запустить сервер в фоновом потоке."""

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if urllib.parse.urlsplit(self.path).path != outer.path:
                    self.send_error(404)
                    return
                try:
                    outer.code = parse_callback_url(self.path, expected_state=outer.expected_state)
                except AuthError as exc:
                    outer.error = exc.message
                    self._reply("Не удалось получить код", exc.message)
                    outer._event.set()
                    return
                self._reply("Подключение выполнено", "Можно закрыть эту вкладку.")
                outer._event.set()

            def _reply(self, title: str, text: str) -> None:
                body = (
                    "<!doctype html><meta charset=utf-8>"
                    f"<title>{title}</title><h1>{title}</h1><p>{text}</p>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                # Журнал callback-запросов ведёт наш логгер; стандартный вывод не нужен.
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def wait(self, timeout: float = DEFAULT_BROWSER_TIMEOUT) -> str:
        """Дождаться кода (блокирующе). Используется внутри ``to_thread``."""

        if not self._event.wait(timeout):
            raise AuthError(
                "Пользователь не подтвердил доступ за отведённое время.",
                details={"timeout": timeout},
            )
        if self.error:
            raise AuthError(self.error)
        if not self.code:
            raise AuthError("Callback не вернул код авторизации.")
        return self.code

    def stop(self) -> None:
        """Остановить сервер."""

        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


@dataclass
class OAuthFlow:
    """Высокоуровневая авторизация: браузер, код на странице, device-поток.

    Args:
        client: настроенный :class:`OAuthClient`.
        store: хранилище токенов (``save``/``get_valid``).
        redirect_uri: зарегистрированный Redirect URI browser-потока.
        open_browser: чем открывать браузер (для тестов подменяется).
        input_code: функция ввода кода пользователем (headless-резерв).
    """

    client: OAuthClient
    store: Any
    redirect_uri: str = "http://localhost:8765/callback"
    open_browser: Callable[[str], Any] = field(default=webbrowser.open)
    input_code: Callable[[str], str] | Callable[[str], Awaitable[str]] = input
    scopes: tuple[str, ...] = METRIKA_ALL_SCOPES

    async def connect(
        self,
        connection_id: str = "default",
        *,
        method: str = "browser",
        timeout: float = DEFAULT_BROWSER_TIMEOUT,
    ) -> TokenRecord:
        """Получить токен и сохранить в хранилище.

        Args:
            connection_id: имя подключения (несколько пользователей/сайтов).
            method: ``browser`` | ``code`` | ``device``.
            timeout: сколько секунд ждать подтверждения.
        """

        handlers: dict[str, Callable[[str, float], Awaitable[TokenRecord]]] = {
            "browser": self._connect_browser,
            "code": self._connect_code,
            "device": self._connect_device,
        }
        try:
            handler = handlers[method]
        except KeyError as exc:
            raise ValidationError(
                f"Неизвестный способ авторизации: {method!r}.",
                details={"allowed": sorted(handlers)},
            ) from exc
        record = await handler(connection_id, timeout)
        user_login = await self.client.fetch_user_login(record.access_token)
        saved = (
            TokenRecord(
                access_token=record.access_token,
                refresh_token=record.refresh_token,
                token_type=record.token_type,
                expires_at=record.expires_at,
                scopes=record.scopes,
                connection_id=record.connection_id,
                user_login=user_login or record.user_login,
                created_at=record.created_at,
            )
            if user_login
            else record
        )
        self.store.save(saved)
        self._ensure_scopes(saved)
        _LOGGER.info(
            "Авторизация завершена: connection=%s scopes=%s",
            saved.connection_id,
            ",".join(saved.scopes) or "-",
        )
        return saved

    @staticmethod
    def _ensure_scopes(record: TokenRecord) -> None:
        """Проверить, что выданный токен годится для Метрики.

        Яндекс возвращает фактические права в ответе token; если прав меньше
        запрошенного (неверный скоуп у клиента, пользователь снял галочку) —
        падаем сразу, а не на первом запросе к API.
        """

        if record.scopes and METRIKA_READ not in record.scopes:
            raise ScopeError(
                "Токен выдан без права metrika:read — переавторизуйте подключение.",
                details={"scopes": list(record.scopes), "required": METRIKA_READ},
            )

    async def _connect_browser(self, connection_id: str, timeout: float) -> TokenRecord:
        state = new_state()
        verifier = new_code_verifier()
        callback = LoopbackCallback(self.redirect_uri, expected_state=state)
        callback.start()
        url = self.client.authorize_url(
            redirect_uri=self.redirect_uri,
            scopes=self.scopes,
            state=state,
            code_challenge=pkce_challenge(verifier),
        )
        _LOGGER.info("Откройте в браузере: %s", url)
        try:
            opened = self.open_browser(url)
            if asyncio.iscoroutine(opened):
                await opened
            code = await asyncio.to_thread(callback.wait, timeout)
            return await self.client.exchange_code(
                code,
                redirect_uri=self.redirect_uri,
                code_verifier=verifier,
                connection_id=connection_id,
            )
        finally:
            callback.stop()

    async def _connect_code(self, connection_id: str, timeout: float) -> TokenRecord:
        device_id = new_device_id()
        url = self.client.verification_code_url(scopes=self.scopes, device_id=device_id)
        _LOGGER.info("Откройте в браузере: %s", url)
        opened = self.open_browser(url)
        if asyncio.iscoroutine(opened):
            await opened
        prompt = f"Введите код со страницы {url}: "
        entered = self.input_code(prompt)
        if asyncio.iscoroutine(entered):
            entered = await entered
        code = str(entered).strip()
        if not code:
            raise ValidationError("Пользователь не ввёл код подтверждения.")
        return await self.client.exchange_code(code, connection_id=connection_id)

    async def _connect_device(self, connection_id: str, timeout: float) -> TokenRecord:
        device = await self.client.request_device_code(
            scopes=self.scopes,
            device_id=new_device_id(),
        )
        _LOGGER.info("%s", device.instructions)
        deadline = time.time() + min(timeout, max(device.expires_at - time.time(), 1.0))
        while time.time() < deadline:
            await asyncio.sleep(device.interval)
            try:
                return await self.client.exchange_device_code(
                    device.device_code,
                    connection_id=connection_id,
                )
            except AuthorizationPendingError:
                continue
        raise AuthError(
            "Пользователь не подтвердил доступ по коду устройства.",
            details={"user_code": device.user_code},
        )

    async def refresh(self, record: TokenRecord) -> TokenRecord:
        """Продлить запись (используется как callback у ``TokenStore.get_valid``)."""

        if not record.refresh_token:
            raise AuthError("Нет refresh-токена: нужна повторная авторизация.")
        updated = await self.client.refresh(
            record.refresh_token,
            connection_id=record.connection_id,
        )
        return updated.with_tokens(
            access_token=updated.access_token,
            refresh_token=updated.refresh_token,
            expires_at=updated.expires_at,
            scopes=updated.scopes or record.scopes,
        )

    async def complete_token(
        self,
        connection_id: str = "default",
        *,
        method: str = "browser",
        timeout: float = DEFAULT_BROWSER_TIMEOUT,
    ) -> TokenRecord:
        """Вернуть запись подключения с действующими токенами.

        Если токен уже сохранён — используется он; если его нет — выполняется
        авторизация. Вызывающий (агент, сервис) не занимается продлением:
        это делает ``get_access_token``.
        """

        record = self.store.get(connection_id)
        if record is not None:
            saved: TokenRecord = record
            return saved
        return await self.connect(connection_id, method=method, timeout=timeout)

    async def get_access_token(
        self,
        connection_id: str = "default",
        *,
        method: str = "browser",
        timeout: float = DEFAULT_BROWSER_TIMEOUT,
    ) -> str:
        """Действующий access-токен подключения.

        Сервис сам продлевает истёкший доступ по refresh-токену (или
        авторизует заново, если подключения нет), поэтому сторона,
        пользующаяся токеном, не должна знать про refresh-токен и сроки.
        """

        await self.complete_token(connection_id, method=method, timeout=timeout)
        get_valid = getattr(self.store, "get_valid", None)
        if get_valid is not None:
            valid = get_valid(connection_id, self.refresh)
            if asyncio.iscoroutine(valid):
                valid = await valid
            return str(valid.access_token)
        record = self.store.get(connection_id)
        if record is None:
            raise AuthError(
                f"Подключение {connection_id!r} не авторизовано.",
                details={"connection_id": connection_id},
            )
        if record.is_expired():
            saved = await self.refresh(record)
            self.store.save(saved)
            return str(saved.access_token)
        return str(record.access_token)

    async def disconnect(self, connection_id: str = "default", *, revoke: bool = True) -> bool:
        """Удалить подключение и (по возможности) отозвать токен у Яндекса."""

        record = self.store.get(connection_id)
        if record is None:
            return False
        if revoke:
            try:
                await self.client.revoke(record.access_token)
            except AuthError as exc:
                _LOGGER.warning("Не удалось отозвать токен: %s", exc.message)
        return bool(self.store.delete(connection_id))


__all__ = [
    "AUTHORIZE_URL",
    "DEVICE_CODE_URL",
    "DEVICE_VERIFY_URL",
    "METRIKA_ALL_SCOPES",
    "METRIKA_READ",
    "METRIKA_WRITE",
    "OAUTH_HOST",
    "REVOKE_TOKEN_URL",
    "TOKEN_URL",
    "USER_INFO_URL",
    "VERIFICATION_CODE_URL",
    "AuthorizationPendingError",
    "DeviceCode",
    "LoopbackCallback",
    "OAuthClient",
    "OAuthEndpoints",
    "OAuthFlow",
    "build_authorize_url",
    "new_device_id",
    "new_state",
    "parse_callback_url",
]
