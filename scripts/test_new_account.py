"""Быстрый тест новой учётки: list_counters → get → goals → reports.

connection_id = "new-account" (OAuth device-flow, user_login из output).

Запуск:  python scripts/test_new_account.py
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
from yandex_metrika_agent.counters import CounterService
from yandex_metrika_agent.goals import GoalService
from yandex_metrika_agent.reports import ReportService

CONNECTION_ID = "new-account"


async def main() -> int:
    settings = load_settings()
    async with MetrikaClient.from_settings(
        settings=settings, connection_id=CONNECTION_ID
    ) as client:
        counters = CounterService(client)
        goals = GoalService(client)
        reports = ReportService(client)

        print("=== Шаг 1: list_counters ===")
        all_counters = await counters.list()
        print(json.dumps({"count": len(all_counters)}, ensure_ascii=False))
        if not all_counters:
            print("Нет счётчиков в новой учётке — тест на этом завершён (нет данных).")
            return 0

        for counter in all_counters[:5]:
            print(
                json.dumps(
                    {
                        "id": counter.id,
                        "name": counter.name,
                        "site": counter.site,
                        "status": counter.status,
                    },
                    ensure_ascii=False,
                )
            )

        counter = all_counters[0]
        print(f"\n=== Шаг 2: цели счётчика {counter.id} ===")
        listed_goals = await goals.list(counter.id)
        print(json.dumps({"goal_count": len(listed_goals)}, ensure_ascii=False))
        for goal in listed_goals[:5]:
            print(
                json.dumps(
                    {"id": goal.id, "name": goal.name, "type": goal.type},
                    ensure_ascii=False,
                )
            )

        print(f"\n=== Шаг 3: трафик за 30 дней (счётчик {counter.id}) ===")
        date_to = date.today()
        date_from = date_to - timedelta(days=30)
        traffic = await reports.get_traffic(
            counter.id, date_from=date_from, date_to=date_to
        )
        print(json.dumps(traffic, ensure_ascii=False))

        if listed_goals:
            goal = listed_goals[0]
            print(f"\n=== Шаг 4: статистика цели {goal.id} ===")
            stats = await reports.get_goal_stats(
                counter.id, goal.id, date_from=date_from, date_to=date_to
            )
            print(json.dumps(stats, ensure_ascii=False))

        print("\n=== Тест завершён успешно ===")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
