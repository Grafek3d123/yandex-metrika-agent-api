# Task04 — Final Production & AI-Agent Integration Audit (REPORT)

## 1. Executive Summary

Проведён финальный read-only аудит полного пути
**AI Agent → AI Tools → Services → Metrika Client → Transport → Yandex Metrica API**
после Task02/Task03. Проверялся фактический код, а не только REPORT-файлы.

- Проверены все 14 публичных AI-инструментов, goal lifecycle, Reports API,
  OAuth/PKCE, security, rate-limit/retry, counter resolution, idempotency,
  публичные экспорты, документация.
- **Исправлен один P1**: `metrika_update_goal` принимал и «применял» read-only
  поле `is_favorite`, которое `Goal.to_request` вырезает → инструмент возвращал
  ложный `status="ok"`, ничего не изменив. Поле полностью убрано из схемы и
  обработчика, добавлены regression-тесты.
- Синхронизирована документация (2×P2): `docs/yandex-metrika-ai.md` и `README.md`.
- **Найден новый P1 (не исправляется в рамках Task04)**: destructive-операция
  `metrika_delete_goal` выполняется без явного подтверждения (confirmation guard
  отсутствует). По safety-контракту это блокирующий дефект.

Итоговый статус: **NOT READY** (см. §7). P1 по `is_favorite` закрыт, но найден
другой P1 — отсутствие confirmation guard для destructive-операций.

---

## 2. Audit Matrix

| Area | Status | Evidence |
| --- | --- | --- |
| AI Tools (14) | PASS | `ai_tools/{base,counters,goals,analytics}.py`; схемы `additionalProperties:false`; вызов только через сервисы; HTTP-инструментов нет |
| Goals CRUD | PASS | `goals.py` + `models.Goal.to_request` (read-only `is_favorite`/`is_retargeting` вырезаны); endpoints POST/GET/PUT/DELETE |
| Reports | PASS | `models.Report.data: list[ReportRow]` (не `data.rows`); `ym:s:`/`ym:pv:` разделены (`metrics.py`, `reports.get_top_pages`, `_check_prefix`) |
| OAuth | PASS | `oauth.py`: PKCE S256, state gen+verify, `_ensure_scopes` (metrika:read); 401/403 → `AuthError` (не generic network) |
| Security | PASS | `log.SENSITIVE_KEYS`+`is_sensitive_key`; `TokenRecord(repr=False)`; `crypto.fingerprint`; `anonymize_query/headers`; `.env`/токены не в git (`git status` чист) |
| Rate limiting / retry | PASS | `transport.py`: 30/сек IP, 3 параллельных, 5000/сутки, 200/5мин `/stat/`; backoff+jitter, `Retry-After`; POST/DELETE повтор только при 420/429 или `ConnectError` до передачи |
| Counter resolution | PASS | `counters.resolve_one`: exact id / unique / ambiguous→`ValidationError(candidates)` / not found→`NotFoundError`; кэш изолирован по `connection_id` |
| Idempotency | PASS | `goals.ensure_goal`/`find_duplicates` — сигнатура без имени; повтор не дублирует |
| Real API | PASS (read-only) | `scripts/task04_live_check.py`: реальный `GET /management/v1/counters` 0.66 с, живой слой работает; полный lifecycle — 16/16 в Task03 |
| Documentation | PASS | README/docs приведены в соответствие (P2 исправлены) |
| Tests | PASS | pytest 157 passed; 3 новых regression-теста |
| **Destructive safety** | **FAIL** | `metrika_delete_goal` удаляет цель немедленно; confirmation guard отсутствует (grep: нет `confirm`/`dry_run`/`require_confirmation`) |

---

## 3. Bugs Found

### 3.1 P1 — `metrika_update_goal` принимает неприменяемое `is_favorite` (ИСПРАВЛЕНО)

- **Severity:** P1
- **File:** `yandex_metrika_agent/ai_tools/goals.py` (`_UPDATE_SCHEMA`, `_update_goal`)
- **Problem:** Схема и обработчик принимали `is_favorite` и помещали его в
  `Goal.model_copy(update=...)`, но `Goal.to_request` (`models.py`) вырезает это
  поле (фактический API отвергает `is_favorite` в PUT — `invalid_json, path:
  goal.is_favorite`). Инструмент возвращал `status="ok"` с неизменённой целью —
  молчаливое игнорирование намерения модели.
- **Root cause:** Расхождение между исправленным `to_request` (Task03) и слоем
  AI-инструментов, который не синхронизировали.
- **Fix:** `is_favorite` удалён из `_UPDATE_SCHEMA` и из `_update_goal`; сообщение
  об ошибке и описание инструмента приведены к `(name, price)`. Альтернативный
  `needs_input` намеренно НЕ добавлен — параметр, который API update не
  поддерживает, не должен приниматься вообще.
- **Regression test:** `tests/test_ai_tools.py`:
  - `test_update_goal_schema_has_no_is_favorite` — поле отсутствует в схеме;
  - `test_update_goal_is_favorite_only_is_error_no_put` — одиночный `is_favorite`
    не приводит к `PUT` (ловушка respx), ответ `error/ValidationError`;
  - `test_update_goal_name_price_works_without_is_favorite` — `name`/`price`
    применяются, тело `PUT` не содержит `is_favorite` (даже когда GET вернул его).

