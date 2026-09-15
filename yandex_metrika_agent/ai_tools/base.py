"""Реестр AI-инструментов и общий конверт ответов.

Инструмент — это не HTTP-запрос, а бизнес-операция со строгой JSON-схемой:
агент передаёт ``{"counter": "example.com", "date_from": "..."}``, а низкоуровневые
параметры (``id``, ``ym:s:...``, ``filters``) строит сервисный слой.

Конверт ответа единый для всех инструментов:

* ``status="ok"`` — ``data`` с результатом;
* ``status="needs_input"`` — агенту нужно уточнение (не хватает значения или
  счётчиков подходит несколько): ``question``, ``missing``, ``candidates``;
* ``status="error"`` — типизированная ошибка ``error`` (``AgentError.to_dict``);
* ``status="confirmation_required"`` — разрушающая операция заблокирована до
  валидного подтверждения: ``confirmation_id`` + сводка операции, API-запрос
  при этом НЕ отправляется.

AI не должен конструировать произвольные HTTP-запросы — инструментов уровня
HTTP здесь нет и не будет.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.counters import CounterService, MetrikaCounter
from yandex_metrika_agent.errors import AgentError, NotFoundError, ValidationError
from yandex_metrika_agent.goals import GoalService
from yandex_metrika_agent.log import get_logger
from yandex_metrika_agent.models import Goal
from yandex_metrika_agent.planner import GoalPlanner
from yandex_metrika_agent.reports import ReportService

_LOGGER = get_logger("ai_tools")

#: Схема параметра-счётчика: идентификатор или человекочитаемый запрос.
COUNTER_PARAM: dict[str, Any] = {
    "type": ["integer", "string"],
    "description": (
        "Счётчик: идентификатор, домен (example.com, www.example.com, "
        "https://example.com) или название счётчика."
    ),
}

#: Схема даты: ISO или относительное значение Reports API.
DATE_PARAM: dict[str, Any] = {
    "type": "string",
    "pattern": r"^(\d{4}-\d{2}-\d{2}|today|yesterday|\d+ ?days? ?ago|\d+ ?daysAgo)$",
    "description": "Дата YYYY-MM-DD или today / yesterday / NdaysAgo.",
}


class ToolSafety(str, Enum):
    """Класс безопасности инструмента.

    Поле обязательно для каждого инструмента: отсутствие классификации —
    ошибка конструирования, а не молчаливое ``False``. Это исключает ситуацию,
    когда новый разрушающий инструмент случайно объявлен незащищённым.

    * ``READ_ONLY`` — только чтение, побочных эффектов нет;
    * ``MUTATING`` — запись, но обратимая (создание/изменение цели);
    * ``DESTRUCTIVE`` — необратимая операция (удаление). Требует валидного
      подтверждения, которое проверяется реестром ДО вызова handler'а.
    """

    READ_ONLY = "read_only"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"


def strict_object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Строгая JSON-схема объекта: неизвестные параметры запрещены."""

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


