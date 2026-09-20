"""Собрать максимум статистики по счётчику Метрики за последние N месяцев.

Вытаскивает через ReportService/CounterService/GoalService реальные данные
Reports API (connection_id="new-account") и формирует markdown-отчёт в файл.

Каждый блок собирается изолированно: сбой одного отчёта не рвёт сбор — ошибка
попадает в раздел «Примечания сбора».

Запуск:  python scripts/monthly_report.py --site <domain.ru> [--months 4]
                                          [--connection new-account]
                                          [--out reports/<site>_report.md]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.config import load_settings
from yandex_metrika_agent.counters import CounterService
from yandex_metrika_agent.goals import GoalService
from yandex_metrika_agent.models import ReportCommand
from yandex_metrika_agent.reports import ReportService


# --- Форматирование ----------------------------------------------------------


def fmt_int(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.0f}".replace(",", " ")
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def fmt_num(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def fmt_pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)


def fmt_duration(value: Any) -> str:
    """Секунды -> «М:СС» (для среднего времени на сайте)."""

    if value is None:
        return "—"
    try:
        total = int(float(value))
    except (TypeError, ValueError):
        return str(value)
    minutes, seconds = divmod(total, 60)
    return f"{minutes}:{seconds:02d}"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_нет данных_"
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
    return "\n".join(lines)


def month_shift(base: date, months: int) -> date:
    """Сдвинуть дату на N месяцев назад (день = min(день, длина месяца))."""

    year = base.year
    month = base.month - months
    while month <= 0:
        month += 12
        year -= 1
    # Длина целевого месяца.
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    last_day = (next_first.toordinal() - 1)
    last_day_num = (date.fromordinal(last_day)).day
    return date(year, month, min(base.day, last_day_num))


# --- Сбор отчётов ------------------------------------------------------------


class Collector:
    def __init__(self, reports: ReportService, counter_id: int, d1: date, d2: date) -> None:
        self.reports = reports
        self.counter_id = counter_id
        self.d1 = d1
        self.d2 = d2
        self.notes: list[str] = []

    async def report(self, label: str, command: dict[str, Any], *, validate: bool = True) -> list[dict]:
        try:
            result = await self.reports.get_report(
                ReportCommand(counter_id=self.counter_id, date1=self.d1, date2=self.d2, **command),
                validate=validate,
            )
            return self.reports.rows_as_dicts(result)
        except Exception as exc:  # noqa: BLE001 - сбор устойчивый, ошибка в примечания
            self.notes.append(f"{label}: {type(exc).__name__}: {exc}")
            return []

    async def report_totals(self, label: str, metrics: list[str]) -> dict[str, Any]:
        """Итоги плоского отчёта (без измерений): человеческое имя -> значение."""

        try:
            result = await self.reports.get_report(
                ReportCommand(counter_id=self.counter_id, date1=self.d1, date2=self.d2, metrics=metrics)
            )
            out: dict[str, Any] = {}
            for index, api in enumerate(result.metric_names):
                if index < len(result.totals):
                    out[self.reports.directory.humanize(api)] = result.totals[index]
            return out
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"{label}: {type(exc).__name__}: {exc}")
            return {}

    async def grouped(
        self,
        label: str,
        dimension: str,
        metrics: list[str],
        *,
        sort: str | None = "-visits",
        limit: int = 30,
    ) -> list[dict]:
        command: dict[str, Any] = {
            "metrics": metrics,
            "dimensions": [dimension],
            "limit": limit,
        }
        if sort:
            command["sort_by"] = [sort]
        return await self.report(label, command)


def _sum(rows: list[dict], key: str) -> float:
    total = 0.0
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)):
            total += float(value)
    return total


# --- Рендер отчёта -----------------------------------------------------------


async def build_report(site: str, months: int, connection_id: str, out_path: Path) -> int:
    settings = load_settings()
    async with MetrikaClient.from_settings(settings=settings, connection_id=connection_id) as client:
        counters = CounterService(client)
        goals = GoalService(client)
        reports = ReportService(client)

        # Резолв счётчика.
        try:
            counter = await counters.resolve_one(site)
        except Exception:  # noqa: BLE001
            counter = await counters.get(int(site))

        d2 = date.today()
        d1 = month_shift(d2, months)

        col = Collector(reports, counter.id, d1, d2)

        # Сводка.
        summary = await reports.get_traffic(counter.id, date_from=d1, date_to=d2)
        extra_totals = await col.report_totals("Сводка (доп.)", ["hits", "bounces", "depth"])

        # Помесячная динамика.
        by_month = await col.grouped(
            "По месяцам",
            "month",
            ["visits", "users", "pageviews", "bounce_rate", "session_duration", "new_users", "depth"],
            sort="month",
            limit=12,
        )
        # Дневная динамика.
        by_day = await col.grouped(
            "По дням", "date", ["visits", "users"], sort="date", limit=200
        )
        # Почасовая активность.
        by_hour = await col.grouped("По часам", "hour", ["visits"], sort="hour", limit=24)

        # Срезы аудитории.
        sources = await col.grouped("Источники", "traffic_source", ["visits", "users"], limit=15)
        search_engines = await col.grouped("Поисковые системы", "search_engine", ["visits"], limit=10)
        devices = await col.grouped("Устройства", "device", ["visits", "users"], limit=10)
        operating = await col.grouped("ОС", "os", ["visits"], limit=10)
        browsers = await col.grouped("Браузеры", "browser", ["visits"], limit=12)
        countries = await col.grouped("Страны", "country", ["visits"], limit=10)
        cities = await col.grouped("Города", "city", ["visits"], limit=15)
        languages = await col.grouped("Язык", "language", ["visits"], limit=10)

        # Хитовые срезы (ym:pv:).
        top_pages = await col.grouped("Топ страниц", "page", ["pv_pageviews"], sort="-pv_pageviews", limit=20)
        referers = await col.grouped("Рефереры", "referer", ["pv_pageviews"], sort="-pv_pageviews", limit=15)

        # Цели.
        goal_rows: list[dict] = []
        goal_stats: list[dict] = []
        try:
            listed = await goals.list(counter.id)
            for goal in listed:
                goal_rows.append({"id": goal.id, "name": goal.name, "type": goal.type})
                if goal.id is None:
                    continue
                try:
                    stats = await reports.get_goal_stats(counter.id, goal.id, date_from=d1, date_to=d2)
                    goal_stats.append(
                        {
                            "id": goal.id,
                            "name": goal.name,
                            "reaches": stats.get("goal_reaches"),
                            "conversion": stats.get("goal_conversion_rate"),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    col.notes.append(f"Статистика цели {goal.id}: {type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            col.notes.append(f"Список целей: {type(exc).__name__}: {exc}")

        # Сравнение: первые 2 месяца vs последние 2 месяца.
        mid = month_shift(d2, months // 2)
        period_b = (mid, d2)
        period_a = (d1, mid)
        comparison: list[Any] = []
        try:
            comparison = await reports.compare_periods(
                counter.id,
                period_a=period_a,
                period_b=period_b,
                metrics=["visits", "users", "pageviews", "new_users", "bounce_rate"],
            )
        except Exception as exc:  # noqa: BLE001
            col.notes.append(f"Сравнение периодов: {type(exc).__name__}: {exc}")

    # --- Рендер markdown ---
    lines: list[str] = []
    A = lines.append

    A(f"# Отчёт по сайту {counter.site or site}")
    A("")
    A(f"*Сформирован:* {d2.isoformat()} · *Период:* {d1.isoformat()} — {d2.isoformat()} "
      f"({months} мес.) · *Данные:* Яндекс Метрика, Reports API (реальный аккаунт)")
    A("")
    A("## 1. О счётчике")
    A("")
    A(md_table(
        ["Параметр", "Значение"],
        [
            ["ID счётчика", str(counter.id)],
            ["Название", counter.name or "—"],
            ["Сайт", counter.site or "—"],
            ["Домен", counter.domain or "—"],
            ["Статус", counter.status or "—"],
            ["Часовой пояс", counter.timezone or "—"],
            ["Владелец", counter.owner_login or "—"],
            ["Права", counter.permission or "—"],
            ["Создан", counter.created_at or "—"],
        ],
    ))
    A("")

    A("## 2. Сводка за период")
    A("")
    A(md_table(
        ["Показатель", "Значение"],
        [
            ["Визиты", fmt_int(summary.get("visits"))],
            ["Посетители", fmt_int(summary.get("users"))],
            ["Просмотры страниц", fmt_int(summary.get("pageviews"))],
            ["Хиты", fmt_int(extra_totals.get("hits"))],
            ["Отказы (шт.)", fmt_int(extra_totals.get("bounces"))],
            ["Доля отказов", fmt_pct(summary.get("bounce_rate"))],
            ["Среднее время на сайте", fmt_duration(summary.get("session_duration"))],
            ["Глубина просмотра", fmt_num(extra_totals.get("depth"), 2)],
            ["Новые посетители", fmt_int(summary.get("new_users"))],
        ],
    ))
    A("")

    A("## 3. Динамика по месяцам")
    A("")
    A(md_table(
        ["Месяц", "Визиты", "Посетители", "Просмотры", "Отказы %", "Время", "Новые", "Глубина"],
        [
            [
                str(row.get("month", "—")),
                fmt_int(row.get("visits")),
                fmt_int(row.get("users")),
                fmt_int(row.get("pageviews")),
                fmt_pct(row.get("bounce_rate")),
                fmt_duration(row.get("session_duration")),
                fmt_int(row.get("new_users")),
                fmt_num(row.get("depth"), 2),
            ]
            for row in by_month
        ],
    ))
    A("")
    if by_day:
        peak_day = max(by_day, key=lambda r: r.get("visits") or 0)
        avg_visits = _sum(by_day, "visits") / len(by_day) if by_day else 0
        A(f"- Пик по визитам: **{fmt_int(peak_day.get('visits'))}** ({peak_day.get('date')})")
        A(f"- Среднее визитов в день: **{fmt_int(round(avg_visits))}** (дней в периоде: {len(by_day)})")
        A("")

    A("## 4. Активность по часам")
    A("")
    A(md_table(
        ["Час", "Визиты"],
        [[str(row.get("hour", "—")), fmt_int(row.get("visits"))] for row in by_hour],
    ))
    A("")

    A("## 5. Источники трафика")
    A("")
    A(md_table(
        ["Источник", "Визиты", "Посетители"],
        [[r.get("traffic_source", "—"), fmt_int(r.get("visits")), fmt_int(r.get("users"))] for r in sources],
    ))
    A("")

    A("## 6. Поисковые системы")
    A("")
    A(md_table(
        ["Поисковая система", "Визиты"],
        [[r.get("search_engine", "—"), fmt_int(r.get("visits"))] for r in search_engines],
    ))
    A("")

    A("## 7. Устройства и окружение")
    A("")
    A("**Тип устройства**")
    A("")
    A(md_table(["Тип", "Визиты", "Посетители"],
               [[r.get("device_type", "—"), fmt_int(r.get("visits")), fmt_int(r.get("users"))] for r in devices]))
    A("")
    A("**Операционные системы**")
    A("")
    A(md_table(["ОС", "Визиты"], [[r.get("operating_system", "—"), fmt_int(r.get("visits"))] for r in operating]))
    A("")
    A("**Браузеры**")
    A("")
    A(md_table(["Браузер", "Визиты"], [[r.get("browser", "—"), fmt_int(r.get("visits"))] for r in browsers]))
    A("")

    A("## 8. География")
    A("")
    A("**Страны**")
    A("")
    A(md_table(["Страна", "Визиты"], [[r.get("country", "—"), fmt_int(r.get("visits"))] for r in countries]))
    A("")
    A("**Города**")
    A("")
    A(md_table(["Город", "Визиты"], [[r.get("city", "—"), fmt_int(r.get("visits"))] for r in cities]))
    A("")
    A("**Язык**")
    A("")
    A(md_table(["Язык", "Визиты"], [[r.get("language", "—"), fmt_int(r.get("visits"))] for r in languages]))
    A("")

    A("## 9. Популярные страницы")
    A("")
    A(md_table(["Страница", "Просмотры"],
               [[r.get("page", "—"), fmt_int(r.get("pv_pageviews"))] for r in top_pages]))
    A("")

    A("## 10. Рефереры (внешние ссылки)")
    A("")
    A(md_table(["Реферер", "Просмотры"],
               [[r.get("referer", "—"), fmt_int(r.get("pv_pageviews"))] for r in referers]))
    A("")

    A("## 11. Цели и конверсия")
    A("")
    if goal_stats:
        ranked = sorted(goal_stats, key=lambda g: (g.get("reaches") or 0), reverse=True)
        A(md_table(
            ["Цель", "Тип", "Достижения", "Конверсия"],
            [
                [g.get("name") or "—",
                 next((gr["type"] for gr in goal_rows if gr["id"] == g["id"]), "—"),
                 fmt_int(g.get("reaches")),
                 fmt_pct(g.get("conversion"))]
                for g in ranked
            ],
        ))
        A("")
        best = max(goal_stats, key=lambda g: (g.get("conversion") or 0))
        A(f"- Лучшая по конверсии: **{best.get('name')}** — {fmt_pct(best.get('conversion'))} "
          f"({fmt_int(best.get('reaches'))} достижений)")
    else:
        A("_цели не найдены или статистика недоступна_")
    A("")

    A("## 12. Сравнение периодов")
    A("")
    A(f"*Период A:* {period_a[0].isoformat()} — {period_a[1].isoformat()} · "
      f"*Период B:* {period_b[0].isoformat()} — {period_b[1].isoformat()}")
    A("")
    if comparison:
        A(md_table(
            ["Показатель", "Период A", "Период B", "Δ", "Δ %"],
            [
                [row.label, fmt_num(row.previous, 2), fmt_num(row.current, 2),
                 fmt_num(row.delta, 2),
                 (f"{row.delta_percent:+.1f}%" if row.delta_percent is not None else "—")]
                for row in comparison
            ],
        ))
    else:
        A("_недостаточно данных для сравнения_")
    A("")

    if col.notes:
        A("## Примечания сбора")
        A("")
        for note in col.notes:
            A(f"- {note}")
        A("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Отчёт сохранён: {out_path} ({len(lines)} строк)")
    if col.notes:
        print(f"Примечаний сбора: {len(col.notes)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True, help="Домен сайта (счётчик)")
    parser.add_argument("--months", type=int, default=4)
    parser.add_argument("--connection", default="new-account")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    stamp = date.today().isoformat()
    out = Path(args.out) if args.out else Path("reports") / f"{args.site}_report_{stamp}.md"
    return asyncio.run(build_report(args.site, args.months, args.connection, out))


if __name__ == "__main__":
    sys.exit(main())