### 3.2 P1 — Деструктивные операции без явного подтверждения (НЕ ИСПРАВЛЕНО, следующий Task)

- **Severity:** P1 (safety)
- **File:** `yandex_metrika_agent/ai_tools/goals.py` (`_delete_goal`,
  `metrika_delete_goal`); `yandex_metrika_agent/ai_tools/base.py`
  (`ToolRegistry.call`)
- **Problem:** `metrika_delete_goal` выполняет `DELETE .../goal/{goalId}`
  немедленно при вызове. Confirmation guard отсутствует: нет параметра
  подтверждения, `dry_run`, двухшагового протокола или обязательного
  перечня удаляемого ресурса. AI-агент может удалить цель одним вызовом без
  явного подтверждения пользователя.
- **Root cause:** Слой AI-инструментов не реализует safety-контракт для
  необратимых операций; `ToolRegistry.call` передаёт аргументы в handler без
  проверки подтверждения (JSON-схема — декларация для агента, локальной
  валидации по схеме нет).
- **Mitigation (фактическое состояние):** `CounterService.delete()` (необратимое
  удаление счётчика) НЕ выставлен как AI-инструмент — через AI Tool Layer
  счётчик удалить нельзя. Write-операции требуют скоуп `metrika:write`.
- **Fix:** вне scope Task04 (требует дизайна контракта подтверждения и не должен
  смешиваться с точечным аудит-патчем). Требуется следующий Task.

---

## 4. Real API Evidence

- **Read-only live-check (после патча):** `scripts/task04_live_check.py`
  - `list_counters`: `status=ok`, elapsed 0.66 с, реальный
    `GET /management/v1/counters`;
  - аккаунт пуст (`count=0`) — соответствует очистке после Task03;
  - `SUMMARY status=ok`, `total_steps=2`, elapsed 0.94 с;
  - секреты/токены в вывод не попадали.
- **Полный lifecycle** (OAuth → create counter → goal create/get/update/stats/
  delete → traffic/sources/pages → ambiguous → no-tokens-in-logs): **16/16** в
  Task03 (см. `Tasks/Task03_REPORT.md`, коммит `2f59479`). Патч Task04 не
  изменяет `GoalService.update`/transport, поэтому повторный разрушающий прогон
  не выполнялся (он создавал бы тестовые счётчики, требующие ручной чистки).
- Реальные credentials/tokens в отчёте не публикуются.

---

## 5. Test Results

Реальные запуски в `.venv` (Python 3.14.6):

```text
pytest          157 passed  (было 154 + 3 новых regression-теста)
ruff check      All checks passed!  (yandex_metrika_agent, tests, scripts)
mypy            Success: no issues found in 35 source files  (strict)
compileall      OK  (yandex_metrika_agent, scripts/task04_live_check.py)
acceptance      read-only live-check: ok; полный lifecycle 16/16 (Task03)
```

---

## 6. Remaining Limitations

### 6.1 Исправлено в Task04

- **P1** `metrika_update_goal` / `is_favorite` — поле убрано из схемы и
  обработчика; добавлены 3 regression-теста; поведение `name`/`price` сохранено.
- **P2** `docs/yandex-metrika-ai.md` — убрано ложное утверждение о HTTP-endpoint
  `GET /stat/v1/metrics`; явно описан внешний каталог `MetricGroupPage`/`is_live`.
- **P2** `README.md` — пример больше не вызывает `url_goal` без импорта
  (использован `GoalService.create_url_goal`); из таблицы инструментов убрано
  «избранное» у `metrika_update_goal`.

### 6.2 Найдено, требует следующего Task

- **P1 (safety)** Деструктивные операции (`metrika_delete_goal`) выполняются без
  явного подтверждения. Нужен confirmation guard (обязательный параметр
  подтверждения / двухшаговый протокол / `dry_run`) и regression-тесты.
  Из-за этого итоговый статус — NOT READY.
  → **Закрыто в Task05** (confirmation guard ниже уровня handler'а,
  одноразовый HMAC-токен, привязка к connection/counter/goal, TTL). Детали —
  `Tasks/Task05_REPORT.md`.

### 6.3 Известные ограничения (не дефекты)

- Write-операции не проверяют `metrika:write` локально; при нехватке скоупа API
  возвращает 403 → типизированный `AuthError` (permission не скрыт как network).
- Справочник метрик по умолчанию статический (`is_live=False`): публичный Reports
  API не отдаёт список метрик по HTTP.
- JSON-схемы инструментов — декларация для агента; `ToolRegistry.call` не
  валидирует аргументы по схеме (handler читает только известные поля).
- Acceptance-скрипт Task03 интерактивен (OAuth в браузере) и оставляет тестовые
  счётчики; для CI нужен неинтерактивный режим (device/token) — вне scope Task04.
- Полный агентский CLI, MCP-обёртка, Logs/Segments/Imports — roadmap.

---

## 7. Final Verdict

```text
NOT READY
```

Обоснование: найден блокирующий **P1 (safety)** — destructive-операция
`metrika_delete_goal` выполняется без явного подтверждения, что нарушает
safety-контракт «невозможность выполнить destructive operation без явного
подтверждения». Точечный P1 по `is_favorite` исправлен и покрыт тестами, но
Task04 не закрывается только этим исправлением. После реализации confirmation
guard для destructive-операций (следующий Task) и повторных зелёных гейтов
статус может быть пересмотрен на READY.
