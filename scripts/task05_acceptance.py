"""Task05: приёмочный прогон destructive safety против реального API Метрики.

Запуск:
    python scripts/task05_acceptance.py

Сценарий (безопасный: удаляется ТОЛЬКО цель, созданная этим прогоном):

    1. Подготовка: счётчик (существующий rw или новый тестовый) и тестовая цель.
    2. AI-вызов metrika_delete_goal БЕЗ подтверждения
       → ожидаем confirmation_required, DELETE не отправляется,
          цель по-прежнему существует (GET 200).
    3. AI-вызов с подделанным токеном (агент не может получить настоящий)
       → ожидаем отказ (error/confirmation_required), цель существует.
    4. Хост подтверждает: approve_confirmation (НЕ инструмент).
    5. AI-вызов с валидным токеном
       → ожидаем ok и ровно один DELETE; цель исчезла (GET 404).
    6. Повторное применение того же токена
       → ожидаем error (reused), повторного DELETE нет.

Требования: .env (METRIKA_TOKEN_KEY) и сохранённый токен подключения ``task03``
(или METRIKA_OAUTH_TOKEN). Секреты в вывод не попадают.

Коды выхода: 0 — контракт подтверждён; 1 — нет credentials или нарушение контракта.
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

# Windows-консоль: переключаем текстовые потоки в UTF-8 (иначе кракозябры).
for _stream in (sys.stdout, sys.stderr):
    if isinstance(_stream, io.TextIOWrapper):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from yandex_metrika_agent.ai_tools import MetrikaTools
from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.config import load_settings
from yandex_metrika_agent.counters import CounterService
from yandex_metrika_agent.errors import AgentError, ApiError, NotFoundError
from yandex_metrika_agent.goals import GoalService, url_goal

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
    RESULTS.append(summary)
    (Path(__file__).resolve().parent / "task05_results.json").write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return code


async def _goal_exists(
    goals: GoalService, counter_id: int, goal_id: int
) -> bool:
    """Цель доступна? Удалённая цель отдаёт 404 либо 400 «No object with ID»."""

    try:
        await goals.get(counter_id, goal_id)
        return True
    except (NotFoundError, ApiError):
        return False


async def main() -> int:
    cfg = load_settings()
    if cfg.token_key is None:
        print("НЕТ METRIKA_TOKEN_KEY и нет METRIKA_OAUTH_TOKEN — live-проверка недоступна.")
        return 1

    try:
        client = MetrikaClient.from_settings(settings=cfg, connection_id="task03")
    except AgentError as exc:
        print("Не удалось собрать клиент:", exc.to_dict())
        return 1

    async with client:
        counters = CounterService(client)
        goals = GoalService(client)
        tools = MetrikaTools(client)

        # --- Подготовка: счётчик и тестовая цель ---------------------------
        st = time.monotonic()
        try:
            # Собственные счётчики API помечает permission="own", выданные — "rw".
            existing = [
                c for c in await counters.list() if c.permission in ("rw", "own", "edit")
            ]
        except AgentError as exc:
            step("list_counters", "error", exc.to_dict(), st)
            return finish(1)
        if existing:
            counter = existing[0]
            created_counter = False
        else:
            try:
                counter = await counters.create(
                    name=f"Task05 safety {time.strftime('%Y%m%d-%H%M%S')}",
                    site="task05-safety.example",
                )
                created_counter = True
            except AgentError as exc:
                step("create_counter", "error", exc.to_dict(), st)
                return finish(1)
        step(
            "counter_ready",
            "ok",
            {"counter_id": counter.id, "created": created_counter},
            st,
        )

        st = time.monotonic()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            made = await goals.ensure_goal(
                counter.id, url_goal(name=f"task05 safety {stamp}", url="/task05")
            )
        except AgentError as exc:
            step("create_goal", "error", exc.to_dict(), st)
            return finish(1)
        goal_id = made.goal.id
        assert goal_id is not None  # только что созданная цель всегда с id
        step("goal_ready", "ok", {"goal_id": goal_id, "created": made.created}, st)

        args = {"counter": counter.id, "goal_id": goal_id}

        # --- Шаг 1: delete без подтверждения → confirmation_required -------
        st = time.monotonic()
        first = await tools.call("metrika_delete_goal", dict(args))
        blocked = first.get("status") == "confirmation_required" and first.get("confirmation_id")
        if not blocked:
            step("delete_without_confirmation", "FAIL", first, st)
            return finish(1)
        confirmation_id = str(first["confirmation_id"])
        still_exists = await _goal_exists(goals, counter.id, goal_id)
        ok = still_exists
        step(
            "delete_without_confirmation",
            "ok" if ok else "FAIL",
            {
                "status": first.get("status"),
                "confirmation_id": confirmation_id,
                "goal_still_exists": still_exists,
            },
            st,
        )
        if not ok:
            return finish(1)

        # --- Шаг 2: подделанный токен → отказ, цель цела -------------------
        st = time.monotonic()
        forged = await tools.call(
            "metrika_delete_goal", {**args, "confirmation_token": "forged.deadbeef"}
        )
        still_exists = await _goal_exists(goals, counter.id, goal_id)
        ok = forged.get("status") in ("error", "confirmation_required") and still_exists
        step(
            "delete_with_forged_token",
            "ok" if ok else "FAIL",
            {"status": forged.get("status"), "goal_still_exists": still_exists},
            st,
        )
        if not ok:
            return finish(1)

        # --- Шаг 3: хост подтверждает (не инструмент) ----------------------
        st = time.monotonic()
        try:
            issued = tools.approve_confirmation(confirmation_id, approved_by="task05-acceptance")
        except AgentError as exc:
            step("approve", "error", exc.to_dict(), st)
            return finish(1)
        step(
            "approve",
            "ok",
            {"confirmation_id": confirmation_id, "token_issued": True},
            st,
        )

        # --- Шаг 4: валидный токен → ровно один DELETE ----------------------
        st = time.monotonic()
        done = await tools.call(
            "metrika_delete_goal", {**args, "confirmation_token": issued["confirmation_token"]}
        )
        gone = not await _goal_exists(goals, counter.id, goal_id)
        ok = done.get("status") == "ok" and gone
        step(
            "delete_with_valid_confirmation",
            "ok" if ok else "FAIL",
            {"status": done.get("status"), "goal_deleted": gone},
            st,
        )
        if not ok:
            return finish(1)

        # --- Шаг 5: повторное применение токена → отказ ---------------------
        st = time.monotonic()
        reuse = await tools.call(
            "metrika_delete_goal", {**args, "confirmation_token": issued["confirmation_token"]}
        )
        ok = reuse.get("status") == "error"
        reuse_reason = reuse.get("error", {}).get("details", {}).get("reason")
        step(
            "delete_with_reused_token",
            "ok" if ok else "FAIL",
            {"status": reuse.get("status"), "reason": reuse_reason},
            st,
        )
        if not ok:
            return finish(1)

        # --- Уборка ---------------------------------------------------------
        step(
            "cleanup_note",
            "info",
            {
                "test_counter_left": created_counter,
                "counter_id": counter.id if created_counter else None,
                "note": (
                    "Тестовый счётчик можно удалить вручную в интерфейсе Метрики."
                    if created_counter
                    else "Использован существующий счётчик, residue нет."
                ),
            },
            st,
        )

    return finish(0)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
