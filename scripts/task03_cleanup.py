"""Task03 cleanup: удалить тестовую цель и все тестовые счётчики.

Удаляет цель 621158581 (счётчик 112675662) и все счётчики, имя которых
начинается с ``Task03 acceptance``. Реальные (не тестовые) счётчики не
трогает — фильтр строго по префиксу имени.

Запуск:
    python scripts/task03_cleanup.py
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    if isinstance(_stream, io.TextIOWrapper):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.config import load_settings
from yandex_metrika_agent.counters import CounterService
from yandex_metrika_agent.errors import AgentError, NotFoundError
from yandex_metrika_agent.goals import GoalService

GOAL_COUNTER_ID = 112675662
GOAL_ID = 621158581
TEST_PREFIX = "Task03 acceptance"


async def main() -> None:
    cfg = load_settings()
    async with MetrikaClient.from_settings(settings=cfg, connection_id="task03") as client:
        goals = GoalService(client)
        counters = CounterService(client)

        # 1. Тестовая цель.
        try:
            result = await goals.delete(GOAL_COUNTER_ID, GOAL_ID)
            print(f"[goal] удалена цель {GOAL_ID} (счётчик {GOAL_COUNTER_ID}): {result}")
        except NotFoundError:
            print(f"[goal] цель {GOAL_ID} уже отсутствует")
        except AgentError as exc:
            print(f"[goal] ошибка удаления цели {GOAL_ID}: {exc.message}")

        # 2. Тестовые счётчики (строгий фильтр по префиксу имени).
        all_counters = await counters.list()
        print(f"[counters] всего в аккаунте: {len(all_counters)}")
        for ctr in all_counters:
            mark = "ТЕСТ" if (ctr.name or "").startswith(TEST_PREFIX) else " KEEP"
            print(f"  [{mark}] {ctr.id}  {ctr.name!r}  site={ctr.site}")

        to_delete = [c for c in all_counters if (c.name or "").startswith(TEST_PREFIX)]
        print(f"[counters] к удалению: {len(to_delete)}")
        for ctr in to_delete:
            try:
                res = await counters.delete(ctr.id)
                print(f"  удалён {ctr.id} ({ctr.name!r}): {res}")
            except AgentError as exc:
                print(f"  ОШИБКА удаления {ctr.id}: {exc.message}")

        # 3. Итог.
        remaining = await counters.list()
        print(f"[counters] осталось: {len(remaining)}")
        for ctr in remaining:
            print(f"    {ctr.id}  {ctr.name!r}  site={ctr.site}")


if __name__ == "__main__":
    asyncio.run(main())
