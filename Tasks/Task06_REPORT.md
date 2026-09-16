# Task06 — Финальный end-to-end аудит (REPORT)

## 0. Область и метод

AUDIT ONLY. Код не изменялся. Проверена **существующая** реализация против
главной задачи:

> «KodaCode AI Agent должен через Yandex Metrica integration уметь безопасно
> работать со счётчиками, целями и статистикой пользователя».

Методы проверки:
- чтение исходников (`ai_tools/*`, `counters.py`, `goals.py`, `reports.py`,
  `metrics.py`, `transport.py`, `oauth.py`, `__init__.py`, README, docs);
- статические гейты (pytest / ruff / mypy strict / compileall);
- **живой e2e-прогон** всех пользовательских сценариев через `MetrikaTools.call`
  (временные счётчик+цель, полный cleanup — `counters_left: 0`);
- офлайн-репродукция найденного бага (без HTTP).

---

## 1. AI Agent → AI Tools (через `ToolRegistry.call`)

Прогон против реального API (`connection_id="task03"`), счётчик создавался
временно. 11 сценариев из 12 проходят, 1 падает.

| Сценарий | Tool | Результат |
| --- | --- | --- |
| определить/выбрать counter | `metrika_get_counter` (по домену) | ✅ ok |
| ambiguous counter → уточнить | `metrika_get_counter` (2 счётчика на домене) | ✅ `needs_input`, 2 кандидата |
| получить traffic | `metrika_get_traffic` | ✅ ok (visits/users/pageviews/bounce_rate/…) |
| traffic by day | `metrika_get_traffic_by_day` | ✅ ok (был FAIL B1 — исправлен, §10) |
| sources | `metrika_get_sources` | ✅ ok |
| top pages | `metrika_get_top_pages` | ✅ ok |
| goal stats | `metrika_get_goal_stats` | ✅ ok |
| сравнить периоды | `metrika_compare_periods` | ✅ ok (delta/delta_percent) |
| список goals | `metrika_list_goals` | ✅ ok |
| конкретная goal | `metrika_get_goal` | ✅ ok (+`describe()`) |
| создать goal | `metrika_create_goal` | ✅ ok (идемпотентно: 2-й вызов `created:false`) |
| создать goal (нет данных) | `metrika_create_goal` | ✅ `needs_input` + вопрос, значение не выдумано |
| изменить goal | `metrika_update_goal` (name, price) | ✅ ok |
| удалить goal (confirmation flow) | `metrika_delete_goal` | ✅ см. §2 |
| произвольный отчёт | `metrika_get_report` | ✅ ok |

---

## 2. Safety

Все проверки — против живого API (созданная цель, реальные DELETE-ловушки).

| Проверка | Результат |
| --- | --- |
| read-only tools без confirmation | ✅ выполняются сразу |
| create/update (MUTATING) штатно | ✅ без гейта, работают |
| delete_goal без confirmation | ✅ `confirmation_required`, **DELETE не отправлен**, цель цела |
| `confirmed=true` от AI | ✅ не обход — снова `confirmation_required` |
| подделанный токен | ✅ `error (unknown)`, цель цела |
| токен чужого ресурса (другой goal_id) | ✅ `confirmation_required`, цель цела |
| повторный (reuse) токен | ✅ `error (reused)`, второй DELETE не выполнен |
| валидный confirmation | ✅ ровно один DELETE, цель удалена |
| `approve_confirmation` не инструмент | ✅ нет в `specs()`, недостижим из Tool Layer |
| нет иного destructive-пути | ✅ см. ниже |

**Иной destructive-путь.** `delete_json` вызывается ровно из двух мест:
`GoalService.delete` (единственный DESTRUCTIVE-инструмент `metrika_delete_goal`,
закрыт guard'ом) и `CounterService.delete` (**не выставлен** как AI-инструмент,
в `specs()` отсутствует). Через AI Tool Layer удалить что-либо, кроме цели и
только через confirmation, невозможно. Прочитать/вызвать `CounterService.delete`
из Tool Layer нельзя — контекст инструментов не даёт доступа к сервису удаления
счётчиков.

