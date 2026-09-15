# Task04 — Final Production & AI-Agent Integration Audit

## Цель

Провести финальный технический аудит репозитория `yandex-metrika-agent-api` после Task02 и Task03.

Главная задача Task04 — не расширять функциональность проекта, а доказать, что существующая реализация действительно готова к использованию внутри AI-agent / KodaCode.

Нужно проверить полный путь:

**AI Agent → AI Tools → Services → Metrika Client → Transport → Yandex Metrica API**

и устранить все найденные P0/P1 проблемы.

После Task04 проект должен иметь однозначный статус:

**READY**

или

**NOT READY**

Нельзя объявлять READY, если остаются известные P0/P1 дефекты.

---

# 1. Что НЕ нужно делать

Не расширять scope проекта без необходимости.

Не добавлять:

* Logs API;
* Segments API;
* Data Import API;
* Offline conversions;
* полноценный CLI;
* новые крупные бизнес-функции;
* MCP server, если он не требуется для текущей интеграции KodaCode.

MCP можно оставить как отдельный future task.

Не переписывать рабочую архитектуру только ради stylistic improvements.

Главный приоритет:

**correctness > API compatibility > security > reliability > tests > cosmetics.**

---

# 2. Обязательный аудит репозитория

Перед изменениями изучить:

* `README.md`
* `Tasks/Task02_REPORT.md`
* `Tasks/Task03_REPORT.md`
* `yandex_metrika_agent/`
* `tests/`
* `scripts/task03_acceptance.py`
* конфигурацию проекта
* package metadata
* публичные exports

Сопоставить заявленные в Task02/Task03 результаты с фактическим кодом.

Важно:

**не доверять только REPORT-файлам.**

Если report говорит, что функция исправлена, проверить реальную реализацию.

---

# 3. P0 — AI Tool Layer

Провести полный аудит всех публичных AI tools.

Проверить минимум:

* `metrika_list_counters`
* `metrika_get_counter`
* `metrika_list_goals`
* `metrika_get_goal`
* `metrika_create_goal`
* `metrika_update_goal`
* `metrika_delete_goal`
* `metrika_get_report`
* `metrika_get_traffic`
* `metrika_get_traffic_by_day`
* `metrika_get_sources`
* `metrika_get_top_pages`
* `metrika_get_goal_stats`
* `metrika_compare_periods`

Для каждого tool проверить:

### Input

* строгая schema;
* обязательные параметры;
* defaults;
* типы;
* enum values;
* validation;
* понятные ошибки;
* отсутствие возможности передать произвольный HTTP request.

### Execution

Tool должен использовать существующий service/client layer.

AI Tool не должен напрямую реализовывать HTTP-запросы к Yandex API.

### Output

Ответ должен быть пригоден для AI agent:

* структурированный;
* предсказуемый;
* без внутренних implementation details;
* без access token;
* без refresh token;
* без лишних HTTP headers;
* без traceback в normal error response.

### Errors

Проверить отдельные сценарии:

* missing counter;
* ambiguous counter;
* missing goal;
* invalid date range;
* invalid goal data;
* insufficient scope;
* expired auth;
* Yandex API error;
* rate limit;
* network error.

Ошибка должна быть machine-readable и понятной агенту.

---

# 4. P0 — Natural-language readiness

Проверить, что AI layer действительно скрывает низкоуровневые Metrica concepts.

Например:

AI должен иметь возможность обработать:

> Покажи посещаемость сайта за последнюю неделю.

без необходимости самостоятельно придумывать:

* `ym:s:visits`;
* `ym:s:date`;
* `date1`;
* `date2`.

---

Проверить:

> Покажи источники трафика за последние 30 дней.

---

Проверить:

> Покажи топ страниц по просмотрам.

---

Проверить:

> Покажи конверсию по цели "Оплата".

---

Проверить:

> Сравни посещаемость за последние 7 дней с предыдущими 7 днями.

---

Проверить:

> Создай цель "Отправка формы".

Если для создания цели действительно не хватает обязательных данных, tool должен вернуть `needs_input`, а не:

* выдумывать данные;
* создавать неправильную цель;
* падать exception.

---

# 5. P0 — Goal lifecycle

Провести полный lifecycle test.

Для тестового counter:

1. create goal;
2. get goal;
3. update goal;
4. get goal again;
5. get goal stats;
6. delete goal;
7. verify goal no longer exists.

Проверить именно реальные endpoint semantics Yandex Metrica:

* `POST /management/v1/counter/{id}/goals`
* `GET /management/v1/counter/{id}/goal/{goalId}`
* `PUT /management/v1/counter/{id}/goal/{goalId}`
* `DELETE /management/v1/counter/{id}/goal/{goalId}`

Особое внимание уделить payload update:

не отправлять read-only / immutable поля.

---

# 6. P0 — Reports API

Провести аудит всех report helpers.

Особенно проверить:

* dimensions;
* metrics;
* filters;
* sort;
* date ranges;
* pagination;
* totals;
* sampled;
* data_lag;
* async reports.

