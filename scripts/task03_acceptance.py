"""Task03: реальный приёмочный прогон против API Яндекс Метрики.

Запуск:
    python scripts/task03_acceptance.py

Требования:
    .env с YANDEX_CLIENT_ID / YANDEX_CLIENT_SECRET (свои OAuth-клиент),
    METRIKA_TOKEN_KEY (64 hex). Скоупы запрашиваются metrika:read+metrika:write.

Ход прогона (по Tasks/Task03.md):
    1. Реальный OAuth (browser-flow, PKCE, loopback-callback).
    2. list_counters.
    3. Создание нового тестового счётчика.
    4. resolve счётчика по имени.
    5. Список целей счётчика.
    6. Создание тестовой цели (url-цель) идемпотентно.
    7. get цели.
    8. update цели (переименование).
    9. Статистика цели (Reports API).
    10. Удаление цели.
    11. Отчёты: traffic / sources / top pages.
    12. needs_input для неоднозначного счётчика (создаётся второй
        счётчик с тем же именем).
    13. Проверка отсутствия токенов в логах.

Результат: JSON-вывод по шагам + лог-файл task03_run.log рядом со скриптом.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
import time
from dataclasses import replace
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
from yandex_metrika_agent.crypto import generate_key
from yandex_metrika_agent.errors import AgentError
from yandex_metrika_agent.goals import GoalService, url_goal
from yandex_metrika_agent.models import Goal
from yandex_metrika_agent.oauth import METRIKA_ALL_SCOPES, OAuthClient, OAuthFlow
from yandex_metrika_agent.reports import ReportService
from yandex_metrika_agent.tokens import EncryptedFileStore

LOG_PATH = Path(__file__).resolve().parent / "task03_run.log"
RESULTS: list[dict[str, Any]] = []

# Лог в файл с полным DEBUG — его потом проверим на отсутствие секретов.
logger = logging.getLogger("task03")
logger.setLevel(logging.DEBUG)
_handler = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
logging.getLogger().addHandler(_handler)
logging.getLogger().setLevel(logging.DEBUG)


def step(name: str, status: str, payload: Any, started: float) -> dict[str, Any]:
    entry = {
        "step": name,
        "status": status,
        "elapsed_s": round(time.monotonic() - started, 2),
        "data": payload,
    }
    RESULTS.append(entry)
    print(json.dumps(entry, ensure_ascii=False, default=str)[:2000], flush=True)
    return entry


def counter_brief(c: Any) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "site": c.site,
        "domain": c.domain,
        "permission": c.permission,
        "owner_login": getattr(c, "owner_login", None),
    }


def goal_brief(g: Goal) -> dict[str, Any]:
    return {
        "id": g.id,
        "name": g.name,
        "type": g.type,
        "conditions": [cond.model_dump(exclude_none=True) for cond in (g.conditions or [])],
    }


async def main() -> int:
    cfg = load_settings()
    if not cfg.client_id or not cfg.client_secret:
        print("ОШИБКА: задайте YANDEX_CLIENT_ID / YANDEX_CLIENT_SECRET в .env")
        return 1
    if cfg.token_key is None:
        # Ключ не задан — сгенерировать и показать один раз (не сохраняем в git).
        key = generate_key()
        print(f"METRIKA_TOKEN_KEY не задан. Одноразовый ключ для прогона: {key}")
        cfg = replace(cfg, token_key=bytes.fromhex(key))

    # --- Шаг 1: реальный OAuth (browser-flow, PKCE) -------------------------
    # Если токен уже сохранён (повторный прогон) — браузер не открываем.
    st = time.monotonic()
    oauth_client = OAuthClient(client_id=cfg.client_id, client_secret=cfg.client_secret)
    store = EncryptedFileStore(cfg.token_dir, key=cfg.token_key)
    saved = store.get("task03")
    if saved is not None:
        record = saved
        step(
            "oauth",
            "ok",
            {
                "connection_id": record.connection_id,
                "user_login": record.user_login,
                "scopes": list(record.scopes),
                "reused_saved_token": True,
                # Яндекс не всегда возвращает scopes в token-ответе; пустой
                # список не ошибка, т.к. _ensure_scopes при авторизации уже
                # проверил metrika:read, а токен подтверждается API-запросами.
            },
            st,
        )
    else:
        flow = OAuthFlow(client=oauth_client, store=store, redirect_uri=cfg.redirect_uri)
        flow.scopes = METRIKA_ALL_SCOPES
        print(">>> Откроется браузер для входа в Яндекс. Подтвердите доступ.", flush=True)
        record = await flow.connect("task03", method="browser", timeout=300.0)
        step(
            "oauth",
            "ok" if METRIKA_ALL_SCOPES[0] in record.scopes else "error",
            {
                "connection_id": record.connection_id,
                "user_login": record.user_login,
                "scopes": list(record.scopes),
                "token_in_output": bool(record.access_token),  # сам токен НЕ печатаем
            },
            st,
        )

    async with MetrikaClient.from_settings(settings=cfg, connection_id="task03") as client:
        counters = CounterService(client)
        goals = GoalService(client)
        reports = ReportService(client)

        # --- Шаг 2: list_counters ------------------------------------------
        st = time.monotonic()
        try:
            all_counters = await counters.list()
            step(
                "list_counters",
                "ok",
                {
                    "count": len(all_counters),
                    "counters": [counter_brief(c) for c in all_counters[:10]],
                },
                st,
            )
        except AgentError as exc:
            step("list_counters", "error", exc.to_dict(), st)
            return finish(1)

        # --- Шаг 3: создать тестовый счётчик --------------------------------
        st = time.monotonic()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        test_name = f"Task03 acceptance {stamp}"
        try:
            created = await counters.create(name=test_name, site="task03-acceptance.example")
            step("create_counter", "ok", counter_brief(created), st)
        except AgentError as exc:
            step("create_counter", "error", exc.to_dict(), st)
            return finish(1)
        test_counter_id = created.id

        # --- Шаг 4: resolve счётчика по имени -------------------------------
        st = time.monotonic()
        try:
            matched = await counters.resolve(test_name)
            step(
                "resolve_counter",
                "ok",
                {"query": test_name, "matches": [counter_brief(c) for c in matched]},
                st,
            )
        except AgentError as exc:
            step("resolve_counter", "error", exc.to_dict(), st)

        # --- Шаг 5: реальные goals счётчика ---------------------------------
        st = time.monotonic()
        try:
            existing = await goals.list(test_counter_id)
            step("list_goals", "ok", {"counter_id": test_counter_id, "count": len(existing)}, st)
        except AgentError as exc:
            step("list_goals", "error", exc.to_dict(), st)

        # --- Шаг 6: создать тестовую цель ------------------------------------
        st = time.monotonic()
        goal = url_goal(name=f"task03 thank-you {stamp}", url="/thank-you")
        try:
            result = await goals.ensure_goal(test_counter_id, goal)
            step(
                "create_goal",
                "ok",
                {
                    "created": result.created,
                    "goal": goal_brief(result.goal),
                    "warnings": result.warnings,
                },
                st,
            )
        except AgentError as exc:
            step("create_goal", "error", exc.to_dict(), st)
            return finish(1)
        goal_id = result.goal.id
        assert goal_id is not None  # только что созданная цель всегда с id

        # --- Шаг 7: get цели --------------------------------------------------
        st = time.monotonic()
        try:
            fetched = await goals.get(test_counter_id, goal_id)
            step("get_goal", "ok", goal_brief(fetched), st)
        except AgentError as exc:
            step("get_goal", "error", exc.to_dict(), st)

        # --- Шаг 8: update цели ----------------------------------------------
        st = time.monotonic()
        try:
            renamed = result.goal.model_copy(update={"name": f"task03 renamed {stamp}"})
            updated = await goals.update(test_counter_id, renamed)
            step("update_goal", "ok", goal_brief(updated), st)
        except AgentError as exc:
            step("update_goal", "error", exc.to_dict(), st)

        # --- Шаг 9: статистика цели ------------------------------------------
        st = time.monotonic()
        try:
            stats = await reports.get_goal_stats(
                test_counter_id, goal_id, date_from="2026-09-01", date_to="2026-09-14"
            )
            step("get_goal_stats", "ok", stats, st)
        except AgentError as exc:
            step("get_goal_stats", "error", exc.to_dict(), st)

        # --- Шаг 10: удалить цель --------------------------------------------
        st = time.monotonic()
        try:
            await goals.delete(test_counter_id, goal_id)
            step("delete_goal", "ok", {"goal_id": goal_id}, st)
        except AgentError as exc:
            step("delete_goal", "error", exc.to_dict(), st)

        # --- Шаг 11: отчёты (traffic / sources / pages) ----------------------
        # Новый счётчик без трафика — проверяем, что запросы проходят и ответ
        # парсится (пустые данные — валидный результат для нового счётчика).
        st = time.monotonic()
        try:
            traffic = await reports.get_traffic(
                test_counter_id, date_from="2026-09-01", date_to="2026-09-14"
            )
            step("get_traffic", "ok", traffic, st)
        except AgentError as exc:
            step("get_traffic", "error", exc.to_dict(), st)

        st = time.monotonic()
        try:
            sources = await reports.get_sources(
                test_counter_id, date_from="2026-09-01", date_to="2026-09-14"
            )
            step("get_sources", "ok", {"report_rows": len(sources)}, st)
        except AgentError as exc:
            step("get_sources", "error", exc.to_dict(), st)

        st = time.monotonic()
        try:
            pages = await reports.get_top_pages(
                test_counter_id, date_from="2026-09-01", date_to="2026-09-14"
            )
            step("get_top_pages", "ok", {"report_rows": len(pages)}, st)
        except AgentError as exc:
            step("get_top_pages", "error", exc.to_dict(), st)

        # --- Шаг 12: needs_input для неоднозначного счётчика ------------------
        st = time.monotonic()
        try:
            # Второй счётчик с тем же именем → resolve даст 2 кандидата.
            await counters.create(name=test_name, site="task03-acceptance-2.example")
            ambiguous = await counters.resolve_one(test_name)
            step("ambiguous_counter", "error", {"unexpected_single": counter_brief(ambiguous)}, st)
        except AgentError as exc:
            # Ожидаем ValidationError с кандидатами.
            candidates = exc.details.get("candidates") or []
            ok = len(candidates) >= 2
            step("ambiguous_counter", "ok" if ok else "error", exc.to_dict(), st)

        # --- Шаг 13: токены не попадают в логи --------------------------------
        st = time.monotonic()
        log_text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
        leaked: list[str] = []
        if record.access_token and record.access_token in log_text:
            leaked.append("access_token")
        if record.refresh_token and record.refresh_token in log_text:
            leaked.append("refresh_token")
        step(
            "no_tokens_in_logs",
            "ok" if not leaked else "FAIL",
            {"leaked": leaked, "log_file": str(LOG_PATH)},
            st,
        )

        # --- Уборка: удалить созданные счётчики нельзя через публичный API
        # счётчика в нашей библиотеке — оставляем их в аккаунте и помечаем.
        step(
            "cleanup_note",
            "info",
            {
                "created_counters_left_in_account": [test_counter_id],
                "note": "Счётчики можно удалить вручную в интерфейсе Метрики.",
            },
            st,
        )

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
    RESULTS.append(summary)
    (Path(__file__).resolve().parent / "task03_results.json").write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return code


_T0 = time.monotonic()

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
