"""Собрать ПОЛНЫЙ пакет данных по счётчику за весь срок жизни (с даты создания).

Формирует JSON-пакет для маркетингового отчёта уровня маркетолога:
- годовой тренд за всю жизнь счётчика (с даты создания);
- помесячная динамика за последние 26 месяцев;
- сводка и все маркетинговые срезы за ВЕСЬ период;
- цели: список + достижения/конверсия за весь период + динамика топ-цели;
- каналы: human-readable источники + домены источников + поисковые системы
  (разделение органики и Директа) + рефералы;
- входные страницы (топ и антитоп по отказам), хитовые страницы;
- география и устройства с конверсией топ-цели;
- сравнение: последние 12 мес vs предыдущие 12 мес.

Имена метрик/измерений — из официальной документации Reports API v1.
Асинхронные задачи отчётов дожидываются через transport.wait_async_report.

Запуск:
  python scripts/full_period_data.py --counter <ID> --site <domain.ru> \
      --date1 <YYYY-MM-DD> --out <site>_full_data.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.config import load_settings
from yandex_metrika_agent.goals import GoalService
from yandex_metrika_agent.metrics import goal_conversion, goal_reaches

# Метрики сводки (жизненный цикл + вовлечённость).
SUMMARY_METRICS = [
    "ym:s:visits",
    "ym:s:users",
    "ym:s:pageviews",
    "ym:s:hits",
    "ym:s:bounces",
    "ym:s:bounceRate",
    "ym:s:avgVisitDurationSeconds",
    "ym:s:pageDepth",
    "ym:s:newUsers",
]
# Метрики для годового/помесячного тренда.
TREND_METRICS = [
    "ym:s:visits",
    "ym:s:users",
    "ym:s:pageviews",
    "ym:s:bounceRate",
    "ym:s:avgVisitDurationSeconds",
    "ym:s:newUsers",
    "ym:s:pageDepth",
]


def parse_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Строки отчёта -> плоские словари (измерения + метрики по позициям)."""

    dims = payload.get("dimension_names") or []
    mets = payload.get("metric_names") or []
    rows: list[dict[str, Any]] = []
    for row in payload.get("data") or []:
        item: dict[str, Any] = {}
        for i, d in enumerate(row.get("dimensions") or []):
            name = d.get("name") if isinstance(d, dict) else d
            if name is None and isinstance(d, dict):
                ident = d.get("id")
                name = ident.get("name") if isinstance(ident, dict) else ident
            item[dims[i] if i < len(dims) else f"dim{i}"] = name
        for i, m in enumerate(row.get("metrics") or []):
            item[mets[i] if i < len(mets) else f"met{i}"] = m
        rows.append(item)
    return rows


class Fetcher:
    def __init__(self, client: MetrikaClient, counter: int) -> None:
        self.client = client
        self.counter = counter
        self.notes: list[str] = []

    async def data(
        self,
        label: str,
        metrics: list[str],
        dimensions: list[str] | None = None,
        *,
        d1: date,
        d2: date,
        sort: str | None = None,
        limit: int = 100,
        filters: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "id": self.counter,
            "date1": d1.isoformat(),
            "date2": d2.isoformat(),
            "metrics": ",".join(metrics),
            "limit": limit,
        }
        if dimensions:
            params["dimensions"] = ",".join(dimensions)
        if sort:
            params["sort"] = sort
        if filters:
            params["filters"] = filters
        try:
            payload = await self.client.get_json("/stat/v1/data", params=params)
            if "data" not in payload and payload.get("id") is not None:
                payload = await self.client.transport.wait_async_report(payload["id"])
            return {
                "totals": payload.get("totals"),
                "rows": parse_rows(payload),
                "sampled": payload.get("sampled"),
                "sample_share": payload.get("sample_share"),
            }
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"{label}: {type(exc).__name__}: {str(exc)[:160]}")
            return {"totals": None, "rows": [], "error": str(exc)[:200]}


