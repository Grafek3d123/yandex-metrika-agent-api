"""Управление целями счётчика (Management API v1).

Методы API (проверено по официальной документации ``management/openapi``):

* ``GET    /management/v1/counter/{counterId}/goals`` — список
  (ответ ``{"goals": [...]}``, старый ``{"items": [...]}`` тоже поддерживается);
* ``GET    /management/v1/counter/{counterId}/goal/{goalId}`` — одна цель
  (ответ ``{"goal": {...}}``);
* ``POST   /management/v1/counter/{counterId}/goals`` — создание (тело ``{"goal": {...}}``);
* ``PUT    /management/v1/counter/{counterId}/goal/{goalId}`` — изменение;
* ``DELETE /management/v1/counter/{counterId}/goal/{goalId}`` — удаление
  (ответ ``{"success": true}``).

Агенту важно не создавать повторы: ``ensure_goal`` сначала ищет такую же цель
по существенным параметрам (тип + условия + шаги + глубина + длительность,
название не учитывается) и возвращает найденную, помечая ``created=False``.
Совпадение только по названию дубликатом не считается: такое цель-имя может
относиться к другому условию — создание выполняется, а в результат попадает
предупреждение.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from typing import Any

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.errors import NotFoundError, ValidationError
from yandex_metrika_agent.log import get_logger
from yandex_metrika_agent.models import GOAL_TYPES, Goal, GoalCondition

_LOGGER = get_logger("goals")


def _validate_id(value: int, name: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValidationError(
            f"{name} должен быть положительным целым числом.",
            details={name: value},
        )
    return value


def _counter_path(counter_id: int) -> str:
    _validate_id(counter_id, "counter_id")
    return f"/management/v1/counter/{counter_id}/goals"


def _goal_path(counter_id: int, goal_id: int) -> str:
    _validate_id(counter_id, "counter_id")
    _validate_id(goal_id, "goal_id")
    return f"/management/v1/counter/{counter_id}/goal/{goal_id}"


def _unwrap_list(payload: Any, *keys: str) -> list[dict[str, Any]]:
    """Достать список целей из ответа API (обёртки ``goals``/``items``)."""

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in (*keys, "goals", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _unwrap_goal(payload: Any) -> dict[str, Any]:
    """Достать объект цели из ответа API."""

    if isinstance(payload, dict):
        value = payload.get("goal")
        if isinstance(value, dict):
            return value
        return payload
    return {}


@dataclass
class GoalResult:
    """Результат создания/поиска цели."""

    goal: Goal
    created: bool
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Словарь для JSON-вывода CLI и ответов агенту."""

        return {
            "created": self.created,
            "reason": self.reason,
            "warnings": self.warnings,
            "goal": self.goal.model_dump(exclude_none=True),
            "description": self.goal.describe(),
        }


