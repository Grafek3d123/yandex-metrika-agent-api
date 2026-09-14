Задача

Провести второй этап разработки интеграции API Яндекс Метрики из текущего репозитория.

Цель этапа — довести существующую реализацию до состояния, в котором она действительно может безопасно использоваться AI-агентом KodaCode.

НЕ переписывать проект с нуля.

Сохранить существующую архитектуру, если она корректна, и исправить только реальные проблемы.

Главный принцип:

«Актуальная официальная документация Яндекс Метрики является единственным источником истины по HTTP API, endpoint'ам, параметрам, моделям ответа, типам целей, метрикам и квотам.»

---

1. Обязательная проверка документации

Перед изменением кода повторно проверить актуальную официальную документацию:

- https://yandex.com/dev/metrika/ru/
- https://yandex.com/dev/metrika/ru/intro/authorization
- https://yandex.com/dev/metrika/ru/intro/quick-start
- https://yandex.com/dev/metrika/ru/management/
- https://yandex.com/dev/metrika/ru/management/openapi/goal/
- https://yandex.com/dev/metrika/ru/stat/
- https://yandex.com/dev/metrika/ru/stat/openapi/
- https://yandex.com/dev/metrika/ru/stat/segmentation
- https://yandex.com/dev/metrika/ru/intro/quotas

Не доверять существующим комментариям в коде, если они расходятся с актуальной документацией.

---

2. P0 — исправить Reports API response model

Текущая модель Reports API неверна.

Текущий код ожидает:

{
  "data": {
    "rows": [...]
  }
}

Но актуальный "/stat/v1/data" возвращает:

{
  "data": [
    {
      "dimensions": [...],
      "metrics": [...]
    }
  ],
  "total_rows": 0,
  "total_rows_rounded": false,
  "sampled": false,
  "sample_share": 1,
  "totals": [...],
  "metric_names": [...],
  "dimension_names": [...]
}

Исправить:

models.py
reports.py

Модель должна соответствовать реальному API.

Например концептуально:

class Report:
    data: list[ReportRow]
    totals: list[float | int | str | None]
    total_rows: int | None
    ...

Не создавать искусственный "ReportData.rows", если такого объекта нет в API.

После изменения:

get_traffic()
get_traffic_by_day()
get_sources()
get_top_pages()
get_goal_stats()
compare_periods()

должны работать с реальным форматом ответа.

Добавить unit tests с реальным JSON fixture, основанным на официальной схеме API.

---

3. P0 — исправить Goal update endpoint

Текущая реализация ошибочно отправляет:

PUT /management/v1/counter/{counterId}/goals

Правильный endpoint:

PUT /management/v1/counter/{counterId}/goal/{goalId}

Изменить:

goals.py

Метод:

GoalService.update()

должен использовать "goal.id" в URL.

Добавить тест, проверяющий точный HTTP method/path/body.

---

4. P0 — исправить Goal delete endpoint

Текущая реализация использует:

DELETE /management/v1/counter/{counterId}/goals?goalId={goalId}

Правильный endpoint:

DELETE /management/v1/counter/{counterId}/goal/{goalId}

Исправить "GoalService.delete()".

Добавить HTTP contract test.

---

5. P0 — исправить получение конкретной цели

Сейчас:

GoalService.get()

получает весь список целей и ищет "goal_id".

Использовать официальный endpoint:

GET /management/v1/counter/{counterId}/goal/{goalId}

Не делать полный "GET /goals", если нужен один goal.

Добавить тест.

---

6. P0 — исправить goal statistics

Текущая реализация использует:

metrics=["visits", "users"],
include=[
    goal_reaches(goal_id),
    goal_conversion(goal_id)
]

Это неправильно.

Метрики цели должны передаваться непосредственно в "metrics".

Использовать:

metrics=[
    "visits",
    "users",
    goal_reaches(goal_id),
    goal_conversion(goal_id),
]

Проверить актуальные идентификаторы:

ym:s:goal<id>reaches
ym:s:goal<id>conversionRate

и при необходимости добавить:

goal<id>users
goal<id>visits
goal<id>reachesPerUser
userConversionRate

если они нужны AI-friendly API.

После исправления "get_goal_stats()" должен возвращать реальные значения:

{
  "goal_id": 123,
  "visits": 1000,
  "users": 800,
  "goal_reaches": 42,
  "goal_conversion_rate": 4.2
}

Добавить tests.

---

7. P0 — создать настоящий AI Tool Layer

Сейчас есть сервисы, но нет полноценного слоя инструментов для AI-агента.

Создать:

yandex_metrika_agent/ai_tools/

Минимально:

counters.py
goals.py
analytics.py

или эквивалентную структуру.

Реализовать tools:

metrika_list_counters
metrika_get_counter

metrika_list_goals
metrika_get_goal
metrika_create_goal
metrika_update_goal
metrika_delete_goal

metrika_get_report
metrika_get_traffic
metrika_get_traffic_by_day
metrika_get_sources
metrika_get_top_pages
metrika_get_goal_stats
metrika_compare_periods