async def main(counter_id: int, site: str, created: date, out_name: str) -> int:
    d2 = date.today()
    d1 = created
    # Актуальный период для детализации вовлечённости: последние 26 месяцев.
    recent_d1 = d2 - timedelta(days=26 * 30)
    settings = load_settings()
    async with MetrikaClient.from_settings(
        settings=settings, connection_id="new-account"
    ) as client:
        f = Fetcher(client, counter_id)
        goals_service = GoalService(client)

        # 1. Сводка за ВЕСЬ период.
        summary = await f.data("Сводка (весь период)", SUMMARY_METRICS, d1=d1, d2=d2)

        # 2. Годовой тренд за всю жизнь счётчика.
        years_rows: list[dict[str, Any]] = []
        for year in range(d1.year, d2.year + 1):
            y_start = max(d1, date(year, 1, 1))
            y_end = min(d2, date(year, 12, 31))
            if y_start > y_end:
                continue
            r = await f.data(
                f"Год {year}", TREND_METRICS, ["ym:s:year"], d1=y_start, d2=y_end, limit=2
            )
            row = {
                "year": year,
                "visits": r["totals"][0] if r["totals"] else None,
                "users": r["totals"][1] if r["totals"] and len(r["totals"]) > 1 else None,
                "pageviews": r["totals"][2] if r["totals"] and len(r["totals"]) > 2 else None,
                "bounce_rate": r["totals"][3] if r["totals"] and len(r["totals"]) > 3 else None,
                "avg_duration": r["totals"][4] if r["totals"] and len(r["totals"]) > 4 else None,
                "new_users": r["totals"][5] if r["totals"] and len(r["totals"]) > 5 else None,
                "page_depth": r["totals"][6] if r["totals"] and len(r["totals"]) > 6 else None,
            }
            years_rows.append(row)
        years = {"rows": years_rows}

        # 3. Помесячно за последние 26 месяцев.
        months = await f.data(
            "По месяцам (26 мес)", TREND_METRICS, ["ym:s:month"],
            d1=recent_d1, d2=d2, sort="ym:s:month", limit=26,
        )

        # 4. Цели: список + пакетные метрики за ВЕСЬ период.
        goals_out: list[dict[str, Any]] = []
        goal_ids: list[int] = []
        try:
            listed = await goals_service.list(counter_id)
            goals_out = [
                {"id": g.id, "name": g.name, "type": g.type} for g in listed if g.id
            ]
            goal_ids = [g["id"] for g in goals_out]
        except Exception as exc:  # noqa: BLE001
            f.notes.append(f"Список целей: {type(exc).__name__}: {str(exc)[:160]}")

        reaches: dict[str, Any] = {}
        conv: dict[str, Any] = {}
        if goal_ids:
            for start in range(0, len(goal_ids), 9):
                chunk = goal_ids[start : start + 9]
                r = await f.data(
                    f"Достижения целей {chunk}", [goal_reaches(i) for i in chunk], d1=d1, d2=d2
                )
                if r["totals"]:
                    for n, t in zip((goal_reaches(i) for i in chunk), r["totals"], strict=False):
                        reaches[n] = t
                c = await f.data(
                    f"Конверсия целей {chunk}", [goal_conversion(i) for i in chunk], d1=d1, d2=d2
                )
                if c["totals"]:
                    for n, t in zip((goal_conversion(i) for i in chunk), c["totals"], strict=False):
                        conv[n] = t

        per_goal = [
            {
                **g,
                "reaches": reaches.get(goal_reaches(g["id"])),
                "conversion_rate": conv.get(goal_conversion(g["id"])),
            }
            for g in goals_out
        ]
        per_goal.sort(key=lambda g: g["reaches"] or 0, reverse=True)
        top_goals = [g for g in per_goal if (g["reaches"] or 0) > 0][:8]
        top_id = top_goals[0]["id"] if top_goals else None

        # 5. Динамика топ-цели по месяцам (последние 26 мес).
        top_goal_months: dict[str, Any] = {"rows": []}
        if top_id:
            top_goal_months = await f.data(
                "Динамика топ-цели", [goal_reaches(top_id), goal_conversion(top_id)],
                ["ym:s:month"], d1=recent_d1, d2=d2, sort="ym:s:month", limit=26,
            )

        # 6. Каналы: human-readable источники + конверсия топ-цели (весь период).
        src_metrics = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:avgVisitDurationSeconds"]
        if top_id:
            src_metrics += [goal_reaches(top_id), goal_conversion(top_id)]
        sources = await f.data(
            "Источники (каналы)", src_metrics, ["ym:s:trafficSourceName"],
            d1=d1, d2=d2, sort="-ym:s:visits", limit=20,
        )

        # 6b. Домены источников (yandex / direct.yandex / google ...) — органика vs Директ.
        src_domain_metrics = ["ym:s:visits", "ym:s:bounceRate"]
        if top_id:
            src_domain_metrics += [goal_reaches(top_id), goal_conversion(top_id)]
        source_domains = await f.data(
            "Домены источников", src_domain_metrics, ["ym:s:trafficSource"],
            d1=d1, d2=d2, sort="-ym:s:visits", limit=20,
        )

        # 7. Поисковые системы (органика) с разделением на Яндекс/Google.
        search = await f.data(
            "Поисковые системы", ["ym:s:visits", "ym:s:bounceRate"],
            ["ym:s:searchEngineName"], d1=d1, d2=d2, sort="-ym:s:visits", limit=10,
        )

        # 7b. Поисковые фразы (органика) — интенты.
        search_terms = await f.data(
            "Поисковые фразы", ["ym:s:visits", "ym:s:bounceRate"],
            ["ym:s:searchPhrase"], d1=d1, d2=d2, sort="-ym:s:visits", limit=25,
        )

        # 8. Целевые срезы: ОРГАНИКА и ДИРЕКТ (ad) с конверсией топ-цели.
        seg_metrics = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:avgVisitDurationSeconds"]
        if top_id:
            seg_metrics += [goal_reaches(top_id), goal_conversion(top_id)]
        organic_seg = await f.data(
            "Срез: органика", seg_metrics, d1=d1, d2=d2,
            filters="ym:s:trafficSource=='organic'",
        )
        ad_seg = await f.data(
            "Срез: директ (ad)", seg_metrics, d1=d1, d2=d2,
            filters="ym:s:trafficSource=='ad'",
        )
        # Органика по системам (Яндекс/Google) и Директ по посадочным.
        organic_by_engine = await f.data(
            "Органика по системам", ["ym:s:visits", "ym:s:bounceRate"],
            ["ym:s:searchEngineName"], d1=d1, d2=d2, sort="-ym:s:visits", limit=8,
            filters="ym:s:trafficSource=='organic'",
        )
        ad_landing_metrics = ["ym:s:visits", "ym:s:bounceRate"]
        if top_id:
            ad_landing_metrics += [goal_reaches(top_id), goal_conversion(top_id)]
        ad_landing = await f.data(
            "Директ: посадочные", ad_landing_metrics, ["ym:s:startURLPath"],
            d1=d1, d2=d2, sort="-ym:s:visits", limit=15,
            filters="ym:s:trafficSource=='ad'",
        )

        # 9. Рефералы.
        referal = await f.data(
            "Рефералы", ["ym:s:visits", "ym:s:bounceRate"],
            ["ym:s:referalSource"], d1=d1, d2=d2, sort="-ym:s:visits", limit=15,
        )

        # 10. Соцсети (если существенно).
        social = await f.data(
            "Соцсети", ["ym:s:visits", "ym:s:bounceRate"],
            ["ym:s:socialNetwork"], d1=d1, d2=d2, sort="-ym:s:visits", limit=10,
        )

        # 11. Города + конверсия топ-цели.
        city_metrics = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate"]
        if top_id:
            city_metrics += [goal_reaches(top_id), goal_conversion(top_id)]
        cities = await f.data(
            "Города", city_metrics, ["ym:s:regionCity"], d1=d1, d2=d2, sort="-ym:s:visits", limit=15,
        )

        # 12. Устройства + конверсия топ-цели.
        dev_metrics = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate"]
        if top_id:
            dev_metrics += [goal_reaches(top_id), goal_conversion(top_id)]
        devices = await f.data(
            "Устройства", dev_metrics, ["ym:s:deviceCategory"], d1=d1, d2=d2, sort="-ym:s:visits", limit=6,
        )

        # 13. Входные страницы: топ по трафику и антитоп по отказам.
        sp_metrics = ["ym:s:visits", "ym:s:bounceRate", "ym:s:avgVisitDurationSeconds"]
        if top_id:
            sp_metrics += [goal_reaches(top_id), goal_conversion(top_id)]
        start_top = await f.data(
            "Входные страницы топ", sp_metrics, ["ym:s:startURLPath"],
            d1=d1, d2=d2, sort="-ym:s:visits", limit=20,
        )
        start_bounce = await f.data(
            "Входные страницы по отказам", ["ym:s:visits", "ym:s:bounceRate"],
            ["ym:s:startURLPath"], d1=d1, d2=d2, sort="-ym:s:bounceRate", limit=15,
            filters="ym:s:visits>=200",
        )

        # 14. Хитовые страницы (ym:pv:URL) — детализация за последние 12 мес
        # (за 12 лет запрос слишком тяжёлый и уходит в таймаут).
        pages = await f.data(
            "Страницы (хиты)", ["ym:pv:pageviews"], ["ym:pv:URL"],
            d1=d2 - timedelta(days=365), d2=d2, sort="-ym:pv:pageviews", limit=20,
        )

        # 15. Сравнение: последние 12 мес vs предыдущие 12 мес.
        now12_d1 = d2 - timedelta(days=365)
        prev12_d2 = d2 - timedelta(days=365)
        prev12_d1 = d2 - timedelta(days=730)
        base_period = {"id": counter_id, "metrics": ",".join(
            ["ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:newUsers",
             "ym:s:avgVisitDurationSeconds", "ym:s:pageDepth"]
        )}
        period_now = await f.data(
            "Период: последние 12 мес",
            ["ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:newUsers",
             "ym:s:avgVisitDurationSeconds", "ym:s:pageDepth"],
            d1=now12_d1, d2=d2,
        )
        period_prev = await f.data(
            "Период: предыдущие 12 мес",
            ["ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:newUsers",
             "ym:s:avgVisitDurationSeconds", "ym:s:pageDepth"],
            d1=prev12_d1, d2=prev12_d2,
        )

    out = {
        "site": site,
        "counter_id": counter_id,
        "created": created.isoformat(),
        "period": {"date1": d1.isoformat(), "date2": d2.isoformat()},
        "summary": summary,
        "years": years,
        "months": months,
        "goals": per_goal,
        "top_goals": top_goals,
        "top_goal_id": top_id,
        "top_goal_months": top_goal_months,
        "sources": sources,
        "source_domains": source_domains,
        "search": search,
        "search_terms": search_terms,
        "organic_seg": organic_seg,
        "ad_seg": ad_seg,
        "organic_by_engine": organic_by_engine,
        "ad_landing": ad_landing,
        "referal": referal,
        "social": social,
        "cities": cities,
        "devices": devices,
        "start_top": start_top,
        "start_bounce": start_bounce,
        "pages": pages,
        "period_now": period_now,
        "period_prev": period_prev,
        "notes": f.notes,
    }
    target = Path("reports") / out_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Данные сохранены: {target}")
    print(f"Целей: {len(per_goal)}, топ-целей с достижениями: {len(top_goals)}")
    print(f"Визиты за всё время: {summary['totals'][0] if summary['totals'] else '?'}")
    if f.notes:
        print("ПРИМЕЧАНИЯ:")
        for n in f.notes:
            print(" -", n)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--counter", type=int, required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--date1", required=True, help="Дата создания счётчика YYYY-MM-DD")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    created = date.fromisoformat(args.date1)
    sys.exit(asyncio.run(main(args.counter, args.site, created, args.out)))