---

## 3. Реальный API (acceptance)

- Существующий `scripts/task05_acceptance.py` — ранее 8/8 ok.
- Задача выполнена новым сквозным e2e-прогоном (12+ сценариев, §1–§2):
  **27/28 ok**, cleanup `counters_left: 0`, **утечек секретов нет**
  (в ответах инструментов нет `ym:s:`/`ym:pv:`/`oauth.yandex`/`Authorization`/
  `access_token`/значения токена).
- Постоянные данные не созданы: временные счётчик/цель/dup-счётчик удалены.

---

## 4. API contract (соответствие официальному API)

| Область | Эндпоинт/параметр | Статус |
| --- | --- | --- |
| counters | `GET /management/v1/counters`, `GET/POST /counter`, `DELETE /counter/{id}` | ✅ совпадает |
| goals CRUD | `GET/POST .../goals`, `GET/PUT/DELETE .../goal/{id}` | ✅ совпадает (Task04: `is_favorite` убран) |
| Reports API | `GET /stat/v1/data`, лимиты 20 метрик / 10 измерений, единый префикс | ✅ валидируется |
| goal metrics | `ym:s:goal<id>{reaches,conversionRate,…}` | ✅ принимается API (прогон ok) |
| OAuth | `oauth.yandex.ru`, PKCE S256, device flow, скоупы `metrika:read/write` | ✅ совпадает |
| ошибки 401/403/404 | `AuthError`/`ScopeError`/`NotFoundError` (`error_from_response`) | ✅ |
| 420/429 rate-limit | повтор с `Retry-After` + `RateLimitedError` | ✅ (`should_retry`, `backoff_delay`) |
| rate limiting (квоты) | 30/сек·IP, 3 параллельно, 200/5мин отчёты, 5000/сутки | ✅ `RateLimiter` |

Примечание: goal-метрики строятся со строчным суффиксом
(`…reaches`/`…conversionRate`); официальная документация Яндекса пишет суффикс
с заглавной (`…Reaches`/…`ConversionRate`). Живой API строчный вариант
**принимает** (прогон вернул `ok`), строгого расхождения нет — см. §D.

---

## 5. Agent usability

Единый конверт соблюдён для всех четырёх исходов (проверено живьём):
- успех → `{"status":"ok","data":…}`;
- не хватает данных → `{"status":"needs_input","question":…,"missing":[…]}`
  (пример: вопрос про JS-событие);
- разрушающая операция → `{"status":"confirmation_required","confirmation_id":…,
  "question":…}`;
- ошибка API → `{"status":"error","error":{…}}` (пример: несуществующая цель →
  `ApiError`, «No object with specified ID»).

Агенту **не** требуются `ym:s:*`, `dimensions`, `filters`, URL эндпоинтов или
OAuth-детали: вход — человекочитаемые `counter` (домен/id), `date_from/to`,
метрики `visits`/`bounce_rate`, измерения `date`/`traffic_source`/`page`.
Проверка на утечку внутренних идентификаторов в ответы — чисто (§3).

---

## 6. Публичный API и документация

- **Public API** (`__init__.__all__`): `MetrikaClient`, `MetrikaTools`,
  `CounterService`, `GoalService`, `ReportService`, `GoalPlanner`,
  `ConfirmationPolicy`, `ToolSafety`, модели (`Goal`, `GoalType`, `GOAL_TYPES`,
  `GoalPlan`, `PlanStatus`, `TokenRecord`) и все ошибки `AgentError`-иерархии.
  Импорты разрешаются, гейты зелёные.
- README: «14 инструментов» — совпадает с `specs()`; destructive-safety-раздел
  соответствует коду Task05; CLI `ymetrika plan` работает (exit 0, валидный
  JSON). Фиктивный `GET /stat/v1/metrics` из docs удалён (Task04) — остатков нет.
- **Расхождение (устранено §10):** README/docs заявляли `metrika_get_traffic_by_day`
  («динамика по дням») как рабочий инструмент, но он фактически не работал (§B) —
  исправлено. Цифра «150+ тестов» — не ложная (фактически 179 после фикса).