Каждый tool должен иметь строгую JSON schema.

НЕ создавать универсальный tool:

metrika_http_request(method, url, body)

AI не должен самостоятельно конструировать произвольные HTTP-запросы.

---

8. AI Tool behavior

Tools должны принимать бизнесовые параметры.

Хорошо:

{
  "counter": "example.com",
  "date_from": "2026-09-01",
  "date_to": "2026-09-14"
}

Плохо:

{
  "counterId": 123,
  "metrics": "ym:s:visits,ym:s:users",
  "dimensions": "ym:s:trafficSource"
}

Низкоуровневые параметры должны строиться внутри service layer.

---

9. Counter resolution

Проверить актуальную модель Management API.

Исправить нормализацию:

owner_login
permission
time_zone_name
time_zone_offset
site2

и другие поля согласно текущей схеме.

Не придумывать:

owner.login
timezone
counter_type

если их нет в текущем response contract.

"CounterService.resolve()" должен уметь:

example.com
www.example.com
https://example.com
название счётчика
counter ID

и возвращать:

0 matches → NotFound
1 match → автоматически использовать
>1 → вернуть кандидатов для уточнения

---

10. Metrics catalog

Добавить реальную интеграцию со справочником доступных metrics/dimensions, если соответствующий официальный endpoint доступен для текущего Reports API.

Нельзя утверждать, что "MetricDirectory" является live directory, если приложение фактически не получает данные из API.

Разделить:

static aliases

и:

live API catalog

Статические aliases оставить как удобный fallback.

---

11. Reports API parameters

Проверить "ReportCommand".

Особенно:

ids
metrics
dimensions
date1
date2
filters
sort
limit
offset
lang
timezone
include_undefined
accuracy
proposed_accuracy
preset

Не хранить в модели параметры, которые относятся к другому endpoint, если они не поддерживаются текущим "/stat/v1/data".

"ReportCommand.to_params()" должен генерировать параметры именно текущего API.

Не использовать "counterId", если конкретный endpoint ожидает "id"/"ids".

---

12. Filters DSL

Сохранить текущий DSL, потому что архитектурно он правильный.

Но добавить tests минимум для:

equals
not_equals
contains
not_contains
starts_with
greater
greater_or_equal
less
less_or_equal
in
not_in
is_null
is_not_null
escaping
AND
OR
NOT
parentheses

Проверить каждый оператор по актуальной документации.

Особенно проверить:

starts_with
not_in
is_null
is_not_null

и не реализовывать оператор через другую конструкцию без проверки официального синтаксиса.

---

13. P0 — настоящий rate limiter

Текущий retry после 420/429 не является полноценным rate limiter.

Реализовать:

max concurrent requests = 3 per user connection

и rate limiting с учетом актуальных квот Яндекс.

Минимально учитывать:

30 requests/sec per IP
3 parallel requests per user
5000 requests/day per user
200 Reports API requests / 5 minutes per user

Если невозможно надёжно контролировать IP-level quota внутри приложения, документировать это и реализовать user-level protection.

Разделить логически:

management read
management write
report read

Не допускать массового fan-out от AI.

---

14. Retry

Проверить retry behavior.

Retry допустим для:

network errors
timeouts
5xx
420/429

Но не делать автоматический retry для:

400
401
403
404
422

Обязательно уважать:

Retry-After

Не ретраить destructive requests вслепую, если повтор может привести к повторной операции.

Особенно осторожно:

POST create goal
DELETE goal

---

15. Cache

Сохранить GET cache/deduplication, но проверить:

Authorization/user/connection

должны входить в cache isolation.

Нельзя допустить:

user A
→ cache
→ user B получает данные user A

Cache key должен быть привязан как минимум к connection/user identity + request parameters.

Добавить regression test.

---

16. OAuth

Проверить существующий OAuth implementation на реальном актуальном Yandex OAuth contract.

Проверить:

state
PKCE
authorization code
redirect_uri
code exchange
refresh token
token expiry
revoked token
wrong account
wrong scope
disconnect

Поддерживать минимум:

metrika:read
metrika:write

Не запрашивать write, если он не нужен.

Не логировать:

access_token
refresh_token
client_secret
authorization code
code_verifier

Проверить, что token storage действительно изолирован между connection_id.

---

17. Token storage

Сохранить AES-GCM storage.

Добавить tests:

save
load
delete
corrupted file
wrong key
multiple connections
expired token
refresh
concurrent refresh

Проверить права файлов на Unix.

Не помещать секреты в error details.

---

18. Goal planner

Сохранить текущую идею "GoalPlanner".

Обязательно покрыть:

"цель на отправку формы"
→ needs_input

Не разрешать агенту автоматически придумывать:

event = submit

если сайт не сообщил реальное событие.

Проверить:

URL goal
action goal
phone
email
file
messenger
search
social
depth
duration
payment_system
step
chat

