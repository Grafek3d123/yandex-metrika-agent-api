"""GoalPlanner: человеческое описание -> намерение цели Метрики.

Пользователь говорит «цель на отправку формы» или «цель при попадании на
/thank-you». Планировщик определяет тип цели и извлекает существенные значения
(URL, номер телефона, число секунд), которые встречаются в тексте.

Главное правило: если для корректной цели нужно значение, которого нет в тексте
и его не передал агент (например, имя JS-события для action-цели), планировщик
НЕ выдумывает его. Он возвращает статус ``needs_input`` и вопрос пользователю.
Создавать цель можно только в статусе ``ready``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from yandex_metrika_agent.errors import ValidationError
from yandex_metrika_agent.goals import (
    action_goal,
    depth_goal,
    email_goal,
    file_goal,
    messenger_goal,
    phone_goal,
    search_goal,
    social_goal,
    url_goal,
    visit_duration_goal,
)
from yandex_metrika_agent.models import Goal, GOAL_TYPES


class PlanStatus(str, Enum):
    """Готовность плана к созданию цели."""

    READY = "ready"
    NEEDS_INPUT = "needs_input"
    UNKNOWN = "unknown"


#: Типы целей, для которых нужно одно значение-условие.
_VALUE_FIELDS: dict[str, tuple[str, str]] = {
    # type: (имя поля, вопрос пользователю)
    "action": ("event", "Какое JavaScript-событие отправляет сайт (reachGoal('<имя>'))?"),
    "url": ("url", "На какой URL должна срабатывать цель (например, /thank-you)?"),
    "phone": ("phone", "Какой номер телефона считается целью (в формате +7...)?"),
    "email": ("email", "Какой email-адрес считается целью?"),
    "file": ("filename", "Файл какого имени/расширения нужно считать целью (например, .pdf)?"),
    "messenger": ("platform", "В какой мессенджер переход (whatsapp, telegram, viber)?"),
    "search": ("param", "Как называется GET-параметр поиска на сайте?"),
    "social": ("network", "В какую соцсеть переход (vk, facebook, twitter)?"),
}

#: Типы целей с числовым параметром.
_NUMBER_FIELDS: dict[str, tuple[str, str, int]] = {
    "number": ("depth", "Сколько просмотров страниц составляет цель?", 2),
    "visit_duration": ("seconds", "Сколько секунд визита составляет цель?", 30),
}

#: Ключевые слова -> тип цели (порядок важен: более специфичные раньше).
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("messenger", ("whatsapp", "telegram", "viber", "мессендж", "вайбер", "телеграм")),
    ("social", ("соцсет", "вконтакт", "facebook", "twitter", "vk")),
    ("payment_system", ("платёжн", "платежн", "эквайринг", "оплата")),
    ("file", ("скачат", "файл", "download", ".pdf", ".docx", ".xlsx")),
    ("email", ("email", "e-mail", "почт", "письм")),
    ("phone", ("телефон", "позвон", "звонок", "номер")),
    ("search", ("поиск по", "поисков"),),
    ("visit_duration", ("секунд", "минут", "врем", "duration")),
    ("number", ("глубин", "просмотр", "страниц")),
    ("url", ("url", "url-адрес", "адрес", "страниц", "/")),
    ("action", ("форм", "заявк", "нажат", "кнопк", "событ", "reachgoal", "submit", "клик")),
]

_URL_RE = re.compile(r"(?<!\w)(/[^\s'\"<>]+)")
_PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{7,}\d")
_SECONDS_RE = re.compile(r"(\d+)\s*(?:сек|секунд|s\b)", re.IGNORECASE)
_MINUTES_RE = re.compile(r"(\d+)\s*(?:мин|minute)", re.IGNORECASE)
_DEPTH_RE = re.compile(r"(\d+)\s*(?:страниц|page)", re.IGNORECASE)


@dataclass
class GoalPlan:
    """Результат планирования цели.

    Args:
        status: можно ли уже создавать цель.
        goal_type: определённый тип цели (или ``None``).
        name: название цели.
        params: извлечённые/переданные параметры цели.
        missing: имена полей, которые нужно запросить у пользователя.
        question: готовый вопрос пользователю.
        reason: пояснение для агента.
    """

    status: PlanStatus
    goal_type: str | None = None
    name: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    question: str | None = None
    reason: str | None = None

    @property
    def is_ready(self) -> bool:
        """Можно ли строить цель."""

        return self.status is PlanStatus.READY

    def to_dict(self) -> dict[str, Any]:
        """Словарь для JSON-ответа агенту."""

        return {
            "status": self.status.value,
            "goal_type": self.goal_type,
            "name": self.name,
            "params": self.params,
            "missing": self.missing,
            "question": self.question,
            "reason": self.reason,
        }


class GoalPlanner:
    """Переводит человеческое описание в :class:`GoalPlan` и в объект :class:`Goal`."""

    def plan(
        self,
        description: str,
        *,
        name: str | None = None,
        goal_type: str | None = None,
        **overrides: Any,
    ) -> GoalPlan:
        """Построить план цели по описанию.

        Args:
            description: фраза пользователя («цель на отправку формы»).
            name: название цели; если не задано — выводится из описания.
            goal_type: явный тип цели, если агент уже его определил.
            **overrides: существенные значения (``event``, ``url``, ``phone`` ...),
                которые агент собрал из диалога.
        """

        text = (description or "").strip()
        if not text and not goal_type:
            raise ValidationError("Пустое описание цели.")
        lowered = text.lower()
        resolved_type = self._resolve_type(goal_type, lowered)
        goal_name = (name or self._derive_name(text)).strip() or "Новая цель"

        params: dict[str, Any] = {k: v for k, v in overrides.items() if v not in (None, "")}
        self._extract_values(resolved_type, text, params)

        if resolved_type is None:
            return GoalPlan(
                status=PlanStatus.UNKNOWN,
                name=goal_name,
                params=params,
                reason="Не удалось определить тип цели по описанию — уточните, что должно считаться достижением.",
                question="Что должно считаться достижением цели: событие на странице, визит по URL, клик по телефону/ссылке или что-то ещё?",
            )

        missing: list[str] = []
        question: str | None = None

        if resolved_type in _VALUE_FIELDS:
            wanted, hint = _VALUE_FIELDS[resolved_type]
            if wanted not in params:
                missing.append(wanted)
                question = hint
        elif resolved_type in _NUMBER_FIELDS:
            wanted, hint, default = _NUMBER_FIELDS[resolved_type]
            if wanted not in params:
                params[wanted] = default
                # Числовой параметр можно подставить по умолчанию, но предупредить.

        if resolved_type == "payment_system":
            pass  # условий нет.

        if missing:
            return GoalPlan(
                status=PlanStatus.NEEDS_INPUT,
                goal_type=resolved_type,
                name=goal_name,
                params=params,
                missing=missing,
                question=question,
                reason="Для этой цели не хватает существенного значения — сначала запросите его.",
            )
        return GoalPlan(
            status=PlanStatus.READY,
            goal_type=resolved_type,
            name=goal_name,
            params=params,
            reason="Тип и параметры цели определены, можно создавать.",
        )

    def build_goal(self, plan: GoalPlan) -> Goal:
        """Собрать объект :class:`Goal` из готового плана.

        Raises:
            ValidationError: план не готов (не хватает данных или тип не определён).
        """

        if plan.status is not PlanStatus.READY or not plan.goal_type:
            raise ValidationError(
                "План цели не готов к созданию.",
                details={"status": plan.status.value, "missing": plan.missing},
            )
        goal_type = plan.goal_type
        name = plan.name or "Новая цель"
        p = plan.params
        if goal_type == "action":
            return action_goal(name=name, event=str(p["event"]), match=str(p.get("match", "exact")))
        if goal_type == "url":
            return url_goal(name=name, url=str(p["url"]), match=str(p.get("match", "contain")))
        if goal_type == "phone":
            return phone_goal(name=name, phone=str(p["phone"]))
        if goal_type == "email":
            return email_goal(name=name, email=str(p["email"]))
        if goal_type == "file":
            return file_goal(name=name, filename=str(p["filename"]))
        if goal_type == "messenger":
            return messenger_goal(name=name, platform=str(p["platform"]))
        if goal_type == "search":
            return search_goal(name=name, param=str(p["param"]))
        if goal_type == "social":
            return social_goal(name=name, network=str(p["network"]))
        if goal_type == "number":
            return depth_goal(name=name, depth=int(p["depth"]))
        if goal_type == "visit_duration":
            return visit_duration_goal(name=name, seconds=int(p["seconds"]))
        raise ValidationError(
            f"Не поддерживаемый планировщиком тип цели: {goal_type}.",
            details={"allowed": list(GOAL_TYPES)},
        )

    # --- Внутреннее ----------------------------------------------------------

    def _resolve_type(self, explicit: str | None, lowered: str) -> str | None:
        if explicit:
            key = explicit.strip().lower()
            if key in GOAL_TYPES:
                return key
            raise ValidationError(
                f"Неизвестный тип цели: {explicit}.",
                details={"allowed": list(GOAL_TYPES)},
            )
        for goal_type, words in _KEYWORDS:
            if any(word in lowered for word in words):
                return goal_type
        return None

    def _derive_name(self, text: str) -> str:
        """Название из описания: убрать служебные слова «цель на/при»."""

        cleaned = re.sub(r"^(цель|goal)\s*(на|при|для|по)?\s*", "", text.strip(), flags=re.IGNORECASE)
        cleaned = cleaned.strip(" .,:")
        if not cleaned:
            return text.strip() or "Новая цель"
        return cleaned[:200]

    def _extract_values(self, goal_type: str | None, text: str, params: dict[str, Any]) -> None:
        """Достать из текста значения, которые там явно есть."""

        if goal_type == "url" and "url" not in params:
            match = _URL_RE.search(text)
            if match:
                params["url"] = match.group(1)
        elif goal_type == "phone" and "phone" not in params:
            match = _PHONE_RE.search(text)
            if match:
                params["phone"] = match.group(0).strip()
        elif goal_type == "visit_duration" and "seconds" not in params:
            sec = _SECONDS_RE.search(text)
            if sec:
                params["seconds"] = int(sec.group(1))
            else:
                minutes = _MINUTES_RE.search(text)
                if minutes:
                    params["seconds"] = int(minutes.group(1)) * 60
        elif goal_type == "number" and "depth" not in params:
            match = _DEPTH_RE.search(text)
            if match:
                params["depth"] = int(match.group(1))


__all__ = [
    "GoalPlan",
    "GoalPlanner",
    "PlanStatus",
]
