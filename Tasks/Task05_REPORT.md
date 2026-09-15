# Task05 — Destructive Safety: Confirmation Guard (REPORT)

## 1. Executive Summary

Реализован обязательный **confirmation guard** для разрушающих AI-инструментов.
`metrika_delete_goal` больше **не может выполнить DELETE без валидного
пользовательского подтверждения** — ни через `confirmed=true`, ни через любой
другой аргумент, который AI-агент способен сформировать сам.

- Guard находится **ниже уровня handler'а** — в `ToolRegistry.call`, до вызова
  обработчика; обойти его через публичный `call()` невозможно.
- Подтверждение — **одноразовый HMAC-токен**, привязанный к конкретной операции
  (`action` + `connection_id` + `counter_id` + `goal_id` + аргументы),
  ограниченный по времени (TTL 300 с по умолчанию).
- Токен выдаёт **доверенный хост** через отдельный метод
  `MetrikaTools.approve_confirmation()` — он не является инструментом,
  отсутствует в `specs()` и недостижим из AI Tool Layer.
- 18 новых regression-тестов (`tests/test_destructive_safety.py`) + live
  acceptance против реального API (8/8).

Итоговый статус: **READY** (в рамках scope Task05 — см. §7).

---

## 2. Механизм (как это работает)

### 2.1 Классификация инструментов

Каждый `Tool` обязан объявлять `safety: ToolSafety` — поле **без значения по
умолчанию**, собрать инструмент без классификации невозможно (`TypeError`;
mypy strict ловит на этапе проверки типов):

| Уровень | Смысл | Инструменты |
| --- | --- | --- |
| `READ_ONLY` | только чтение | 11 (счётчики, цели-чтение, вся аналитика) |
| `MUTATING` | обратимая запись | `metrika_create_goal`, `metrika_update_goal` |
| `DESTRUCTIVE` | необратимая операция | `metrika_delete_goal` |