@dataclass
class ToolResult:
    """Единый ответ инструмента агенту."""

    status: str  # ok | needs_input | error | confirmation_required
    data: Any = None
    question: str | None = None
    missing: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    error: dict[str, Any] | None = None
    confirmation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-открытка для агента (только заполненные поля)."""

        payload: dict[str, Any] = {"status": self.status}
        if self.data is not None:
            payload["data"] = self.data
        if self.question:
            payload["question"] = self.question
        if self.missing:
            payload["missing"] = self.missing
        if self.candidates:
            payload["candidates"] = self.candidates
        if self.reason:
            payload["reason"] = self.reason
        if self.error:
            payload["error"] = self.error
        if self.confirmation_id:
            payload["confirmation_id"] = self.confirmation_id
        return payload

    @classmethod
    def ok(cls, data: Any) -> ToolResult:
        return cls(status="ok", data=data)

    @classmethod
    def needs_input(
        cls,
        question: str,
        *,
        missing: list[str] | None = None,
        candidates: list[dict[str, Any]] | None = None,
        reason: str | None = None,
        data: Any = None,
    ) -> ToolResult:
        return cls(
            status="needs_input",
            data=data,
            question=question,
            missing=missing or [],
            candidates=candidates or [],
            reason=reason,
        )

    @classmethod
    def failed(cls, error: AgentError) -> ToolResult:
        return cls(status="error", error=error.to_dict(), reason=error.message)

    @classmethod
    def confirmation_required(
        cls,
        *,
        confirmation_id: str,
        question: str,
        data: Any = None,
        reason: str | None = None,
    ) -> ToolResult:
        """Разрушающая операция заблокирована до валидного подтверждения.

        Возвращается вместо выполнения: DELETE/API-запрос не отправляется.
        ``confirmation_id`` — идентификатор pending-операции, по которому
        доверенный хост (после явного согласия пользователя) получает
        одноразовый токен через :meth:`ToolRegistry.approve`.
        """

        return cls(
            status="confirmation_required",
            data=data,
            question=question,
            reason=reason,
            confirmation_id=confirmation_id,
        )


@dataclass
class ToolContext:
    """Сервисы, доступные инструментам."""

    client: MetrikaClient
    counters: CounterService = field(init=False)
    goals: GoalService = field(init=False)
    reports: ReportService = field(init=False)
    planner: GoalPlanner = field(default_factory=GoalPlanner)

    def __post_init__(self) -> None:
        self.counters = CounterService(self.client)
        self.goals = GoalService(self.client)
        self.reports = ReportService(self.client)

    async def resolve_counter(self, value: int | str) -> MetrikaCounter | ToolResult:
        """Разрешить параметр ``counter`` в один счётчик.

        0 совпадений — ошибка NotFound (статус error), 1 — счётчик,
        несколько — ``needs_input`` со списком кандидатов: пользователь
        выбирает, агент не угадывает.
        """

        if isinstance(value, bool):
            raise ValidationError("counter не может быть булевым значением.")
        if isinstance(value, int):
            if value <= 0:
                raise ValidationError(
                    "counter_id должен быть положительным.",
                    details={"counter": value},
                )
            return await self.counters.get(value)
        text = str(value or "").strip()
        if not text:
            raise ValidationError("Пустой счётчик в запросе.")
        if text.isdigit():
            return await self.counters.get(int(text))
        try:
            return await self.counters.resolve_one(text)
        except NotFoundError as exc:
            raise exc
        except ValidationError as exc:
            candidates = exc.details.get("candidates") or []
            if candidates:
                return ToolResult.needs_input(
                    question=(
                        f"По запросу {text!r} найдено несколько счётчиков. "
                        "Уточните, какой имеется в виду."
                    ),
                    missing=["counter"],
                    candidates=list(candidates),
                    reason=exc.message,
                )
            raise


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class Tool:
    """Инструмент: имя, описание, строгая схема, обработчик, класс безопасности.

    ``safety`` — обязательное поле без значения по умолчанию: конструирование
    инструмента без классификации невозможно. Для ``DESTRUCTIVE`` реестр
    требует настроенный :class:`ConfirmationPolicy` и проверяет подтверждение
    до вызова ``handler``.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    safety: ToolSafety

    def spec(self) -> dict[str, Any]:
        """Открытка инструмента для реестра AI-агента (MCP-совместимая)."""

        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class PendingConfirmation:
    """Ожидающая подтверждения разрушающая операция.

    * ``nonce`` — идентификатор pending-операции (``confirmation_id``);
    * ``fingerprint`` — отпечаток конкретной операции (действие + connection +
      разрешённые аргументы-цель); привязывает токен к точным параметрам;
    * ``expires_at`` — абсолютный срок жизни по ``clock``;
    * ``consumed`` — одноразовость: после успешного выполнения токен гасится.
    """

    nonce: str
    fingerprint: bytes
    expires_at: float
    consumed: bool = False
    summary: dict[str, Any] = field(default_factory=dict)