Проверить, что model соответствует реальному формату:

```text
data: list[ReportRow]
```

а не старой ошибочной структуре `data.rows`.

Отдельно проверить различия между:

* session metrics `ym:s:*`
* pageview metrics `ym:pv:*`

Например top pages должен использовать корректные pageview dimensions/metrics.

---

# 7. P0 — Security audit

Проверить отсутствие секретов во всех:

* logs;
* exceptions;
* ToolResult;
* error messages;
* debug output;
* acceptance output;
* README examples;
* tests;
* fixtures.

Особенно:

* access token;
* refresh token;
* OAuth authorization code;
* client secret;
* PKCE verifier.

Добавить regression tests, если какие-либо потенциальные утечки найдены.

Проверить `.gitignore` и отсутствие реальных credentials в репозитории.

---

# 8. P0 — OAuth audit

Проверить:

* Authorization Code flow;
* PKCE S256;
* state generation;
* state verification;
* scope verification;
* token expiration;
* refresh;
* concurrent refresh;
* invalid refresh token;
* missing scope;
* wrong OAuth state.

Проверить, что:

`metrika:read`

достаточно для read-only operations,

а write operations корректно требуют:

`metrika:write`.

Не скрывать permission errors как generic network errors.

---

# 9. P1 — Rate limiting / retry

Проверить существующий transport layer.

Официальные ограничения Metrica должны быть отражены корректно.

Проверить:

* 30 req/sec/IP;
* max 3 parallel requests/user;
* daily quota;
* Reports API quota;
* HTTP 420;
* HTTP 429;
* Retry-After;
* exponential backoff;
* jitter;
* destructive request retry policy.

Особенно важно:

**не должно быть бесконтрольного retry для destructive operations.**

Например DELETE goal не должен автоматически повторяться после неоднозначного server/network failure, если это может привести к некорректной семантике операции.

Добавить tests для boundary cases.

---

# 10. P1 — Counter resolution

Проверить `resolve_counter`.

Сценарии:

### Exact ID

Если пользователь указал:

```text
counter_id=12345678
```

должен использоваться именно этот counter.

### Unique name

Если имя однозначно соответствует одному counter — выбрать его.

### Ambiguous name

Если найдено несколько counters:

вернуть структурированную ошибку с кандидатами.

Не выбирать случайный counter.

### Not found

Вернуть понятную ошибку.

### Cache

Проверить, что cache не смешивает данные разных:

```text
connection_id
```

---

# 11. P1 — Idempotency

Проверить повторные операции.

Особенно:

`create_goal`

При повторном вызове с теми же идентификационными параметрами не должно происходить неконтролируемого создания дублей, если текущая архитектура заявляет idempotency.

Проверить также:

* cache;
* single-flight;
* concurrent calls.

Написать regression tests для race conditions, где это разумно.

---

# 12. P1 — Data correctness

Проверить, что AI получает правильные значения, а не просто HTTP 200.

Минимум проверить:

### Traffic

* visits;
* users.

### Traffic by day

* date;
* visits;
* users.

### Sources

* source dimension;
* visits/users или заявленные metrics.

### Top pages

* URL;
* pageviews.

### Goal stats

* reaches;
* conversion rate.

### Compare periods

* current period;
* previous period;
* absolute difference;
* relative difference, если заявлена.

Проверить корректность преобразования API response → ToolResult.

---

# 13. P1 — Date handling

Провести отдельный audit дат.

Проверить:

* ISO date;
* `YYYY-MM-DD`;
* start <= end;
* inclusive/exclusive semantics;
* timezone;
* "last 7 days";
* "previous 7 days";
* same-day period;
* invalid date.

Не допускать silent timezone bugs.

Если библиотека использует timezone counter — это должно быть явно документировано.

---

# 14. P1 — Real API acceptance

Расширить существующий:

`scripts/task03_acceptance.py`

или создать:

`scripts/task04_acceptance.py`

Acceptance должен использовать реальный Yandex Metrica API.

Не заменять реальные проверки mocks.

Минимальный flow:

```text
OAuth
  ↓
list counters
  ↓
resolve counter
  ↓
list goals
  ↓
create test goal
  ↓
get goal
  ↓
update goal
  ↓
get goal
  ↓
goal stats
  ↓
traffic
  ↓
traffic by day
  ↓
sources
  ↓
top pages
  ↓
compare periods
  ↓
delete test goal
  ↓
verify deletion
```

Если текущий Task03 уже делает большую часть этого — переиспользовать его и добавить только недостающие проверки.

Не создавать ненужные постоянные ресурсы.

Обязателен cleanup через `try/finally`.

---

# 15. AI Tool acceptance tests

Добавить отдельный уровень tests поверх unit tests.

Проверить не только HTTP layer, а:

```text
Tool input
    ↓
Tool validation
    ↓
Tool execution
    ↓
Service
    ↓
Client
    ↓
normalized ToolResult
```