`ToolRegistry` **отказывается регистрировать** `DESTRUCTIVE`-инструмент без
настроенной `ConfirmationPolicy` — новый разрушающий инструмент нельзя
случайно выпустить незащищённым (требование #9).

### 2.2 Guard ниже handler'а

Проверка выполняется в `ToolRegistry.call` **до** вызова `handler`:

```text
call(name, args)
  └─ tool.safety is DESTRUCTIVE?
       └─ да → _destructive_gate():
             1. отпечаток операции (resolve counter → counter_id)
             2. токена нет        → confirmation_required (+pending)   ← без API-запроса
             3. токен невалиден   → confirmation_required / error     ← без DELETE
             4. токен валиден     → handler → ровно один DELETE; подтверждение гасится
       └─ нет → handler сразу (READ_ONLY/MUTATING не гейтятся)
```

Handler `_delete_goal` сам подтверждение не проверяет — и не может быть вызван
в обход guard'а через публичный интерфейс.

### 2.3 Протокол подтверждения

1. **Первый вызов** `metrika_delete_goal {counter, goal_id}` без токена →
   `status="confirmation_required"` + `confirmation_id` + сводка
   (`action`, `counter_id`, `goal_id`, `expires_in_seconds`, `reversible:
   false`). DELETE **не отправляется**.
2. **Хост** после явного согласия пользователя:
   `tools.approve_confirmation(confirmation_id, approved_by="user")` →
   `{confirmation_token}`. Метод не входит в `specs()`, не регистрируется как
   инструмент и недоступен через `call()` (проверено тестом).
3. **Повторный вызов** с `confirmation_token` (все параметры совпадают) →
   `status="ok"`, ровно один DELETE, подтверждение погашено.

### 2.4 Токен и привязка

`confirmation_token = "<nonce>.<HMAC-SHA256(secret, nonce + fingerprint)>"`

- `secret` — `secrets.token_bytes(32)` процесса: агент не может вычислить
  HMAC и не может подделать токен;
- `fingerprint` = SHA-256 канонического JSON
  `{action, connection_id, target={counter_id, goal_id, ...аргументы}}` —
  токен привязан к точным параметрам; изменение `goal_id`/`counter`/
  `connection_id`/любого аргумента обесценивает токен;
- TTL: `ttl_seconds` (по умолчанию 300 с, настраивается; `clock` инъектируется
  для тестов);
- одноразовость: подтверждение гасится при успешной проверке, повторное
  применение → `error (reused)`;
- pending-операции идемпотентны по отпечатку: повторный вызов без токена
  возвращает тот же `confirmation_id`.

### 2.5 Отказы (DELETE никогда не выполняется)

| Ситуация | Ответ | `reason` |
| --- | --- | --- |
| токена нет (первый вызов) | `confirmation_required` | — |
| истёк TTL | `confirmation_required` | `expired` |
| токен от другой операции (иной connection/counter/goal/аргументы) | `confirmation_required` | `mismatch` |
| токен уже использован | `error` | `reused` |
| подделан / неизвестен / битый формат | `error` | `tampered` / `unknown` / `malformed` |
| `confirmed=true` или любые самодельные аргументы | `confirmation_required` | — |

---

## 3. Маппинг на требования Task05

| # | Требование | Статус | Реализация/тест |
| --- | --- | --- | --- |
| 1 | обязательный confirmation guard для destructive AI tools | ✅ | `ToolRegistry.call` → `_destructive_gate` |
| 2 | delete_goal без валидного подтверждения НЕ выполняет DELETE | ✅ | gate до handler; `test_delete_goal_without_confirmation_does_not_delete` |
| 3 | `confirmed=true` недостаточно, если агент может его сформировать | ✅ | HMAC-токен под секретом процесса; `test_confirmed_true_argument_does_not_bypass_guard`, `test_forged_confirmation_token_is_rejected` |
| 4 | привязка к action / connection_id / counter_id / goal_id / pending operation | ✅ | fingerprint + nonce; тесты 3–5 (другой goal/counter/connection) |
| 5 | ограниченный срок, не переиспользуем для другой операции | ✅ | TTL + одноразовость; `test_expired_...`, `test_reused_...`, `test_confirmation_for_other_*` |
| 6 | до подтверждения — структурированный `confirmation_required`, без API-запроса | ✅ | `ToolResult.confirmation_required`; DELETE-ловушки `call_count == 0` |
| 7 | после корректного подтверждения DELETE выполняется | ✅ | `test_correct_confirmation_executes_exactly_one_delete` (1 DELETE) |
| 8 | неверное/просроченное/чужое подтверждение → без DELETE | ✅ | таблица §2.5; тесты 3–7 |
| 9 | guard ниже handler'а; новый destructive tool нельзя случайно без safety | ✅ | обязательное поле `Tool.safety` (без default) + запрет реестра DESTRUCTIVE без policy; `test_tool_requires_explicit_safety`, `test_registry_rejects_destructive_without_policy` |
| 10 | НЕ добавлять `metrika_delete_counter` | ✅ | не добавлен; `test_only_delete_goal_is_destructive` фиксирует множество DESTRUCTIVE |

Обязательные regression-сценарии — все 10 покрыты в
`tests/test_destructive_safety.py` (18 тестов, вкл. доп. инварианты).

---

## 4. Реальный API (live acceptance)

`scripts/task05_acceptance.py` — безопасный прогон: удаляется **только цель,
созданная самим сценарием**. Прогон 2026-09-16 — **8/8 ok**:

| Шаг | Результат |
| --- | --- |
| counter_ready | ok (переиспользован существующий счётчик 112680665) |
| goal_ready | ok (создана тестовая цель) |
| delete_without_confirmation | ok: `confirmation_required`, цель **существует** |
| delete_with_forged_token | ok: `error`, цель **существует** |
| approve | ok: токен выдан хостом |
| delete_with_valid_confirmation | ok: `status="ok"`, цель **удалена** (ровно один DELETE) |
| delete_with_reused_token | ok: `error (reused)` |
| SUMMARY | ok, failed_steps=[] |

Результаты: `scripts/task05_results.json`. Секреты в вывод не попадают.

---

## 5. Quality Gates (финальный прогон)

```text
pytest          175 passed  (157 + 18 новых destructive-safety)
ruff check      All checks passed!  (yandex_metrika_agent, tests, scripts)
mypy            Success: no issues found in 36 source files  (strict)
compileall      OK  (yandex_metrika_agent, scripts)
live acceptance 8/8 ok (scripts/task05_acceptance.py, реальный API)
```

---

## 6. Изменённые файлы

| Файл | Изменение |
| --- | --- |
| `ai_tools/base.py` | `ToolSafety`; `Tool.safety` (обязательное); `ToolResult.confirmation_required` + `confirmation_id`; `PendingConfirmation`, `ConfirmationPolicy` (HMAC, TTL, single-use, clock); guard в `ToolRegistry.call`; `approve`/`pending_confirmations`; запрет DESTRUCTIVE без policy |
| `ai_tools/__init__.py` | `MetrikaTools(confirmations=...)`, `approve_confirmation()`, `pending_confirmations()`; экспорты |
| `ai_tools/counters.py`, `analytics.py` | `safety=READ_ONLY` (9 инструментов) |
| `ai_tools/goals.py` | `safety=` классификация 5 инструментов; комментарий-инвариант в `_delete_goal`; описание delete-инструмента |
| `__init__.py` (пакет) | экспорт `ConfirmationPolicy`, `ToolSafety` |
| `tests/test_destructive_safety.py` | 18 regression-тестов (10 обязательных сценариев + инварианты) |
| `scripts/task05_acceptance.py` | live acceptance destructive safety |
| `README.md`, `docs/yandex-metrika-ai.md` | раздел «Подтверждение разрушающих операций» (§18.1 в docs) |
| `Tasks/Task04_REPORT.md` | cross-ref: P1 (safety) закрыт в Task05 |

---

## 7. Final Verdict

```text
READY
```

Критерий готовности выполнен: **невозможно выполнить `metrika_delete_goal`
через AI Tool Layer без валидного пользовательского подтверждения**, даже если
AI самостоятельно передаст `confirmed=true` или изменит аргументы операции
(изменение аргументов обесценивает токен; подделка HMAC невозможна без секрета
процесса; повторное/просроченное применение отклоняется).

Известные ограничения (не блокеры):

- Доверенная сторона — процесс хоста: если интегратор сам выставит
  `approve_confirmation` агенту как инструмент, guard'ы обесценятся; в
  библиотеке метод намеренно не является инструментом и покрыт тестом.
- Подтверждения хранятся in-memory (per-process): после рестарта хоста
  pending-операции сбрасываются — агент получит новый
  `confirmation_required`, выполнение без подтверждения невозможно.
- `CounterService.delete()` по-прежнему не выставлен как AI-инструмент
  (требование #10 Task05); при будущей экспозиции он обязан получить
  `safety=DESTRUCTIVE` и автоматически попадёт под guard.