Отдельно реализовать "chat", если он заявлен как поддерживаемый high-level goal type.

---

19. Goal idempotency

Сохранить "ensure_goal()".

Но пересмотреть алгоритм duplicate detection.

Нельзя считать две цели одинаковыми только по имени, если у них разные:

type
conditions
parameters

Например:

"Заявка"
action submit_form

и:

"Заявка"
url /thank-you

не являются одной целью.

Использовать структурный signature как основной критерий.

Совпадение имени использовать только как сигнал для дополнительного предупреждения AI.

---

20. Tests

Создать настоящий каталог:

tests/

Минимальный набор:

test_auth.py
test_tokens.py
test_counters.py
test_goals.py
test_goal_planner.py
test_metrics.py
test_filters.py
test_reports.py
test_transport.py
test_ai_tools.py

HTTP tests делать через "respx".

Не использовать реальные OAuth credentials.

---

21. Fixtures

Создать fixtures реальных форматов API:

fixtures/
    counters.json
    counter.json
    goals.json
    goal.json
    report.json
    report_empty.json
    report_sampled.json
    api_error_400.json
    api_error_401.json
    api_error_403.json
    api_error_420.json

Структуры fixtures должны соответствовать актуальной официальной документации.

---

22. Integration smoke test

Добавить отдельный smoke test, который запускается только при наличии:

METRIKA_OAUTH_TOKEN
METRIKA_COUNTER_ID

Он должен проверить:

GET counters
GET counter
GET goals
GET report

Для write операций не менять production-счётчик без явного флага.

Для goal CRUD использовать отдельный тестовый счётчик.

---

23. CLI

Не считать CLI главным AI interface.

CLI оставить как debugging/dev interface.

Но README должен соответствовать реальному CLI.

Сейчас README описывает команды, которых фактически нет.

Исправить README либо реализовать заявленные команды.

Предпочтительно для этого этапа:

README = фактическое состояние проекта

Не документировать несуществующий функционал.

---

24. Public API

Обновить:

__init__.py

чтобы AI/service layer имел чистые публичные импорты.

Например:

from yandex_metrika_agent import (
    MetrikaClient,
    CounterService,
    GoalService,
    ReportService,
    GoalPlanner,
)

Не заставлять интегратор импортировать внутренние private helpers.

---

25. Quality gates

После реализации выполнить:

python -m compileall yandex_metrika_agent
pytest -q
ruff check .
mypy yandex_metrika_agent

Если "ruff" или "mypy" отсутствуют, установить dev dependencies и повторить.

Не считать задачу завершённой, если:

pytest != 0
ruff != 0
mypy != 0

за исключением документированных false positives, которые нельзя просто игнорировать без объяснения.

---

26. Final acceptance criteria

Интеграция считается READY только если одновременно выполнены все пункты:

OAuth

- пользователь может авторизовать подключение;
- токен безопасно хранится;
- токен не появляется в логах/errors;
- refresh/re-auth работает.

Counters

- список счётчиков работает;
- один счётчик можно получить по ID;
- счётчик можно определить по домену;
- несколько совпадений корректно возвращаются агенту для выбора.

Goals

- list работает;
- get работает через официальный endpoint;
- create работает;
- update работает через "/goal/{goalId}";
- delete работает через "/goal/{goalId}";
- duplicate detection работает;
- минимум основные типы целей протестированы.

Reports

- raw report работает;
- response parsing соответствует реальному JSON;
- traffic работает;
- sources работает;
- pages работает;
- goal statistics работает;
- comparison работает;
- filters работают.

AI

AI tool layer существует и предоставляет:

metrika_list_counters
metrika_get_counter
metrika_list_goals
metrika_get_goal
metrika_create_goal
metrika_update_goal
metrika_delete_goal
metrika_get_report
metrika_get_traffic
metrika_get_sources
metrika_get_top_pages
metrika_get_goal_stats
metrika_compare_periods

AI не должен самостоятельно формировать raw HTTP requests.

Reliability

- rate limiting;
- concurrency limit;
- retry;
- Retry-After;
- cache isolation;
- request deduplication.

Tests

Есть реальные automated tests.

Минимум:

pytest = PASS
ruff = PASS
mypy = PASS

Documentation

README соответствует фактическому состоянию проекта.

Создать:

docs/yandex-metrika-ai.md

с описанием именно AI tools и сценариев их использования.

---

27. После завершения

Не просто написать "готово".

Создать файл:

Tasks/Task02_REPORT.md

и указать:

1. Что было найдено
2. Что исправлено
3. Какие API endpoints проверены
4. Какие модели приведены к официальной схеме
5. Какие AI tools добавлены
6. Какие тесты добавлены
7. Результат pytest
8. Результат ruff
9. Результат mypy
10. Что осталось намеренно не реализованным
11. Какие ограничения API Яндекс остались

Особенно отдельно перечислить все изменения, которые были сделаны на основании актуальной официальной документации Яндекс.

Не считать работу завершённой до формирования этого отчёта.