Проверить как минимум:

1. successful traffic;
2. successful sources;
3. successful pages;
4. successful goal stats;
5. ambiguous counter;
6. missing goal;
7. `needs_input` for insufficient goal creation data;
8. invalid dates;
9. authorization error;
10. API error normalization.

---

# 16. Public API audit

Проверить:

* `__init__.py`;
* exports;
* imports;
* naming;
* backwards compatibility;
* no accidental internal classes exposed as public API.

Проверить clean installation:

```bash
pip install .
```

или используемый проектом package manager.

После установки:

```python
import yandex_metrika_agent
```

должен работать.

Проверить, что AI tools можно импортировать из документированного public API.

---

# 17. Documentation audit

README должен соответствовать фактическому проекту.

Убрать любые claims о функциях, которых нет.

README должен содержать минимум:

* что это;
* архитектура;
* authentication;
* required OAuth scopes;
* counters;
* goals;
* reports;
* AI tools;
* examples;
* error handling;
* rate limits;
* testing;
* real API acceptance;
* known limitations.

Отдельно явно указать:

* static metric aliases vs live metric catalog;
* MCP пока не входит в scope, если это так;
* real API credentials required для acceptance test.

---

# 18. Quality gates

Перед завершением Task04 выполнить:

```bash
python -m compileall .
pytest -q
ruff check .
mypy ...
```

Использовать реальные команды проекта, указанные в `pyproject.toml`.

Если есть formatter:

```bash
ruff format --check .
```

Также проверить:

```bash
git status
```

Не должно быть случайных:

* credentials;
* `.env`;
* token files;
* generated secrets;
* debug artifacts;
* temporary acceptance files.

---

# 19. Regression requirement

Не удалять существующие tests для того, чтобы получить green build.

После исправлений:

**все существующие tests должны продолжать проходить.**

Если test изменён из-за реального изменения API contract — в report объяснить почему.

---

# 20. Final verdict

Создать:

```text
Tasks/Task04_REPORT.md
```

Report должен содержать:

## 1. Executive Summary

Кратко:

* что проверено;
* что исправлено;
* итоговый статус.

## 2. Audit Matrix

Таблица:

| Area               | Status    | Evidence |
| ------------------ | --------- | -------- |
| AI Tools           | PASS/FAIL | ...      |
| Goals CRUD         | PASS/FAIL | ...      |
| Reports            | PASS/FAIL | ...      |
| OAuth              | PASS/FAIL | ...      |
| Security           | PASS/FAIL | ...      |
| Rate limiting      | PASS/FAIL | ...      |
| Counter resolution | PASS/FAIL | ...      |
| Idempotency        | PASS/FAIL | ...      |
| Real API           | PASS/FAIL | ...      |
| Documentation      | PASS/FAIL | ...      |
| Tests              | PASS/FAIL | ...      |

## 3. Bugs Found

Для каждого:

* severity;
* file;
* problem;
* root cause;
* fix;
* regression test.

## 4. Real API Evidence

Указать:

* количество успешных acceptance steps;
* какие реальные endpoints были вызваны;
* какие реальные resources были созданы;
* cleanup result;
* execution time.

Никогда не публиковать реальные credentials или tokens.

## 5. Test Results

Указать реальные результаты:

```text
pytest: ...
ruff: ...
mypy: ...
compileall: ...
acceptance: ...
```

Не писать `PASS`, если команда фактически не запускалась.

## 6. Remaining Limitations

Явно перечислить всё, что осталось.

Если нет P0/P1:

```text
No known P0/P1 issues.
```

## 7. Final Verdict

Использовать строго один из:

```text
READY
```

или

```text
NOT READY
```

### READY допускается только если:

* нет известных P0;
* нет известных P1;
* все tests green;
* real API acceptance green;
* security audit green;
* AI Tool Layer проверен;
* documentation соответствует реализации.

Если остаётся хотя бы один P0/P1:

**NOT READY**

---

# 21. Важное правило Task04

Не маскировать проблемы.

Если реальный API показывает несовпадение — исправить код и добавить regression test.

Если исправить невозможно в рамках текущего scope — отметить:

```text
NOT READY
```

с объяснением.

Не менять acceptance test так, чтобы проблема просто перестала проверяться.

Не удалять сложные tests.

Не использовать mocks вместо real API там, где требуется acceptance.

---

# 22. Ожидаемый результат

В конце Task04 должны существовать:

```text
Tasks/Task04_REPORT.md
scripts/task04_acceptance.py
```

если отдельный acceptance script действительно нужен.

Все исправления должны быть committed в рабочее дерево проекта.

Финальный результат должен позволять ответить на вопрос:

> "Можно ли сейчас подключить этот проект к KodaCode AI agent как Yandex Metrica integration и безопасно выполнять через него counters/goals/analytics operations?"

Ответ должен быть объективно подтверждён кодом, tests и acceptance results.

Если все критерии выполнены:

# READY FOR AI-AGENT INTEGRATION