class ConfirmationPolicy:
    """Политика подтверждений для разрушающих инструментов.

    Модель безопасности. AI-агент умеет только вызывать инструменты с JSON
    аргументами. Он не может подделать подтверждение, потому что:

    * токен — это HMAC от отпечатка операции под секретом процесса, который
      недоступен через интерфейс вызова инструментов;
    * токен выдаёт отдельный доверенный метод :meth:`approve`, который хост
      вызывает только после явного согласия пользователя, — он НЕ является
      инструментом и не входит в ``specs()``;
    * токен привязан к точным параметрам (connection_id, counter_id, goal_id,
      ...) — изменение аргументов операции его обесценивает;
    * токен ограничен по времени и одноразовый.

    ``clock`` инъектируется для детерминированных тестов просрочки.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        secret: bytes | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValidationError("ttl_seconds подтверждения должен быть положительным.")
        self.ttl_seconds = float(ttl_seconds)
        self._secret = secret or secrets.token_bytes(32)
        self._clock = clock
        self._pending: dict[str, PendingConfirmation] = {}
        self._lock = threading.Lock()

    # --- Внутреннее ----------------------------------------------------------

    def _mac(self, nonce: str, fingerprint: bytes) -> str:
        message = nonce.encode("utf-8") + fingerprint
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def _is_expired(self, pending: PendingConfirmation, now: float) -> bool:
        return now > pending.expires_at

    def _prune(self, now: float) -> None:
        stale = [
            nonce
            for nonce, pending in self._pending.items()
            if self._is_expired(pending, now)
        ]
        for nonce in stale:
            self._pending.pop(nonce, None)

    # --- Публичное API -------------------------------------------------------

    def request(self, fingerprint: bytes, summary: dict[str, Any]) -> PendingConfirmation:
        """Зарегистрировать pending-операцию (идемпотентно по отпечатку).

        Повторный вызов без подтверждения для той же операции возвращает тот
        же ``confirmation_id`` — агент может «напомнить» о себе без спам-
        создания новых pending.
        """

        now = self._clock()
        with self._lock:
            self._prune(now)
            for pending in self._pending.values():
                if not pending.consumed and pending.fingerprint == fingerprint:
                    return pending
            pending = PendingConfirmation(
                nonce=secrets.token_urlsafe(16),
                fingerprint=fingerprint,
                expires_at=now + self.ttl_seconds,
                summary=dict(summary),
            )
            self._pending[pending.nonce] = pending
            return pending

    def approve(self, confirmation_id: str, *, approved_by: str) -> str:
        """Доверенный хост: выдать одноразовый токен после согласия пользователя.

        Метод намеренно НЕ является инструментом: через ``ToolRegistry.call``
        (единственный интерфейс AI-агента) он недоступен. Требует
        непустого ``approved_by`` — кто подтвердил.
        """

        if not approved_by or not str(approved_by).strip():
            raise ValidationError("Нужно указать, кто подтвердил операцию (approved_by).")
        now = self._clock()
        with self._lock:
            self._prune(now)
            pending = self._pending.get(confirmation_id)
            if pending is None:
                raise ValidationError(
                    "Подтверждение не найдено или уже просрочено.",
                    details={"confirmation_id": confirmation_id},
                )
            if pending.consumed:
                raise ValidationError(
                    "Это подтверждение уже использовано.",
                    details={"confirmation_id": confirmation_id},
                )
            return f"{confirmation_id}.{self._mac(confirmation_id, pending.fingerprint)}"

    def verify_and_consume(
        self, token: str, fingerprint: bytes
    ) -> tuple[str, PendingConfirmation | None]:
        """Проверить токен и погасить подтверждение при успехе.

        Возвращает ``(status, pending)``:

        * ``"ok"`` — токен валиден, привязан к текущему отпечатку, не истёк,
          не использован; подтверждение помечено использованным;
        * ``"absent"`` — токена нет;
        * ``"malformed"`` — токен неверного формата;
        * ``"unknown"`` — неизвестный/утерянный ``confirmation_id``;
        * ``"expired"`` — срок жизни истёк;
        * ``"reused"`` — подтверждение уже погашено;
        * ``"tampered"`` — HMAC не совпал (токен подделан/изменён);
        * ``"mismatch"`` — токен выдан для другой операции (другой
          connection_id/counter_id/goal_id или иные изменённые аргументы).
        """

        if not token:
            return ("absent", None)
        nonce, sep, mac = token.partition(".")
        if not sep or not mac:
            return ("malformed", None)
        now = self._clock()
        with self._lock:
            pending = self._pending.get(nonce)
            if pending is None:
                return ("malformed" if not sep else "unknown", None)
            if self._is_expired(pending, now):
                self._pending.pop(nonce, None)
                return ("expired", None)
            expected = self._mac(nonce, pending.fingerprint)
            if not hmac.compare_digest(expected, mac):
                return ("tampered", None)
            if pending.fingerprint != fingerprint:
                return ("mismatch", None)
            if pending.consumed:
                return ("reused", None)
            pending.consumed = True
            return ("ok", pending)

    def pending_ids(self) -> list[str]:
        """Идентификаторы ожидающих подтверждений (для наблюдения хостом)."""

        now = self._clock()
        with self._lock:
            self._prune(now)
            return sorted(n for n, p in self._pending.items() if not p.consumed)

    def remaining(self, confirmation_id: str) -> float:
        """Секунды до истечения pending-подтверждения (0, если нет/истёк)."""

        now = self._clock()
        with self._lock:
            pending = self._pending.get(confirmation_id)
            if pending is None or self._is_expired(pending, now):
                return 0.0
            return max(0.0, round(pending.expires_at - now, 3))


class ToolRegistry:
    """Реестр инструментов с вызовом по имени."""

    def __init__(
        self,
        tools: list[Tool],
        *,
        confirmations: ConfirmationPolicy | None = None,
    ) -> None:
        self.confirmations = confirmations
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValidationError(
                    f"Дубликат инструмента: {tool.name}.",
                    details={"tool": tool.name},
                )
            if tool.safety is ToolSafety.DESTRUCTIVE and confirmations is None:
                raise ValidationError(
                    f"Разрушающий инструмент {tool.name!r} нельзя зарегистрировать "
                    "без ConfirmationPolicy: подтверждение обязательного guard'а "
                    "негде проверять.",
                    details={"tool": tool.name},
                )
            self._tools[tool.name] = tool

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ValidationError(
                f"Неизвестный инструмент: {name!r}.",
                details={"tool": name, "allowed": self.names},
            ) from exc

    def specs(self) -> list[dict[str, Any]]:
        """Список инструментов со строгими схемами для AI-агента."""

        return [self._tools[name].spec() for name in self.names]

    def approve(self, confirmation_id: str, *, approved_by: str) -> dict[str, Any]:
        """Доверенный метод хоста: выдать токен после согласия пользователя.

        НЕ является инструментом: недоступен через ``call``/``specs`` и потому
        недостижим из AI Tool Layer. Хост вызывает его только после того, как
        пользователь явно подтвердил операцию.
        """

        if self.confirmations is None:
            raise ValidationError("Политика подтверждений не настроена.")
        token = self.confirmations.approve(confirmation_id, approved_by=approved_by)
        return {"confirmation_id": confirmation_id, "confirmation_token": token}

    def pending_confirmations(self) -> list[str]:
        """Идентификаторы ожидающих подтверждений (для наблюдения хостом)."""

        if self.confirmations is None:
            return []
        return self.confirmations.pending_ids()

    async def call(
        self, context: ToolContext, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Вызвать инструмент и вернуть JSON-конверт.

        Для ``DESTRUCTIVE``-инструментов guard подтверждения выполняется ЗДЕСЬ,
        до вызова ``handler``: без валидного одноразового токена обработчик (и
        тем более DELETE) не запускается никогда. Ошибки агента превращаются в
        ``status="error"``, неожиданные — в ``ValidationError``: агенту не нужны
        стеки трейсов.
        """

        tool = self.get(name)
        try:
            if tool.safety is ToolSafety.DESTRUCTIVE:
                gate = await self._destructive_gate(tool, context, dict(arguments or {}))
                if gate is not None:
                    return gate
            result = tool.handler(context, dict(arguments or {}))
            if inspect.isawaitable(result):
                result = await result
            return _as_envelope(result)
        except AgentError as exc:
            _LOGGER.info("Инструмент %s: %s", name, exc.message)
            return ToolResult.failed(exc).to_dict()

    # --- Guard разрушающих операций ------------------------------------------

    async def _target_fingerprint(
        self, tool: Tool, context: ToolContext, arguments: dict[str, Any]
    ) -> tuple[bytes | None, dict[str, Any] | None, ToolResult | None]:
        """Собрать отпечаток конкретной операции и человекочитаемую сводку.

        Аргумент ``counter`` разрешается в числовой ``counter_id`` (сильная
        привязка: домен мог бы резолвиться неоднозначно). Если разрешение
        требует уточнения — возвращается ``needs_input`` (это ещё не
        разрушающее действие).
        """

        work = {key: value for key, value in arguments.items() if key != "confirmation_token"}
        if "counter" in work:
            resolved = await context.resolve_counter(work["counter"])
            if isinstance(resolved, ToolResult):
                return None, None, resolved
            work["counter_id"] = resolved.id
            del work["counter"]
        canonical = {
            "action": tool.name,
            "connection_id": context.client.connection_id,
            "target": work,
        }
        blob = json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")
        fingerprint = hashlib.sha256(blob).digest()
        summary: dict[str, Any] = {"action": tool.name, "target": work}
        if "counter_id" in work:
            summary["counter_id"] = work["counter_id"]
        if "goal_id" in work:
            summary["goal_id"] = work["goal_id"]
        return fingerprint, summary, None

    async def _destructive_gate(
        self, tool: Tool, context: ToolContext, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Проверить подтверждение. Вернуть конверт (блок) или None (выполнять)."""

        policy = self.confirmations
        if policy is None:  # pragma: no cover - защищено на этапе конструирования
            raise ValidationError(
                f"Для инструмента {tool.name!r} не настроена политика подтверждений.",
                details={"tool": tool.name},
            )
        token = str(arguments.get("confirmation_token") or "")
        fingerprint, summary, needs = await self._target_fingerprint(tool, context, arguments)
        if needs is not None:
            return needs.to_dict()
        assert fingerprint is not None and summary is not None  # narrowed above

        if not token:
            pending = policy.request(fingerprint, summary)
            return ToolResult.confirmation_required(
                confirmation_id=pending.nonce,
                question=self._confirmation_question(summary),
                data=self._confirmation_view(policy, pending, summary),
            ).to_dict()

        status, _pending = policy.verify_and_consume(token, fingerprint)
        if status == "ok":
            return None
        if status in ("expired", "mismatch"):
            fresh = policy.request(fingerprint, summary)
            return ToolResult.confirmation_required(
                confirmation_id=fresh.nonce,
                question=self._confirmation_question(summary),
                data=self._confirmation_view(policy, fresh, summary),
                reason=status,
            ).to_dict()
        # malformed / unknown / reused / tampered — сигнал безопасности:
        # ошибка, разрушающее действие не выполняется.
        raise ValidationError(
            f"Подтверждение разрушающей операции отклонено ({status}).",
            details={"reason": status, "action": tool.name},
        )

    @staticmethod
    def _confirmation_question(summary: dict[str, Any]) -> str:
        target = summary.get("target", {})
        counter_id = summary.get("counter_id")
        goal_id = target.get("goal_id") if isinstance(target, dict) else None
        if counter_id is not None and goal_id is not None:
            return (
                f"Требуется явное подтверждение: удалить цель {goal_id} "
                f"в счётчике {counter_id}? Операция необратима."
            )
        return (
            f"Требуется явное подтверждение разрушающей операции "
            f"{summary.get('action', 'unknown')}. Операция необратима."
        )

    @staticmethod
    def _confirmation_view(
        policy: ConfirmationPolicy, pending: PendingConfirmation, summary: dict[str, Any]
    ) -> dict[str, Any]:
        view = dict(summary)
        view["expires_in_seconds"] = policy.remaining(pending.nonce)
        view["reversible"] = False
        return view



def _as_envelope(result: Any) -> dict[str, Any]:
    """Привести ответ обработчика к конверту."""

    if isinstance(result, ToolResult):
        return result.to_dict()
    if isinstance(result, Goal):
        return ToolResult.ok(result.model_dump(exclude_none=True)).to_dict()
    if isinstance(result, dict) and "status" in result and result.get("status") in {
        "ok",
        "needs_input",
        "error",
        "confirmation_required",
    }:
        return result
    return ToolResult.ok(result).to_dict()


__all__ = [
    "COUNTER_PARAM",
    "DATE_PARAM",
    "ConfirmationPolicy",
    "Handler",
    "PendingConfirmation",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "ToolSafety",
    "strict_object",
]