@dataclass
class GoalService:
    """CRUD целей и высокоуровневые конструкторы.

    Args:
        client: клиент API Метрики.
        check_duplicates: проверять наличие такой цели перед созданием.
    """

    client: MetrikaClient
    check_duplicates: bool = True

    # --- Чтение --------------------------------------------------------------

    async def list(self, counter_id: int, *, fields: str | None = None) -> builtins.list[Goal]:
        """Список целей счётчика."""

        payload = await self.client.get_json(
            _counter_path(counter_id),
            params={"fields": fields} if fields else None,
        )
        goals = [Goal.model_validate(item) for item in _unwrap_list(payload)]
        _LOGGER.debug("Счётчик %s: целей %s", counter_id, len(goals))
        return goals

    async def get(self, counter_id: int, goal_id: int) -> Goal:
        """Цель по идентификатору (``GET .../goal/{goalId}``)."""

        payload = await self.client.get_json(_goal_path(counter_id, goal_id))
        data = _unwrap_goal(payload)
        if not data:
            raise NotFoundError(
                f"Цель {goal_id} не найдена на счётчике {counter_id}.",
                details={"counter_id": counter_id, "goal_id": goal_id},
            )
        return Goal.model_validate(data)

    # --- Запись --------------------------------------------------------------

    async def create(self, counter_id: int, goal: Goal) -> Goal:
        """Создать цель и вернуть её же с присвоенным ``id``.

        Тип создаваемой цели проверяется строго: допустимы только известные
        создаваемые типы (:data:`GOAL_TYPES`). Автоцели Метрики (``contact_data``,
        ``cdp_order_*``) модель читает, но создать их через этот метод нельзя —
        так сохраняется защита от выдуманного типа на записи.
        """

        if goal.type not in GOAL_TYPES:
            raise ValidationError(
                f"Нельзя создать цель неизвестного типа {goal.type!r}. "
                f"Допустимо: {', '.join(GOAL_TYPES)}.",
                details={"type": goal.type, "allowed": list(GOAL_TYPES)},
            )
        try:
            goal.validate_for_write()
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        payload = await self.client.post_json(
            _counter_path(counter_id),
            {"goal": goal.to_request()},
        )
        created = Goal.model_validate(_unwrap_goal(payload))
        _LOGGER.info("Создана цель %s (счётчик %s)", created.id, counter_id)
        return created if created.id is not None else goal.model_copy(update={"id": None})

    async def update(self, counter_id: int, goal: Goal) -> Goal:
        """Изменить цель (``PUT .../goal/{goalId}``, обязателен ``id``)."""

        if goal.id is None:
            raise ValidationError(
                "Для изменения цели нужен id.",
                details={"name": goal.name},
            )
        payload = await self.client.put_json(
            _goal_path(counter_id, goal.id),
            {"goal": goal.to_request()},
        )
        updated = _unwrap_goal(payload)
        return Goal.model_validate(updated) if updated else goal

    async def delete(self, counter_id: int, goal_id: int) -> dict[str, Any]:
        """Удалить цель (``DELETE .../goal/{goalId}``)."""

        payload = await self.client.delete_json(_goal_path(counter_id, goal_id))
        _LOGGER.info("Удалена цель %s (счётчик %s)", goal_id, counter_id)
        return payload if isinstance(payload, dict) else {}

    async def ensure_goal(self, counter_id: int, goal: Goal) -> GoalResult:
        """Создать цель, если такой ещё нет (идемпотентность).

        Возвращает найденную цель с ``created=False``, если она уже есть:
        агент может безопасно повторять запрос после сбоя. Совпадение только
        по названию дубликатом не считается — создание выполняется, но в
        ``GoalResult.warnings`` попадает предупреждение о похожем имени.
        """

        if not self.check_duplicates:
            return GoalResult(goal=await self.create(counter_id, goal), created=True)

        exact, by_name = await self.find_duplicates(counter_id, goal)
        if exact:
            existing = exact[0]
            _LOGGER.info(
                "Цель уже существует: id=%s name=%s",
                existing.id,
                existing.name,
            )
            return GoalResult(
                goal=existing,
                created=False,
                reason=(
                    f"На счётчике уже есть цель {existing.title!r} с id={existing.id}: "
                    f"{existing.describe()}. Создание пропущено."
                ),
            )
        warnings: list[str] = []
        if by_name:
            other = by_name[0]
            warnings.append(
                f"На счётчике есть цель с таким же названием {other.title!r} "
                f"(id={other.id}), но другие условия — создана новая цель."
            )
        return GoalResult(
            goal=await self.create(counter_id, goal),
            created=True,
            warnings=warnings,
        )

    async def find_duplicates(
        self, counter_id: int, goal: Goal
    ) -> tuple[builtins.list[Goal], builtins.list[Goal]]:
        """Разделить цели на полные дубликаты и совпадения только по имени.

        Полным совпадением считаем одинаковый тип и существенные параметры
        (условия/глубина/длительность/шаги). Название сравнивается
        нечувствительно к регистру и пробелам, но само по себе дубликатом
        не считается.
        """

        wanted = goal.signature()
        wanted_name = _normalize_name(goal.name)
        exact: builtins.list[Goal] = []
        by_name: builtins.list[Goal] = []
        for existing in await self.list(counter_id):
            try:
                existing_signature = existing.signature()
            except ValueError:  # цель с необычными полями — сравниваем только имя
                existing_signature = ()
            if existing_signature and existing_signature == wanted:
                exact.append(existing)
            elif _normalize_name(existing.name) == wanted_name:
                by_name.append(existing)
        return exact, by_name

    async def find_similar(self, counter_id: int, goal: Goal) -> builtins.list[Goal]:
        """Полные дубликаты цели (историческое имя метода)."""

        exact, _ = await self.find_duplicates(counter_id, goal)
        return exact

    # --- Высокоуровневые конструкторы ---------------------------------------

    async def create_action_goal(
        self,
        counter_id: int,
        *,
        name: str,
        event: str,
        match: str = "exact",
        price: float | None = None,
    ) -> GoalResult:
        """Цель «JS-событие»: срабатывает на ``reachGoal('<event>')``."""

        return await self.ensure_goal(
            counter_id,
            action_goal(name=name, event=event, match=match, price=price),
        )

    async def create_url_goal(
        self,
        counter_id: int,
        *,
        name: str,
        url: str,
        match: str = "contain",
        price: float | None = None,
    ) -> GoalResult:
        """Цель «Посещение страницы»."""

        return await self.ensure_goal(
            counter_id,
            url_goal(name=name, url=url, match=match, price=price),
        )

    async def create_phone_goal(
        self,
        counter_id: int,
        *,
        name: str,
        phone: str,
        hide_number: bool = False,
    ) -> GoalResult:
        """Цель «Клик по номеру телефона»."""

        return await self.ensure_goal(
            counter_id, phone_goal(name=name, phone=phone, hide_number=hide_number)
        )

    async def create_email_goal(self, counter_id: int, *, name: str, email: str) -> GoalResult:
        """Цель «Клик по email»."""

        return await self.ensure_goal(counter_id, email_goal(name=name, email=email))

    async def create_file_goal(self, counter_id: int, *, name: str, filename: str) -> GoalResult:
        """Цель «Скачивание файлов»."""

        return await self.ensure_goal(
            counter_id, file_goal(name=name, filename=filename)
        )

    async def create_messenger_goal(
        self, counter_id: int, *, name: str, platform: str
    ) -> GoalResult:
        """Цель «Переход в мессенджер» (whatsapp, telegram, viber, ...)."""

        return await self.ensure_goal(counter_id, messenger_goal(name=name, platform=platform))

    async def create_chat_goal(self, counter_id: int, *, name: str, platform: str) -> GoalResult:
        """Цель «Переход в чат» (типы ``chat`` и ``messenger`` в API)."""

        return await self.ensure_goal(counter_id, chat_goal(name=name, platform=platform))

    async def create_search_goal(self, counter_id: int, *, name: str, param: str) -> GoalResult:
        """Цель «Поиск по сайту»: параметр запроса, куда попадает фраза поиска."""

        return await self.ensure_goal(counter_id, search_goal(name=name, param=param))

    async def create_social_goal(self, counter_id: int, *, name: str, network: str) -> GoalResult:
        """Цель «Переход в соцсети» (vkontakte, facebook, twitter, ...)."""

        return await self.ensure_goal(counter_id, social_goal(name=name, network=network))

    async def create_depth_goal(self, counter_id: int, *, name: str, depth: int) -> GoalResult:
        """Цель «Количество просмотров»."""

        return await self.ensure_goal(counter_id, depth_goal(name=name, depth=depth))

    async def create_visit_duration_goal(
        self,
        counter_id: int,
        *,
        name: str,
        seconds: int,
    ) -> GoalResult:
        """Цель «Продолжительность визита»."""

        return await self.ensure_goal(counter_id, visit_duration_goal(name=name, seconds=seconds))

    async def create_payment_system_goal(self, counter_id: int, *, name: str) -> GoalResult:
        """Цель «Возврат из платёжной системы»."""

        return await self.ensure_goal(counter_id, payment_system_goal(name=name))

    async def create_composite_goal(
        self,
        counter_id: int,
        *,
        name: str,
        steps: builtins.list[Goal],
        price: float | None = None,
    ) -> GoalResult:
        """Составная цель. Шаги должны быть в том же запросе созданы заранее."""

        return await self.ensure_goal(
            counter_id, composite_goal(name=name, steps=steps, price=price)
        )


