"""Лучшая цель по конверсии за последний месяц (counter 112724717).

Запрашивает РЕАЛЬНЫЕ данные Reports API по каждой цели, считает
конверсию и выбирает максимум. Ничего не выдумывает: если трафика нет,
в выводе это честно отражено.

Запуск:  python scripts/best_goal_by_conversion.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.config import load_settings
from yandex_metrika_agent.goals import GoalService
from yandex_metrika_agent.reports import ReportService

COUNTER_ID = 112724717


def last_month() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=30)).isoformat(), today.isoformat()


async def main() -> int:
    date_from, date_to = last_month()
    settings = load_settings()
    async with MetrikaClient.from_settings(
        settings=settings, connection_id="task03"
    ) as client:
        goals = GoalService(client)
        reports = ReportService(client)

        listed = await goals.list(COUNTER_ID)
        rows: list[dict] = []
        for goal in listed:
            if goal.id is None:
                continue
            stats = await reports.get_goal_stats(
                COUNTER_ID, goal.id, date_from=date_from, date_to=date_to
            )
            reaches = stats.get("goal_reaches") or 0
            conversion = stats.get("goal_conversion_rate")
            rows.append(
                {
                    "goal_id": goal.id,
                    "name": goal.name,
                    "type": goal.type,
                    "reaches": reaches,
                    "conversion_rate": conversion,
                }
            )

        # Сортировка: по конверсии (убыв.), затем по достижениям.
        def sort_key(row: dict):
            conv = row["conversion_rate"]
            return (
                conv if isinstance(conv, (int, float)) else -1,
                row["reaches"] if isinstance(row["reaches"], (int, float)) else -1,
            )

        ranked = sorted(rows, key=sort_key, reverse=True)
        total_reaches = sum(r["reaches"] for r in rows if isinstance(r["reaches"], int))
        best = ranked[0] if ranked else None
        has_data = total_reaches > 0

        print(
            json.dumps(
                {
                    "counter": COUNTER_ID,
                    "period": {"from": date_from, "to": date_to},
                    "total_goal_reaches": total_reaches,
                    "has_traffic_data": has_data,
                    "best_goal": best if has_data else None,
                    "ranking": ranked,
                },
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