---

## 7. Quality gates (финальный прогон)

```text
pytest          175 passed
ruff check      All checks passed!   (yandex_metrika_agent, tests, scripts)
mypy            Success: no issues found in 36 source files   (strict)
compileall      OK
live e2e        27/28 ok (единственный FAIL — §B), cleanup counters_left:0
```

> Слепая зона тестов: на `get_traffic_by_day` и на сортировку `metrika_get_report`
> по измерению автотестов нет (`grep` по `tests/` — 0 совпадений), поэтому баг
> не пойман pytest'ом и выплыл только на живом прогоне.

> **ПОСЛЕ ФИКСА B1 (§10):** pytest **179 passed** (добавлены 4 regression-теста,
> закрывающие слепую зону), ruff/mypy strict/compileall — зелёные, live e2e
> sort-сценариев 5/5 ok.

---

## 8. Классификация находок

### A. PASS — реально работает
- Выбор/разрешение счётчика (домен/id, ambiguous→`needs_input` с кандидатами).
- 12 из 12 бизнес-сценариев через `ToolRegistry` против живого API (после фикса B1).
- Confirmation guard: полные сценарии блок/подделка/чужой/просроченный/reuse/
  однократное выполнение; нет иного destructive-пути из Tool Layer.
- Идемпотентность `create_goal`; `needs_input` без выдумывания значений.
- API contract (counters/goals/Reports/OAuth/ошибки/квоты).
- Единый конверт ответов; отсутствие утечек секретов и внутренних имён.
- Публичный API и его импорты; README/destructive-раздел соответствуют коду.
- Все статические гейты зелёные.

### B. FAIL — блокирует готовность
**B1. ✅ FIXED (§10). `metrika_get_traffic_by_day` всегда падает; сортировка `metrika_get_report`
по любому измерению тоже.**
- Корень: `ReportService.get_traffic_by_day` жёстко передаёт `sort_by=["date"]`
  (`reports.py:194`), а `ReportService._sort_token` (`reports.py:406–414`)
  резолвит токен **только как метрику** (`directory.metric(...,
  allow_unknown=False)`). `date` — это **измерение** (`DIMENSION_ALIASES`,
  `metrics.py:60`), его нет в `METRIC_ALIASES`.
- Следствие: `ValidationError: Неизвестное метрики: 'date'` бросается на
  валидации **до** HTTP-запроса → метод никогда не работает (офлайн-репро
  подтверждён; живой прогон — `status:"error"`).
- Тот же корень ломает универсальный `metrika_get_report`, если агент просит
  сортировку по измерению (напр. `sort:["date"]` при `dimensions:["date"]`) —
  типовой сценарий «по дням по возрастанию».
- Почему блокер: «traffic by day» — один из 12 обязательных сценариев ТЗ и
  заявлен в README/docs рабочим; сейчас он гарантированно недоступен агенту.
- Минимальный фикс (требует отдельного согласования, в коде НЕ выполнен): в
  `_sort_token` резолвить токен как метрику **или** измерение (метрика →
  fallback на измерение), + добавить тесты на `get_traffic_by_day` и
  `get_report` с сортировкой по измерению.

### C. LIMITATION — ограничение, не блокер
- Подтверждения in-memory per-process: после рестарта хоста pending-операции
  сбрасываются (безопасно: выполнение без подтверждения по-прежнему невозможно).
- `RateLimiter` защищает только свой процесс; IP-квоту 30/сек между процессами
  учесть из приложения нельзя (задокументировано в коде).
- Goal-метрики: строчный суффикс вместо заглавного в docs Яндекса — API
  принимает, но для строгого соответствия стоит унифицировать регистр.
- `get_top_pages` группируется только по `ym:pv:URL` (измерение «входная
  страница» в Reports API отсутствует) — осознанное ограничение.
- CLI `plan` выводит кириллицу «кракозябрами» в консоли Windows (cp1251) —
  косметика терминала, JSON корректен.

### D. NICE TO HAVE — позже
- Освежить в README цифру «150+ тестов» → «179 тестов».
- ~~Добавить unit-тесты на `get_traffic_by_day` и `get_report` с сортировкой по
  измерению~~ — **сделано** в фиксе B1 (§10, 4 теста).
