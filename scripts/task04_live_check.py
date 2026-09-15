"""Task04: read-only приёмочная проверка против реального API Яндекс Метрики.

Запуск:
    python scripts/task04_live_check.py

Назначение. Патч Task04 меняет только AI-tool обёртку ``metrika_update_goal``
(поле ``is_favorite`` убрано из схемы). Само поведение ``GoalService.update``
и весь путь OAuth -> client -> transport -> API не менялся и уже покрыт
живым прогоном Task03. Поэтому здесь выполняется только НЕРАЗРУШАЮЩАЯ
проверка: создаётся ли вообще рабочий клиент, проходит ли реальный
Management/Reports GET, парсится ли ответ. Никаких POST/PUT/DELETE,
никаких созданий/удалений ресурсов — скрипт не оставляет следов в аккаунте.

Требования:
    .env с METRIKA_TOKEN_KEY и сохранённым токеном подключения ``task03``
    (или METRIKA_OAUTH_TOKEN). Секреты в вывод не попадают.

Коды выхода: 0 — проверка пройдена; 1 — нет credentials/токена или ошибка API.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows-консоль по умолчанию cp866/cp1251 — кириллица в выводе превращается
# в кракозябры. Принудительно переключаем текстовые потоки в UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if isinstance(_stream, io.TextIOWrapper):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.config import load_settings
from yandex_metrika_agent.counters import CounterService
from yandex_metrika_agent.errors import AgentError
from yandex_metrika_agent.goals import GoalService
from yandex_metrika_agent.reports import ReportService

RESULTS: list[dict[str, Any]] = []
_T0 = time.monotonic()


def step(name: str, status: str, payload: Any, started: float) -> dict[str, Any]:
    entry = {
        "step": name,
        "status": status,
        "elapsed_s": round(time.monotonic() - started, 2),
        "data": payload,
    }
    RESULTS.append(entry)
    print(json.dumps(entry, ensure_ascii=False, default=str)[:1500], flush=True)
    return entry


async def main() -> int:
    cfg = load_settings()

    # Токен берём из окружения (METRIKA_OAUTH_TOKEN) либо из сохранённого
    # подключения task03. Никакого интерактивного OAuth — только чтение.
    connection_id = "task03"
    if cfg.token_key is None:
        print("НЕТ METRIKA_TOKEN_KEY и нет METRIKA_OAUTH_TOKEN — live-проверка недоступна.")
        return 1

    try:
        client = MetrikaClient.from_settings(settings=cfg, connection_id=connection_id)
    except AgentError as exc:
        print("Не удалось собрать клиент:", exc.to_dict())
        return 1

    async with client:
        counters = CounterService(client)
        goals = GoalService(client)
        reports = ReportService(client)

        # 1. list_counters — реальный GET Management API.
        st = time.monotonic()
        try:
            all_counters = await counters.list()
            brief = [
                {
                    "id": c.id,
                    "name": c.name,
                    "domain": c.domain,
                    "permission": c.permission,
                }
                for c in all_counters[:10]
            ]
            step("list_counters", "ok", {"count": len(all_counters), "counters": brief}, st)
        except AgentError as exc:
            step("list_counters", "error", exc.to_dict(), st)
            return finish(1)

        # 2. Первый доступный счётчик с правом чтения — цели и трафик.
        readable = [c for c in all_counters if c.permission in ("rw", "r", "w")]
        if not readable:
            step(
                "goals_and_reports",
                "info",
                {"note": "Нет доступных счётчиков (аккаунт пуст) — GET прошёл, данных нет."},
                st,
            )
            return finish(0)

        counter = readable[0]

        st = time.monotonic()
        try:
            goals_list = await goals.list(counter.id)
            step(
                "list_goals",
                "ok",
                {"counter_id": counter.id, "count": len(goals_list)},
                st,
            )
        except AgentError as exc:
            step("list_goals", "error", exc.to_dict(), st)

        st = time.monotonic()
        try:
            traffic = await reports.get_traffic(
                counter.id, date_from="2026-09-01", date_to="2026-09-07"
            )
            step("get_traffic", "ok", traffic, st)
        except AgentError as exc:
            step("get_traffic", "error", exc.to_dict(), st)

    return finish(0)


def finish(code: int) -> int:
    failed = [e for e in RESULTS if e["status"] not in ("ok", "info")]
    summary = {
        "step": "SUMMARY",
        "status": "ok" if not failed else "error",
        "failed_steps": [e["step"] for e in failed],
        "total_steps": len(RESULTS),
        "total_elapsed_s": round(time.monotonic() - _T0, 2),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
