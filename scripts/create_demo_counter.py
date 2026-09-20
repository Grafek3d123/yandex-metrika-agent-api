"""Создать тестовый счётчик Метрики, эмитирующий реальный сайт.

Использует зашифрованное хранилище токенов (connection_id из .env-ключа
METRIKA_TOKEN_KEY). Секреты в вывод не попадают.

Запуск:  python scripts/create_demo_counter.py [--delete ID]

--delete ID — удалить счётчик (необратимо), для уборки после теста.
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
from yandex_metrika_agent.counters import CounterService
from yandex_metrika_agent.tokens import EncryptedFileStore

#: Название и сайт тестового счётчика (домен из зарезервированной зоны
#: example.org — реальные владельцы доменов не затрагиваются).
COUNTER_NAME = "E2E Demo-магазин (тестовый)"
COUNTER_SITE = "e2e-demo-shop.example.org"
#: UTC+3 (Москва), в секундах.
COUNTER_TIMEZONE = 10800

#: Пробуем подключения по очереди: task03 (заводское имя из acceptance- прогонов),
#: затем прочие. Первый hit сохранным токеном выигрывает.
CANDIDATE_CONNECTIONS = ("task03", "1afda6454693", "default")


def pick_connection(settings) -> str | None:
    """Найти connection_id с сохранённым токеном (без вывода секретов)."""

    if settings.token_key is None:
        return None
    store = EncryptedFileStore(settings.token_dir, key=settings.token_key)
    for connection_id in CANDIDATE_CONNECTIONS:
        try:
            if store.get(connection_id) is not None:
                return connection_id
        except Exception:  # noqa: BLE001 - повреждённая запись не должна ломать подбор
            continue
    return None


def counter_json(counter) -> dict:
    return {
        "id": counter.id,
        "name": counter.name,
        "site": counter.site,
        "domain": counter.domain,
        "status": counter.status,
        "owner_login": counter.owner_login,
        "timezone": counter.timezone,
        "created_at": counter.created_at,
    }


async def main(*, delete_id: int | None) -> int:
    settings = load_settings()
    async with MetrikaClient.from_settings(
        settings=settings, connection_id="task03"
    ) as client:
        counters = CounterService(client)
        if delete_id is not None:
            result = await counters.delete(delete_id)
            print(json.dumps({"deleted": delete_id, "api": result}, ensure_ascii=False))
            return 0

        existing = await counters.resolve(COUNTER_SITE)
        if existing:
            print(
                json.dumps(
                    {
                        "reused": True,
                        "counter": counter_json(existing[0]),
                        "hint": f"Счётчик для {COUNTER_SITE} уже существует.",
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        created = await counters.create(
            name=COUNTER_NAME, site=COUNTER_SITE, timezone=COUNTER_TIMEZONE
        )
        # Верификация: get по id и resolve по домену (путь AI-инструментов).
        fetched = await counters.get(created.id)
        resolved = await counters.resolve_one(COUNTER_SITE)
        print(
            json.dumps(
                {
                    "created": True,
                    "counter": counter_json(created),
                    "verify_get_id_matches": fetched.id == created.id,
                    "verify_resolve_by_domain_matches": resolved.id == created.id,
                },
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", type=int, default=None, help="counter_id для удаления")
    args = parser.parse_args()

    if args.delete is None:
        settings = load_settings()
        if pick_connection(settings) is None:
            print(
                json.dumps(
                    {
                        "error": "no_token",
                        "message": "Нет METRIKA_TOKEN_KEY или сохранённого токена "
                        f"в {settings.token_dir}. Выполните auth.",
                    },
                    ensure_ascii=False,
                )
            )
            sys.exit(2)

    sys.exit(asyncio.run(main(delete_id=args.delete)))
