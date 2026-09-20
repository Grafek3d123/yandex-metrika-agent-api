"""Создать правдоподобные цели для демо-магазина (counter 112724717).

Идемпотентно: повтор не создаёт дубликат (совпадение по типу и параметрам).
Запуск:  python scripts/create_demo_goals.py [--delete GOAL_ID]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.config import load_settings
from yandex_metrika_agent.goals import GoalService

COUNTER_ID = 112724717

#: Цели, типичные для реального интернет-магазина.
GOALS = [
    {
        "kind": "url",
        "name": "Покупка — страница спасибо",
        "url": "/checkout/thank-you",
        "match": "contain",
        "price": 1250.0,
    },
    {
        "kind": "action",
        "name": "Добавление в корзину",
        "event": "add_to_cart",
        "match": "exact",
    },
    {
        "kind": "url",
        "name": "Регистрация — профиль",
        "url": "/profile/welcome",
        "match": "contain",
    },
    {
        "kind": "action",
        "name": "Переход в корзину",
        "event": "view_cart",
        "match": "exact",
    },
    {
        "kind": "url",
        "name": "Просмотр карточки товара",
        "url": "/product/",
        "match": "contain",
    },
]


def goal_json(result) -> dict:
    g = result.goal
    return {
        "id": g.id,
        "name": g.name,
        "type": g.type,
        "created": result.created,
    }


async def main(*, delete_id: int | None) -> int:
    settings = load_settings()
    async with MetrikaClient.from_settings(
        settings=settings, connection_id="task03"
    ) as client:
        goals = GoalService(client)
        if delete_id is not None:
            await goals.delete(COUNTER_ID, delete_id)
            print(json.dumps({"deleted": delete_id, "counter": COUNTER_ID}, ensure_ascii=False))
            return 0

        created: list[dict] = []
        for spec in GOALS:
            if spec["kind"] == "url":
                result = await goals.create_url_goal(
                    COUNTER_ID,
                    name=spec["name"],
                    url=spec["url"],
                    match=spec.get("match", "contain"),
                    price=spec.get("price"),
                )
            else:
                result = await goals.create_action_goal(
                    COUNTER_ID,
                    name=spec["name"],
                    event=spec["event"],
                    match=spec.get("match", "exact"),
                    price=spec.get("price"),
                )
            created.append(goal_json(result))

        print(
            json.dumps(
                {"counter": COUNTER_ID, "goals": created},
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", type=int, default=None, help="goal_id для удаления")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(delete_id=args.delete)))
