"""Переименование цели «Покупка…» в «Заказ» (counter 112724717).

Находит цель по имени (содержит «покупк»), меняет name на «Заказ», сохраняет
через API, проверяет. Реальная операция.

Запуск:  python scripts/rename_goal_by_name.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.config import load_settings
from yandex_metrika_agent.goals import GoalService

COUNTER_ID = 112724717
NEW_NAME = "Заказ"


async def main() -> int:
    settings = load_settings()
    async with MetrikaClient.from_settings(
        settings=settings, connection_id="task03"
    ) as client:
        goals = GoalService(client)

        listed = await goals.list(COUNTER_ID)
        target = None
        for goal in listed:
            if goal.name and "покупк" in goal.name.lower():
                target = goal
                break

        if target is None or target.id is None:
            print(
                json.dumps(
                    {
                        "renamed": False,
                        "reason": "цель с именем «Покупка…» не найдена",
                        "goals": [{"id": g.id, "name": g.name} for g in listed],
                    },
                    ensure_ascii=False,
                )
            )
            return 1

        before = target.name
        target.name = NEW_NAME
        updated = await goals.update(COUNTER_ID, target)

        print(
            json.dumps(
                {
                    "counter": COUNTER_ID,
                    "goal_id": updated.id,
                    "name_before": before,
                    "name_after": updated.name,
                    "renamed": updated.name == NEW_NAME,
                },
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
