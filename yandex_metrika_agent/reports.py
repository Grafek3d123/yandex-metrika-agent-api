"""Сервис отчётов (Reports API v1).

Низкоуровневый метод :meth:`ReportService.get_report` формирует запрос к
``GET /stat/v1/data`` из намерения :class:`~yandex_metrika_agent.models.ReportCommand`:
переводит человеческие метрики/измерения в ``ym:s:...``, собирает ``filters``
из DSL, валидирует ограничения API и разбирает ответ в
:class:`~yandex_metrika_agent.models.Report`.

Поверх него — AI-friendly методы, которые сами выбирают ``metrics``,
``dimensions`` и сортировку под типовой вопрос: посещаемость, источники,
страницы, статистика целей, сравнение периодов. Агент оперирует понятиями
«посещаемость», «источники», «конверсия», а не ``ym:s:``.

Синтаксис и ограничения — из документации
(``https://yandex.com/dev/metrika/ru/stat/``, ``.../intro/quotas``): до 20
метрик и до 10 измерений в запросе, единый префикс ``ym:s:``/``ym:pv:``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
import re
from typing import Any

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.errors import ValidationError
from yandex_metrika_agent.filters import Filter, as_filters, render_filters
from yandex_metrika_agent.log import get_logger
from yandex_metrika_agent.metrics import (
    MAX_DIMENSIONS,
    MAX_METRICS,
    MetricDirectory,
    goal_conversion,
    goal_reaches,
)
from yandex_metrika_agent.models import ComparisonRow, Report, ReportCommand

_LOGGER = get_logger("reports")

#: Метрики для сводки посещаемости (человеческие имена).
TRAFFIC_METRICS: tuple[str, ...] = (
    "visits",
    "users",
    "pageviews",
    "bounce_rate",
    "session_duration",
    "new_users",
)

#: Ключи, которыми отдаём сводку посещаемости агенту.
_TRAFFIC_KEYS: tuple[str, ...] = (
    "visits",
    "users",
    "pageviews",
    "bounce_rate",
    "session_duration",
    "new_users",
)

#: Значение ``group`` для отчёта по дням.
GROUP_DAY = "day"


def _fmt_date(value: date | datetime | str | None) -> str | None:
    """Привести дату к строке параметра API (ISO или относительная ``30daysAgo``)."""

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _check_prefix(api_metrics: Sequence[str], api_dimensions: Sequence[str]) -> None:
    """Все метрики и измерения должны быть одного множества (``ym:s:``/``ym:pv:``)."""

    prefixes: set[str] = set()
    for name in (*api_metrics, *api_dimensions):
        prefixes.add(name.split(":", 1)[0] + ":" + name.split(":")[1] if ":" in name else name)
    normalized = {p.split(":")[1] for p in prefixes if ":" in p}
    if len(normalized) > 1:
        raise ValidationError(
            "В одном запросе нельзя смешивать метрики визитов (ym:s:) и хитов (ym:pv:).",
            details={"prefixes": sorted(normalized)},
        )


class ReportService:
    """Формирование и разбор отчётов Reports API.

    Args:
        client: клиент API Метрики.
        directory: справочник переводов «человек -> API». По умолчанию — по
            псевдонимам модуля :mod:`yandex_metrika_agent.metrics`.
        default_limit: сколько строк запрашивать, если не указано.
    """

    def __init__(
        self,
        client: MetrikaClient,
        *,
        directory: MetricDirectory | None = None,
        default_limit: int | None = 1000,
    ) -> None:
        self.client = client
        self.directory = directory or MetricDirectory()
        self.default_limit = default_limit

    # --- Универсальный отчёт -------------------------------------------------

    async def get_report(
        self,
        command: ReportCommand | dict[str, Any],
        *,
        validate: bool = True,
        cacheable: bool = True,
    ) -> Report:
        """Выполнить произвольный отчёт и вернуть разобранный ответ.

        Args:
            command: намерение отчёта (или его dict-представление).
            validate: проверять существование и лимиты метрик/измерений.
        """

        if isinstance(command, dict):
            command = ReportCommand.model_validate(command)
        api_metrics = self._metric_names(command.metrics, validate=validate)
        api_dimensions = self._dimension_names(command.dimensions, validate=validate)
        filters = render_filters(
            as_filters(command.filters),
            directory=self.directory,
        )
        if validate:
            self._validate(command, api_metrics, api_dimensions)

        params: dict[str, Any] = {
            "id": command.counter_id,
            "metrics": ",".join(api_metrics),
        }
        if api_dimensions:
            params["dimensions"] = ",".join(api_dimensions)
        date1 = _fmt_date(command.date1)
        date2 = _fmt_date(command.date2)
        if date1 and date2:
            params["date1"] = date1
            params["date2"] = date2
        if filters:
            params["filters"] = filters
        if command.sort_by:
            params["sort"] = ",".join(
                self._sort_token(token, validate=validate) for token in command.sort_by
            )
        limit = command.limit if command.limit is not None else self.default_limit
        if limit is not None:
            params["limit"] = limit
        if command.offset:
            params["offset"] = command.offset

        payload = await self.client.get_json("/stat/v1/data", params=params, cacheable=cacheable)
        report = Report.model_validate(payload if isinstance(payload, dict) else {})
        # Подставляем имена, если API их не вернул (для нормализации строк).
        if not report.metric_names:
            report.metric_names = api_metrics
        if not report.dimension_names:
            report.dimension_names = api_dimensions
        return report

    # --- Сводки --------------------------------------------------------------

    async def get_traffic(
        self,
        counter_id: int,
        *,
        date_from: date | datetime | str | None = None,
        date_to: date | datetime | str | None = None,
        filters: Sequence[object] | None = None,
    ) -> dict[str, Any]:
        """Сводка посещаемости: визиты, посетители, просмотры, отказы, время."""

        report = await self.get_report(
            ReportCommand(
                counter_id=counter_id,
                metrics=list(TRAFFIC_METRICS),
                date1=_coerce_date(date_from),
                date2=_coerce_date(date_to),
                filters=_filter_strings(as_filters(filters), directory=self.directory),
            )
        )
        totals = self._totals_map(report)
        result: dict[str, Any] = {"counter_id": counter_id, "period": self._period(report)}
        for key in _TRAFFIC_KEYS:
            result[key] = totals.get(key)
        return result

    async def get_traffic_by_day(
        self,
        counter_id: int,
        *,
        date_from: date | datetime | str | None = None,
        date_to: date | datetime | str | None = None,
        metrics: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Динамика посещаемости по дням (строки с датой)."""

        report = await self.get_report(
            ReportCommand(
                counter_id=counter_id,
                metrics=list(metrics) if metrics else ["visits", "users"],
                dimensions=["date"],
                date1=_coerce_date(date_from),
                date2=_coerce_date(date_to),
                sort_by=["date"],
            )
        )
        return self._rows_as_dicts(report)

    async def get_sources(
        self,
        counter_id: int,
        *,
        date_from: date | datetime | str | None = None,
        date_to: date | datetime | str | None = None,
        limit: int = 10,
        dimension: str = "traffic_source",
    ) -> list[dict[str, Any]]:
        """Источники трафика по посещаемости (по убыванию)."""

        report = await self.get_report(
            ReportCommand(
                counter_id=counter_id,
                metrics=["visits", "users"],
                dimensions=[dimension],
                date1=_coerce_date(date_from),
                date2=_coerce_date(date_to),
                sort_by=["-visits"],
                limit=limit,
            )
        )
        return self._rows_as_dicts(report)

    async def get_top_pages(
        self,
        counter_id: int,
        *,
        date_from: date | datetime | str | None = None,
        date_to: date | datetime | str | None = None,
        limit: int = 10,
        landing: bool = False,
    ) -> list[dict[str, Any]]:
        """Популярные (или входные) страницы по просмотрам."""

        report = await self.get_report(
            ReportCommand(
                counter_id=counter_id,
                metrics=["pageviews", "visits"],
                dimensions=["landing_page" if landing else "page"],
                date1=_coerce_date(date_from),
                date2=_coerce_date(date_to),
                sort_by=["-pageviews"],
                limit=limit,
            )
        )
        return self._rows_as_dicts(report)

    async def get_goal_stats(
        self,
        counter_id: int,
        goal_id: int,
        *,
        date_from: date | datetime | str | None = None,
        date_to: date | datetime | str | None = None,
        by_source: bool = False,
    ) -> dict[str, Any]:
        """Достижения и конверсия цели (сводно или по источникам)."""

        dimensions = ["traffic_source"] if by_source else []
        report = await self.get_report(
            ReportCommand(
                counter_id=counter_id,
                metrics=["visits", "users"],
                dimensions=dimensions,
                date1=_coerce_date(date_from),
                date2=_coerce_date(date_to),
                include=[goal_reaches(goal_id), goal_conversion(goal_id)],
            )
        )
        reaches_metric = goal_reaches(goal_id)
        conversion_metric = goal_conversion(goal_id)
        if by_source:
            rows = self._rows_as_dicts(report, extra_metrics=[reaches_metric, conversion_metric])
            return {
                "counter_id": counter_id,
                "goal_id": goal_id,
                "period": self._period(report),
                "rows": rows,
            }
        totals = self._totals_map(report)
        return {
            "counter_id": counter_id,
            "goal_id": goal_id,
            "period": self._period(report),
            "visits": totals.get("visits"),
            "users": totals.get("users"),
            "goal_reaches": None,
            "goal_conversion_rate": None,
            "_raw_metric_names": report.metric_names,
        }

    async def compare_periods(
        self,
        counter_id: int,
        *,
        period_a: tuple[date | datetime | str, date | datetime | str],
        period_b: tuple[date | datetime | str, date | datetime | str],
        metrics: Sequence[str] = ("visits", "users"),
    ) -> list[ComparisonRow]:
        """Сравнить метрики за два периода.

        ``current`` — период B (более поздний), ``previous`` — период A.
        ``delta``/``delta_percent`` — изменение от A к B.
        """

        api_metrics = self._metric_names(metrics, validate=True)
        a_from, a_to = period_a
        b_from, b_to = period_b
        base = ReportCommand(counter_id=counter_id, metrics=list(metrics))
        report_a = await self.get_report(
            base.model_copy(update={"date1": _coerce_date(a_from), "date2": _coerce_date(a_to)})
        )
        report_b = await self.get_report(
            base.model_copy(update={"date1": _coerce_date(b_from), "date2": _coerce_date(b_to)})
        )
        totals_a = self._totals_map(report_a)
        totals_b = self._totals_map(report_b)
        rows: list[ComparisonRow] = []
        for human, api in zip(metrics, api_metrics, strict=True):
            previous = totals_a.get(human)
            current = totals_b.get(human)
            delta = None
            delta_percent = None
            if isinstance(previous, (int, float)) and isinstance(current, (int, float)):
                delta = round(current - previous, 4)
                if previous:
                    delta_percent = round((current - previous) / previous * 100, 2)
            rows.append(
                ComparisonRow(
                    label=self.directory.title(api),
                    current=current,
                    previous=previous,
                    delta=delta,
                    delta_percent=delta_percent,
                )
            )
        return rows

    # --- Разбор ответа -------------------------------------------------------

    def _totals_map(self, report: Report) -> dict[str, Any]:
        """Сопоставить человеческое имя метрики -> значение из итогов."""

        totals = report.data.totals.metrics if report.data and report.data.totals else []
        result: dict[str, float | int | None] = {}
        for index, api in enumerate(report.metric_names):
            if index >= len(totals):
                break
            result[self.directory.humanize(api)] = totals[index]
        return result

    def _rows_as_dicts(
        self,
        report: Report,
        *,
        extra_metrics: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        """Превратить строки отчёта в список нормализованных словарей.

        Каждой строке соответствуют измерения (человеческие имена -> значения)
        и метрики (человеческие имена -> значения).
        """

        rows: list[dict[str, Any]] = []
        if not report.data:
            return rows
        metric_names = list(report.metric_names)
        for extra in extra_metrics:
            if extra not in metric_names:
                metric_names.append(extra)
        for row in report.data.rows:
            item: dict[str, Any] = {}
            for index, dimension in enumerate(row.dimensions):
                key = (
                    self.directory.humanize(report.dimension_names[index])
                    if index < len(report.dimension_names)
                    else f"dimension_{index}"
                )
                item[key] = _dimension_value(dimension)
            for index, value in enumerate(row.metrics):
                if index < len(metric_names):
                    item[self.directory.humanize(metric_names[index])] = value
            if row.total is not None:
                item["total"] = row.total
            rows.append(item)
        return rows

    def _period(self, report: Report) -> dict[str, str] | None:
        """Период отчёта из ответа (min_date/max_date)."""

        if report.data and report.data.min_date and report.data.max_date:
            return {"from": report.data.min_date, "to": report.data.max_date}
        return None

    # --- Валидация и переводы ------------------------------------------------

    def _metric_names(self, names: Iterable[str], *, validate: bool) -> list[str]:
        if validate:
            return self.directory.metrics(names)
        return [self.directory.metric(name, allow_unknown=True) for name in names]

    def _dimension_names(self, names: Iterable[str], *, validate: bool) -> list[str]:
        if validate:
            return self.directory.dimensions(names)
        return [self.directory.dimension(name, allow_unknown=True) for name in names]

    def _sort_token(self, token: str, *, validate: bool) -> str:
        """Перевести токен сортировки (``-visits`` -> ``-ym:s:visits``)."""

        sign = ""
        name = token.strip()
        if name[:1] in {"-", "+"}:
            sign, name = name[0], name[1:]
        resolved = self.directory.metric(name, allow_unknown=not validate)
        return f"{sign}{resolved}"

    def _validate(
        self,
        command: ReportCommand,
        api_metrics: Sequence[str],
        api_dimensions: Sequence[str],
    ) -> None:
        if not api_metrics:
            raise ValidationError("Для отчёта нужна хотя бы одна метрика.")
        if len(api_metrics) > MAX_METRICS:
            raise ValidationError(
                f"Слишком много метрик: {len(api_metrics)} (максимум {MAX_METRICS}).",
                details={"count": len(api_metrics), "max": MAX_METRICS},
            )
        if len(api_dimensions) > MAX_DIMENSIONS:
            raise ValidationError(
                f"Слишком много измерений: {len(api_dimensions)} (максимум {MAX_DIMENSIONS}).",
                details={"count": len(api_dimensions), "max": MAX_DIMENSIONS},
            )
        _check_prefix(api_metrics, api_dimensions)


_RELATIVE_RE = re.compile(r"^(\d+)\s*days?\s*ago$", re.IGNORECASE)


def _coerce_date(value: date | datetime | str | None) -> date | None:
    """Привести вход к ``date`` для типизированного :class:`ReportCommand`.

    Поддерживает относительные значения параметра API: ``today``, ``yesterday``,
    ``NdaysAgo`` — они вычисляются от текущей даты, чтобы модель оставалась
    строго типизированной.
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    today = date.today()
    if lowered == "today":
        return today
    if lowered == "yesterday":
        return today - timedelta(days=1)
    match = _RELATIVE_RE.match(text)
    if match:
        return today - timedelta(days=int(match.group(1)))
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(
            "Не удалось разобрать дату. Нужен формат YYYY-MM-DD или today/yesterday/NdaysAgo.",
            details={"value": text},
        ) from exc


def _dimension_value(dimension: dict[str, Any]) -> Any:
    """Человеческое значение измерения строки (``name`` или ``id``)."""

    name = dimension.get("name")
    if name is not None:
        return name
    ident = dimension.get("id")
    if isinstance(ident, dict):
        return ident.get("name") or ident.get("id")
    return ident


def _filter_strings(
    filters: Sequence[Filter | str],
    *,
    directory: MetricDirectory,
) -> list[str]:
    """Рендерить DSL-фильтры в строки параметра ``filters``.

    :class:`~yandex_metrika_agent.models.ReportCommand` хранит ``filters`` как
    список строк, поэтому перевод из ``Filter`` делаем до передачи в команду.
    """

    rendered = render_filters(filters, directory=directory)
    return [rendered] if rendered else []


__all__ = [
    "GROUP_DAY",
    "TRAFFIC_METRICS",
    "ReportService",
]
