"""Собрать пакет данных по счётчику за 365 дней (для маркетингового отчёта).

Прямые запросы Reports API (имена метрик/измерений — из официальной документации
Reports API v1, проверены на живом счётчике). Асинхронные задачи отчётов
дожидаются через transport.wait_async_report. Результат — JSON-пакет для отчёта.

Запуск:  python scripts/collect_annual.py --counter <ID> --site <domain.ru> \
                                              --out <site>_annual_data.json
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

DAYS = 365


def parse_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Строки отчёта -> словари (измерения по name/id + метрики по позициям)."""

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
    def __init__(self, client: MetrikaClient, counter: int, d1: date, d2: date) -> None:
        self.client = client
        self.counter = counter
        self.d1 = d1
        self.d2 = d2
        self.notes: list[str] = []

    async def data(
        self,
        label: str,
        metrics: list[str],
        dimensions: list[str] | None = None,
        *,
        sort: str | None = None,
        limit: int = 100,
        filters: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "id": self.counter,
            "date1": self.d1.isoformat(),
            "date2": self.d2.isoformat(),
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
            # Асинхронная задача: {"id": N, "status": ...} без data.
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


async def main(counter_id: int, site: str, out_name: str) -> int:
    d2 = date.today()
    d1 = d2 - timedelta(days=DAYS - 1)
    settings = load_settings()
    async with MetrikaClient.from_settings(
        settings=settings, connection_id="new-account"
    ) as client:
        f = Fetcher(client, counter_id, d1, d2)
        goals_service = GoalService(client)

        # 1. Сводка.
        summary = await f.data(
            "Сводка",
            [
                "ym:s:visits",
                "ym:s:users",
                "ym:s:pageviews",
                "ym:s:hits",
                "ym:s:bounces",
                "ym:s:bounceRate",
                "ym:s:avgVisitDurationSeconds",
                "ym:s:pageDepth",
                "ym:s:newUsers",
            ],
        )

        # 2. Помесячно.
        months = await f.data(
            "По месяцам",
            [
                "ym:s:visits",
                "ym:s:users",
                "ym:s:pageviews",
                "ym:s:bounceRate",
                "ym:s:avgVisitDurationSeconds",
                "ym:s:newUsers",
                "ym:s:pageDepth",
            ],
            ["ym:s:month"],
            sort="ym:s:month",
            limit=13,
        )

        # 3. По дням (для пиков/провалов).
        days = await f.data(
            "По дням",
            ["ym:s:visits", "ym:s:users", "ym:s:bounceRate"],
            ["ym:s:date"],
            sort="ym:s:date",
            limit=400,
        )

        # 4. Цели: список + пакетные метрики.
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
            for chunk_start in range(0, len(goal_ids), 9):
                chunk = goal_ids[chunk_start : chunk_start + 9]
                r = await f.data(
                    f"Достижения целей {chunk}",
                    [goal_reaches(i) for i in chunk],
                )
                if r["totals"]:
                    for n, t in zip((goal_reaches(i) for i in chunk), r["totals"], strict=False):
                        reaches[n] = t
                c = await f.data(
                    f"Конверсия целей {chunk}",
                    [goal_conversion(i) for i in chunk],
                )
                if c["totals"]:
                    for n, t in zip((goal_conversion(i) for i in chunk), c["totals"], strict=False):
                        conv[n] = t

        # Топ-5 целей по достижениям.
        per_goal = []
        for g in goals_out:
            per_goal.append(
                {
                    **g,
                    "reaches": reaches.get(goal_reaches(g["id"])),
                    "conversion_rate": conv.get(goal_conversion(g["id"])),
                }
            )
        per_goal.sort(key=lambda g: g["reaches"] or 0, reverse=True)
        top_goals = [g for g in per_goal if (g["reaches"] or 0) > 0][:5]
        top_id = top_goals[0]["id"] if top_goals else None

        # 5. Динамика топ-цели по месяцам.
        top_goal_months: dict[str, Any] = {"rows": []}
        if top_id:
            top_goal_months = await f.data(
                "Динамика топ-цели",
                [goal_reaches(top_id), goal_conversion(top_id)],
                ["ym:s:month"],
                sort="ym:s:month",
                limit=13,
            )

        # 6. Источники (ТЗ: trafficSourceName) + конверсия топ-цели по каналам.
        src_metrics = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:avgVisitDurationSeconds"]
        if top_id:
            src_metrics += [goal_reaches(top_id), goal_conversion(top_id)]
        sources = await f.data(
            "Источники", src_metrics, ["ym:s:trafficSourceName"], sort="-ym:s:visits", limit=15
        )

        # 7. Поисковые системы.
        search = await f.data(
            "Поисковые системы",
            ["ym:s:visits", "ym:s:bounceRate"],
            ["ym:s:searchEngineName"],
            sort="-ym:s:visits",
            limit=10,
        )

        # 8. Рефералы (ТЗ: referalSource).
        referal = await f.data(
            "Рефералы",
            ["ym:s:visits", "ym:s:bounceRate"],
            ["ym:s:referalSource"],
            sort="-ym:s:visits",
            limit=15,
        )

        # 9. Города + конверсия топ-цели.
        city_metrics = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate"]
        if top_id:
            city_metrics += [goal_reaches(top_id), goal_conversion(top_id)]
        cities = await f.data(
            "Города", city_metrics, ["ym:s:regionCity"], sort="-ym:s:visits", limit=15
        )

        # 10. Устройства (ТЗ: deviceCategory) + конверсия топ-цели.
        dev_metrics = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate"]
        if top_id:
            dev_metrics += [goal_reaches(top_id), goal_conversion(top_id)]
        devices = await f.data(
            "Устройства", dev_metrics, ["ym:s:deviceCategory"], sort="-ym:s:visits", limit=6
        )

        # 11. Входные страницы (ТЗ: startURLPath): топ по трафику и топ по отказам.
        sp_metrics = ["ym:s:visits", "ym:s:bounceRate", "ym:s:avgVisitDurationSeconds"]
        if top_id:
            sp_metrics += [goal_conversion(top_id)]
        start_top = await f.data(
            "Входные страницы топ",
            sp_metrics,
            ["ym:s:startURLPath"],
            sort="-ym:s:visits",
            limit=12,
        )
        start_bounce = await f.data(
            "Входные страницы по отказам",
            ["ym:s:visits", "ym:s:bounceRate"],
            ["ym:s:startURLPath"],
            sort="-ym:s:bounceRate",
            limit=12,
            filters="ym:s:visits>=100",
        )

        # 12. Хитовые страницы (ym:pv:URL) — детализация.
        pages = await f.data(
            "Страницы (хиты)",
            ["ym:pv:pageviews"],
            ["ym:pv:URL"],
            sort="-ym:pv:pageviews",
            limit=12,
        )

        # 13. Сравнение периодов: последние полгода vs предыдущие полгода.
        mid = d2 - timedelta(days=182)
        base_period = {
            "id": counter_id,
            "metrics": "ym:s:visits,ym:s:users,ym:s:bounceRate,ym:s:newUsers",
        }
        period_a: dict[str, Any] = {"totals": None}
        period_b: dict[str, Any] = {"totals": None}
        try:
            pa = await client.get_json(
                "/stat/v1/data",
                params={**base_period, "date1": d1.isoformat(), "date2": mid.isoformat()},
            )
            if "data" not in pa and pa.get("id") is not None:
                pa = await client.transport.wait_async_report(pa["id"])
            period_a = {"totals": pa.get("totals")}
        except Exception as exc:  # noqa: BLE001
            f.notes.append(f"Период A: {type(exc).__name__}: {str(exc)[:160]}")
        try:
            pb = await client.get_json(
                "/stat/v1/data",
                params={**base_period, "date1": mid.isoformat(), "date2": d2.isoformat()},
            )
            if "data" not in pb and pb.get("id") is not None:
                pb = await client.transport.wait_async_report(pb["id"])
            period_b = {"totals": pb.get("totals")}
        except Exception as exc:  # noqa: BLE001
            f.notes.append(f"Период B: {type(exc).__name__}: {str(exc)[:160]}")

    out = {
        "site": site,
        "counter_id": counter_id,
        "period": {"date1": d1.isoformat(), "date2": d2.isoformat(), "days": DAYS},
        "summary": summary,
        "months": months,
        "days": days,
        "goals": per_goal,
        "top_goals": top_goals,
        "top_goal_months": top_goal_months,
        "sources": sources,
        "search": search,
        "referal": referal,
        "cities": cities,
        "devices": devices,
        "start_top": start_top,
        "start_bounce": start_bounce,
        "pages": pages,
        "period_a": period_a,
        "period_b": period_b,
        "notes": f.notes,
    }
    target = Path("reports") / out_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Данные сохранены: {target}")
    print(f"Целей: {len(per_goal)}, топ-целей с достижениями: {len(top_goals)}")
    if f.notes:
        print("ПРИМЕЧАНИЯ:")
        for n in f.notes:
            print(" -", n)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--counter", type=int, required=True, help="ID счётчика Метрики")
    parser.add_argument("--site", required=True, help="Домен сайта")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    out_name = args.out or f"{args.site.split('.')[0]}_365d_data.json"
    sys.exit(asyncio.run(main(args.counter, args.site, out_name)))
