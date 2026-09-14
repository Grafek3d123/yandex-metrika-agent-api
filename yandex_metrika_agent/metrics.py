"""Словарь человеческих названий метрик и измерений Reports API.

AI-агент и пользователь оперируют понятиями «посещаемость», «источники»,
«страницы», «конверсия», а не ``ym:s:visits`` и ``ym:s:trafficSource``. Здесь
живёт отображение первых во вторые и обратно, а также проверка корректности.

Идентификаторы взяты только из актуальной документации Reports API
(``https://yandex.com/dev/metrika/ru/stat/``):

* метрики визитов — префикс ``ym:s:``;
* цель — параметризованные ``ym:s:goal<id>reaches`` и
  ``ym:s:goal<id>conversionRate``.

Словарь — это удобные псевдонимы. Авторитетный же список доступных для
конкретного счётчика метрик/измерений отдаёт ``GET /stat/v1/metrics`` —
:class:`MetricDirectory` умеет сверять псевдонимы с ним, когда справочник
получен из API.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from yandex_metrika_agent.errors import ValidationError
from yandex_metrika_agent.models import MetricGroupPage

#: Человеческий псевдоним метрики -> идентификатор Reports API.
#: Значения соответствуют официальным примерам Stat API.
METRIC_ALIASES: dict[str, str] = {
    "visits": "ym:s:visits",
    "users": "ym:s:users",
    "pageviews": "ym:s:pageviews",
    "hits": "ym:s:hits",
    "views": "ym:s:pageviews",
    "bounces": "ym:s:bounces",
    "bounce_rate": "ym:s:bounceRate",
    "session_duration": "ym:s:avgVisitDurationSeconds",
    "visit_duration": "ym:s:avgVisitDurationSeconds",
    "new_users": "ym:s:newUsers",
    "depth": "ym:s:avgPageViews",
    "avg_pageviews": "ym:s:avgPageViews",
    "pages_per_visit": "ym:s:avgPageViews",
}

#: Человеческий псевдоним измерения -> идентификатор Reports API.
DIMENSION_ALIASES: dict[str, str] = {
    "date": "ym:s:date",
    "day": "ym:s:date",
    "month": "ym:s:month",
    "week": "ym:s:week",
    "hour": "ym:s:hour",
    "traffic_source": "ym:s:trafficSource",
    "source": "ym:s:trafficSource",
    "search_engine": "ym:s:searchEngine",
    "country": "ym:s:regionCountry",
    "region": "ym:s:regionArea",
    "city": "ym:s:regionCity",
    "device": "ym:s:deviceType",
    "device_type": "ym:s:deviceType",
    "browser": "ym:s:browser",
    "os": "ym:s:operatingSystemRoot",
    "operating_system": "ym:s:operatingSystemRoot",
    "language": "ym:s:language",
    "page": "ym:s:page",
    "url": "ym:s:page",
    "landing_page": "ym:s:landingPage",
    "exit_page": "ym:s:exitPage",
}

#: Обратные словари: идентификатор API -> человекочитаемое имя (для ответов).
METRIC_TITLES: dict[str, str] = {
    "ym:s:visits": "Посещаемость",
    "ym:s:users": "Посетители",
    "ym:s:pageviews": "Просмотры страниц",
    "ym:s:hits": "Хиты",
    "ym:s:bounces": "Отказы",
    "ym:s:bounceRate": "Доля отказов",
    "ym:s:avgVisitDurationSeconds": "Среднее время на сайте",
    "ym:s:newUsers": "Новые посетители",
    "ym:s:avgPageViews": "Глубина просмотра",
}

DIMENSION_TITLES: dict[str, str] = {
    "ym:s:date": "Дата",
    "ym:s:month": "Месяц",
    "ym:s:week": "Неделя",
    "ym:s:hour": "Час",
    "ym:s:trafficSource": "Источник трафика",
    "ym:s:searchEngine": "Поисковая система",
    "ym:s:regionCountry": "Страна",
    "ym:s:regionArea": "Регион",
    "ym:s:regionCity": "Город",
    "ym:s:deviceType": "Тип устройства",
    "ym:s:browser": "Браузер",
    "ym:s:operatingSystemRoot": "Операционная система",
    "ym:s:language": "Язык",
    "ym:s:page": "Страница",
    "ym:s:landingPage": "Входная страница",
    "ym:s:exitPage": "Выходная страница",
}

#: Максимумы из документации Reports API (``https://.../intro/quotas``).
#: В запросе можно передать до 20 метрик и до 10 измерений.
MAX_METRICS = 20
MAX_DIMENSIONS = 10

#: Идентификатор метрики/измерения: ``ym:s:name`` или ``ym:pv:name``.
_IDENTIFIER_RE = re.compile(r"^ym:(s|pv):[A-Za-z0-9_]+$")

#: Параметризованная метрика цели: ``ym:s:goal<id>reaches`` / ``...conversionRate``.
_GOAL_METRIC_RE = re.compile(r"^ym:s:goal(\d+)(reaches|conversionRate)$")


def _normalize_alias(name: str) -> str:
    """Привести человеческое имя к ключу словаря: нижний регистр, ``_`` вместо пробелов/дефисов."""

    return " ".join(name.strip().lower().replace("-", " ").split()).replace(" ", "_")


def is_api_identifier(value: str) -> bool:
    """Похоже ли значение на идентификатор Reports API (в т. ч. метрику цели)."""

    text = (value or "").strip()
    return bool(_IDENTIFIER_RE.match(text) or _GOAL_METRIC_RE.match(text))


@dataclass(frozen=True)
class GoalMetric:
    """Параметризованная метрика цели.

    Args:
        goal_id: идентификатор цели.
        kind: ``reaches`` (достижения) или ``conversionRate`` (конверсия).
    """

    goal_id: int
    kind: str = "reaches"

    def __post_init__(self) -> None:
        if self.goal_id <= 0:
            raise ValidationError(
                "goal_id должен быть положительным.",
                details={"goal_id": self.goal_id},
            )
        if self.kind not in {"reaches", "conversionRate"}:
            raise ValidationError(
                "Метрика цели бывает reaches или conversionRate.",
                details={"kind": self.kind},
            )

    @property
    def api_name(self) -> str:
        """Идентификатор для параметра ``metrics``."""

        return f"ym:s:goal{self.goal_id}{self.kind}"

    @property
    def title(self) -> str:
        """Человеческое название метрики цели."""

        return f"Цель {self.goal_id}: {'достижения' if self.kind == 'reaches' else 'конверсия'}"


def goal_reaches(goal_id: int) -> str:
    """Идентификатор метрики «достижения цели»."""

    return GoalMetric(goal_id, "reaches").api_name


def goal_conversion(goal_id: int) -> str:
    """Идентификатор метрики «конверсия цели»."""

    return GoalMetric(goal_id, "conversionRate").api_name


class MetricDirectory:
    """Справочник метрик/измерений для переводов «человек -> API».

    Работает в двух режимах:

    * без аргументов — использует только словари-псевдонимы этого модуля;
    * с :class:`~yandex_metrika_agent.models.MetricGroupPage` (ответ
      ``GET /stat/v1/metrics``) — сверяет имена с фактическим списком счётчика
      и умеет предлагать близкие варианты при опечатке.
    """

    def __init__(self, page: MetricGroupPage | None = None) -> None:
        self._page = page
        self._known_metrics: set[str] | None = None
        self._known_dimensions: set[str] | None = None
        if page is not None:
            self._known_metrics = {item.metric for item in page.all_metrics}
            self._known_dimensions = {item.metric for item in page.all_dimensions}

    @property
    def is_live(self) -> bool:
        """Получен ли справочник из API (а не только из псевдонимов)."""

        return self._page is not None

    # --- Метрики -------------------------------------------------------------

    def metric(self, name: str, *, allow_unknown: bool = False) -> str:
        """Перевести метрику в идентификатор API.

        Принимает человеческий псевдоним (``visits``) или готовый идентификатор
        (``ym:s:visits``, ``ym:s:goal123reaches``). При неизвестном имени
        бросает :class:`ValidationError` с подсказками, если включён строгий
        режим.
        """

        return self._resolve(
            name,
            aliases=METRIC_ALIASES,
            known=self._known_metrics,
            allow_unknown=allow_unknown,
            what="метрики",
            suggestions=self.suggest_metrics,
        )

    def dimension(self, name: str, *, allow_unknown: bool = False) -> str:
        """Перевести измерение в идентификатор API (см. :meth:`metric`)."""

        return self._resolve(
            name,
            aliases=DIMENSION_ALIASES,
            known=self._known_dimensions,
            allow_unknown=allow_unknown,
            what="измерения",
            suggestions=self.suggest_dimensions,
        )

    def metrics(self, names: Iterable[str], *, allow_unknown: bool = False) -> list[str]:
        """Перевести список метрик с дедупликацией, сохранив порядок."""

        return _dedupe([self.metric(name, allow_unknown=allow_unknown) for name in names])

    def dimensions(self, names: Iterable[str], *, allow_unknown: bool = False) -> list[str]:
        """Перевести список измерений с дедупликацией, сохранив порядок."""

        return _dedupe([self.dimension(name, allow_unknown=allow_unknown) for name in names])

    # --- Обратный перевод ----------------------------------------------------

    def title(self, api_name: str) -> str:
        """Человеческое имя по идентификатору API (для ответов агенту)."""

        if api_name in METRIC_TITLES:
            return METRIC_TITLES[api_name]
        if api_name in DIMENSION_TITLES:
            return DIMENSION_TITLES[api_name]
        if self._page is not None:
            item = self._page.lookup(api_name)
            if item is not None and item.title:
                return item.title
        match = _GOAL_METRIC_RE.match(api_name)
        if match:
            return GoalMetric(int(match.group(1)), match.group(2)).title
        return api_name

    def humanize(self, api_name: str) -> str:
        """Человеческое имя или обратный псевдоним, если он есть."""

        for alias, ident in METRIC_ALIASES.items():
            if ident == api_name:
                return alias
        for alias, ident in DIMENSION_ALIASES.items():
            if ident == api_name:
                return alias
        return api_name

    # --- Подсказки -----------------------------------------------------------

    def suggest_metrics(self, query: str, *, limit: int = 8) -> list[str]:
        """Близкие идентификаторы метрик по опечатке/фрагменту."""

        return self._suggest(query, self._known_metrics or set(METRIC_ALIASES.values()), limit)

    def suggest_dimensions(self, query: str, *, limit: int = 8) -> list[str]:
        """Близкие идентификаторы измерений по опечатке/фрагменту."""

        return self._suggest(query, self._known_dimensions or set(DIMENSION_ALIASES.values()), limit)

    # --- Внутреннее ----------------------------------------------------------

    def _resolve(
        self,
        name: str,
        *,
        aliases: dict[str, str],
        known: set[str] | None,
        allow_unknown: bool,
        what: str,
        suggestions: Callable[[str], list[str]],
    ) -> str:
        text = (name or "").strip()
        if not text:
            raise ValidationError(f"Пустое {what}.")
        if is_api_identifier(text):
            resolved = text
        else:
            key = _normalize_alias(text)
            resolved = aliases.get(key, "")
        if resolved:
            # Если справочник из API и имени в нём нет — это ошибка, а не тишина.
            goal_parametrized = bool(_GOAL_METRIC_RE.match(resolved))
            if (
                known is not None
                and resolved not in known
                and not goal_parametrized
                and not allow_unknown
            ):
                raise ValidationError(
                    f"{what} {name!r} нет в справочнике счётчика ({resolved}).",
                    details={"requested": name, "candidates": suggestions(name)},
                )
            return resolved
        if allow_unknown:
            # Разрешаем пройти дальше как есть — вызывающий сам решит.
            return text
        raise ValidationError(
            f"Неизвестное {what}: {name!r}.",
            details={"requested": name, "candidates": suggestions(name)},
        )

    def _suggest(self, query: str, pool: set[str], limit: int) -> list[str]:
        needle = _normalize_alias(query).replace("_", "")
        scored: list[tuple[int, str]] = []
        for ident in pool:
            hay = ident.lower().replace("ym:s:", "").replace("ym:pv:", "").replace("_", "")
            if needle and (needle in hay or hay in needle):
                scored.append((abs(len(hay) - len(needle)), ident))
        scored.sort(key=lambda pair: pair[0])
        return [ident for _, ident in scored[:limit]]


def _dedupe(values: list[str]) -> list[str]:
    """Убрать повторы, сохранив порядок."""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


#: Справочник по умолчанию (только псевдонимы, без обращения к API).
DEFAULT_DIRECTORY = MetricDirectory()


__all__ = [
    "DEFAULT_DIRECTORY",
    "DIMENSION_ALIASES",
    "DIMENSION_TITLES",
    "MAX_DIMENSIONS",
    "MAX_METRICS",
    "METRIC_ALIASES",
    "METRIC_TITLES",
    "GoalMetric",
    "MetricDirectory",
    "goal_conversion",
    "goal_reaches",
    "is_api_identifier",
]
