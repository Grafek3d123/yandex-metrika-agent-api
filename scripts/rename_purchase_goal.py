"""Переименование цели «Покупка» в «Заказ» (counter 112724717, goal 627172301).

Реальная операция: читает цель, меняет name, сохраняет через API, проверяет.

Запуск:  python scripts/rename_purchase_goal.py
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
GOAL_ID = 627172301
NEW_NAME = "Заказ"


async def main() -> int:
    settings = load_settings()
    async with MetrikaClient.from_settings(
        settings=settings, connection_id="task03"
    ) as client:
        goals = GoalService(client)

        goal = await goals.get(COUNTER_ID, GOAL_ID)
        before = goal.name
        goal.name = NEW_NAME
        updated = await goals.update(COUNTER_ID, goal)
        after = updated.name

        print(
            json.dumps(
                {
                    "counter": COUNTER_ID,
                    "goal_id": GOAL_ID,
                    "name_before": before,
                    "name_after": after,
                    "renamed": after == NEW_NAME,
                },
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