- Унифицировать регистр суффиксов goal-метрик с официальной документацией.

---

## 9. Итог

```text
READY
```

Блокер B1 устранён точечным фиксом (см. §10): `metrika_get_traffic_by_day` и
сортировка `metrika_get_report` по измерению работают — подтверждено офлайн-
тестами (4 новых) и живым прогоном (5/5). Все 12 пользовательских сценариев
проходят через `ToolRegistry`, safety выдержан полностью, API contract
соответствует документации, quality gates зелёные (pytest 179, ruff, mypy
strict 36 файлов, compileall).

Постоянных данных не осталось (cleanup `counters_left: 0`), временный
live-скрипт удалён. Новые функции/MCP/AI-tools не добавлялись — только
багфик B1.

---

## 10. B1 — точечный фикс (после аудита)

### Причина

`ReportService._sort_token()` резолвил токен сортировки **только как метрику**
(`directory.metric(..., allow_unknown=not validate)`). Измерение `date`
(и любые другие dimension) отсутствует в `METRIC_ALIASES`, поэтому
`MetricDirectory._resolve` бросал `ValidationError: Неизвестное метрики:
'date'` **до** HTTP-запроса. `get_traffic_by_day` жёстко передаёт
`sort_by=["date"]` → метод падал всегда; тот же корень ломал сортировку
`get_report` по любому измерению.

### Фикс (`yandex_metrika_agent/reports.py`, `_sort_token`)

Токен сортировки сначала пробуют как метрику, при `ValidationError` — как
измерение; знак (`+`/`-`) сохраняется:

```python
try:
    resolved = self.directory.metric(name, allow_unknown=not validate)
except ValidationError:
    # Метрики нет — пробуем измерение (``date`` и т. п.). При неизвестном
    # имени dimension тоже бросит ValidationError: валидация не слабеет.
    resolved = self.directory.dimension(name, allow_unknown=not validate)
```

- сортировка по метрикам сохранена (`-visits` → `-ym:s:visits`);
- сортировка по измерениям заработала (`date` → `ym:s:date`);
- **валидация не ослаблена**: неизвестный токен не резолвится ни метрикой, ни
  измерением → `ValidationError` пробрасывается (в строгом режиме
  `allow_unknown=not validate`);
- объём правки — один метод; новых инструментов/MCP/API не добавлено.

### Regression-тесты (`tests/test_reports.py`, +4)

| Тест | Проверяет |
| --- | --- |
| `test_get_traffic_by_day_sorts_by_date_dimension` | `sort=ym:s:date` в запросе, разбор строк |
| `test_get_report_sorts_by_dimension` | `dimensions=["date"]`+`sort=["date"]` проходит валидацию |
| `test_sort_by_metric_still_works` | `-visits` → `-ym:s:visits` (регресс метрик) |
| `test_unknown_sort_token_rejected` | неизвестный токен → `ValidationError` |

### Live e2e после фикса (реальный API, временный счётчик, cleanup)

```text
OK  get_traffic_by_day            status=ok
OK  get_report_sort_by_dimension  status=ok
OK  get_report_sort_by_metric     status=ok
OK  get_report_unknown_sort_rejected  status=error (ValidationError)
OK  cleanup_counters              counters_left=0
SUMMARY: 5/5 ok
```

Остальные основные сценарии уже подтверждены audit-прогоном Task06 (§1–§3),
safety — `scripts/task05_acceptance.py` (8/8).

### Quality gates после фикса

```text
pytest          179 passed  (175 + 4 новых regression)
ruff check      All checks passed!   (yandex_metrika_agent, tests, scripts)
mypy            Success: no issues found in 36 source files   (strict)
compileall      OK
live e2e        5/5 ok, cleanup counters_left:0
```

> Примечание: LSP/pyright показывает ложные «No parameter named "counter_id"»
> на конструкторе pydantic-модели `ReportCommand` — не связано с правкой;
> `mypy strict` (авторитет с pydantic-плагином) — без ошибок.