def _normalize_name(name: str) -> str:
    """Название для сравнения: нижний регистр, схлопнутые пробелы."""

    return " ".join(name.strip().lower().split())


def _value_condition(value: str, match: str, *, what: str) -> GoalCondition:
    """Условие вида «значение + способ сравнения»."""

    if not value or not value.strip():
        raise ValidationError(f"Не задано значение условия цели ({what}).")
    operator = (match or "exact").strip().lower()
    aliases = {
        "equals": "exact",
        "eq": "exact",
        "равно": "exact",
        "совпадает": "exact",
        "contains": "contain",
        "содержит": "contain",
        "startsWith": "start",
        "начинается": "start",
        "regex": "regexp",
        "регексп": "regexp",
    }
    operator = aliases.get(operator, operator)
    return GoalCondition(type=operator, url=value.strip())


# --- Конструкторы целей (без обращения к API) --------------------------------


def action_goal(*, name: str, event: str, match: str = "exact", price: float | None = None) -> Goal:
    """Цель «JS-событие»: имя события из ``ym.reachGoal('<event>')``."""

    return Goal(
        name=name,
        type="action",
        conditions=[_value_condition(event, match, what="имя JS-события")],
        default_price=price,
    )


def url_goal(*, name: str, url: str, match: str = "contain", price: float | None = None) -> Goal:
    """Цель «Посещение страницы»."""

    return Goal(
        name=name,
        type="url",
        conditions=[_value_condition(url, match, what="URL страницы")],
        default_price=price,
    )


def phone_goal(*, name: str, phone: str, hide_number: bool = False) -> Goal:
    """Цель «Клик по номеру телефона»."""

    return Goal(
        name=name,
        type="phone",
        conditions=[_value_condition(phone, "exact", what="номер телефона")],
        hide_phone_number=hide_number,
    )


def email_goal(*, name: str, email: str) -> Goal:
    """Цель «Клик по email»."""

    return Goal(
        name=name,
        type="email",
        conditions=[_value_condition(email, "exact", what="email-адрес")],
    )


def file_goal(*, name: str, filename: str) -> Goal:
    """Цель «Скачивание файлов»: имя файла с расширением или без."""

    return Goal(
        name=name,
        type="file",
        conditions=[GoalCondition(type="file", url=filename.strip())],
    )


def messenger_goal(*, name: str, platform: str) -> Goal:
    """Цель «Переход в мессенджер»."""

    return Goal(
        name=name,
        type="messenger",
        conditions=[GoalCondition(type="messenger", url=platform.strip().lower())],
    )


def search_goal(*, name: str, param: str) -> Goal:
    """Цель «Поиск по сайту»: имя GET-параметра с фразой поиска."""

    return Goal(
        name=name,
        type="search",
        conditions=[GoalCondition(type="search", url=param.strip())],
    )


def social_goal(*, name: str, network: str) -> Goal:
    """Цель «Переход в соцсети»."""

    return Goal(
        name=name,
        type="social",
        conditions=[GoalCondition(type="social", url=network.strip().lower())],
    )


def chat_goal(*, name: str, platform: str) -> Goal:
    """Цель «Переход в чат»: официальная схема — условие ``chat_platform``."""

    if not platform or not platform.strip():
        raise ValidationError("Не задана платформа чата (platform).")
    return Goal(
        name=name,
        type="chat",
        conditions=[GoalCondition(field="chat_platform", platform=platform.strip().lower())],
    )


def depth_goal(*, name: str, depth: int, price: float | None = None) -> Goal:
    """Цель «Количество просмотров»."""

    return Goal(name=name, type="number", depth=depth, default_price=price)


def visit_duration_goal(*, name: str, seconds: int) -> Goal:
    """Цель «Продолжительность визита», секунд."""

    return Goal(name=name, type="visit_duration", duration=seconds)


def payment_system_goal(*, name: str) -> Goal:
    """Цель «Возврат из платёжной системы»: условий нет."""

    return Goal(name=name, type="payment_system")


def composite_goal(*, name: str, steps: list[Goal], price: float | None = None) -> Goal:
    """Составная цель из уже существующих целей (шаги с ``id``)."""

    if len(steps) < 2:
        raise ValidationError(
            "Составной цели нужно минимум два шага.",
            details={"steps": len(steps)},
        )
    missing = [step.name for step in steps if step.id is None]
    if missing:
        raise ValidationError(
            "Шаги составной цели должны быть созданы заранее (нет id).",
            details={"steps": missing},
        )
    payload = [{"id": step.id, "name": step.name, "type": step.type} for step in steps]
    return Goal.model_validate(
        {"name": name, "type": "step", "steps": payload, "default_price": price}
    )


__all__ = [
    "GoalResult",
    "GoalService",
    "action_goal",
    "chat_goal",
    "composite_goal",
    "depth_goal",
    "email_goal",
    "file_goal",
    "messenger_goal",
    "payment_system_goal",
    "phone_goal",
    "search_goal",
    "social_goal",
    "url_goal",
    "visit_duration_goal",
]
