# Интеграция с API Яндекс Метрики — документация результатов

Документ фиксирует состояние проекта `yandex-metrika-agent-api`: AI-ориентированный
слой поверх API Яндекс Метрики (Management API + Reports API). Описаны архитектура,
OAuth, каждый модуль, словарь метрик, DSL фильтров, поведение GoalPlanner, ошибки,
CLI и план развития.

> Главный принцип: AI-агент оперирует бизнес-понятиями — **сайт, счётчик, цель,
> визиты, посетители, страницы, источники, конверсия, период** — а не `counterId`,
> `ym:s:visits`, `dimensions`, `filters`. Технические детали скрыты внутри слоя.

---

## 1. Статус

Реализован и работает **MVP интеграционного слоя**:

- OAuth (Authorization Code + PKCE, loopback-callback; ручная и device-альтернативы);
- зашифрованное хранилище токенов (AES-GCM, на подключение);
- типизированный транспорт (retry, backoff, кэш GET, дедупликация, rate-limit);
- клиент API Метрики (заголовок `Authorization: OAuth`, разбор ошибок);
- сервис **счётчиков** с выбором счётчика по сайту/домену;
- сервис **целей**: CRUD + конструкторы 12 типов + защита от дубликатов (идемпотентность);
- **GoalPlanner** — перевод человеческого описания в намерение цели с запросом
  недостающих данных;
- сервис **отчётов**: универсальный `get_report` + AI-friendly методы;
- словарь человеческих имён метрик/измерений;
- безопасный **DSL фильтров**;
- нормализованные типизированные ошибки;
- CLI `python -m yandex_metrika_agent` (планирование цели из фразы).

Идентификаторы метрик/измерений/фильтров и эндпоинты взяты **только** из
актуальной официальной документации (см. §19). Ничего не выдумано.

---

## 2. Архитектура (слои)

```
AI Agent
   ↓  (бизнес-операции, человекочитаемые имена)
Сервисы:  CounterService · GoalService(+GoalPlanner) · ReportService
   ↓
MetrikaClient            — заголовок Authorization, разбор JSON/ошибок
   ↓
Transport                — retry, backoff, кэш, дедупликация, rate-limit
   ↓
HTTP API Яндекс Метрики  — Management v1 (/management/v1), Reports v1 (/stat/v1)

OAuth / EncryptedFileStore — авторизация и безопасное хранение токенов (сквозной слой)
```

Агент **не** получает прямой доступ к низкоуровневым HTTP-вызовам. Он вызывает
методы сервисов, которые сами переводят намерения в корректные запросы Метрики.

---

## 3. Стек и инструменты

- **Язык:** Python ≥ 3.11 (выбран за строгую типизацию моделей и зрелость для
  агентных интеграций).
- **HTTP:** `httpx` (async).
- **Модели:** `pydantic` v2 (открытые модели: неизвестные поля Метрики сохраняются).
- **Криптография:** `cryptography` (AES-GCM для токенов).
- **Тесты:** `pytest` + `pytest-asyncio` + `respx` (мок HTTP).
- **Линт/формат:** `ruff`. **Типы:** `mypy --strict`.

---

## 4. OAuth: как это работает (простыми словами)

Используется **Authorization Code flow с PKCE** и локальным **loopback-callback**.

UX для пользователя:

1. Приложение поднимает временный локальный HTTP-сервер на
   `http://localhost:8765/callback` и открывает браузер на странице Яндекса.
2. Пользователь входит в аккаунт и нажимает «Разрешить». **Пароль Яндекса в наше
   приложение не попадает никогда.**
3. Яндекс редиректит браузер на `localhost:8765/callback?code=...`. Локальный
   сервер (`LoopbackCallback`) перехватывает `code` и сам обменивает его на токен.
4. Пользователь ничего вручную не копирует.

Почему так:

- **Authorization Code** — рекомендованный Яндексом поток для приложений.
- **PKCE** защищает перехваченный `code` — он бесполезен без секретного
  verifier, который остался в приложении.
- **Loopback-callback** даёт «автоматический возврат из браузера в приложение» без
  публичного сервера.

Альтернативы на случай, когда браузер/порт недоступны (сервер, RDP, редкий
нетворк):

- **ручной ввод кода** (`verification_code_url` → вставка кода);
- **device flow** (код + ссылка, подтверждение на любом устройстве).

Реализация: `oauth.py` — `OAuthClient`, `OAuthFlow`, `LoopbackCallback`.

Два уровня доступа (`metrika:read` / `metrika:write`) задаются скоупами
OAuth-клиента в конфигурации Яндекса. Если пользователю нужен только просмотр
статистики — write-скоуп не запрашивается.

---

## 5. Хранение токенов и много пользователей

- Токены хранит `EncryptedFileStore` (`tokens.py`): **один файл на подключение**,
  содержимое шифруется **AES-GCM**, ключ — из `METRIKA_TOKEN_KEY` (hex, 32 байта).
- Токены **скрыты** из `repr`, логов, сообщений об ошибках и ответов.
  `TokenRecord.public()` отдаёт только неверительную информацию (срок, права,
  отпечаток).
- **Много пользователей/подключений** уже поддержаны через `connection_id`:
  каждое подключение — отдельный зашифрованный файл; клиент и сервисы принимают
  `connection_id`.
- Автоматическое продление: `EncryptedFileStore.get_valid()` обновляет токен по
  `refresh_token` с мьютексом на подключение (single-flight).

---

## 6. Карта модулей

| Модуль | Назначение |
| --- | --- |
| `errors.py` | Типизированные ошибки (`AgentError` и подклассы), разбор ответа в ошибку. |
| `config.py` | Настройки из env/XDG, ключ токена, пути. |
| `crypto.py` | AES-GCM шифрование, отпечатки. |
| `tokens.py` | `TokenRecord`, `EncryptedFileStore`, авто-продление. |
| `oauth.py` | `OAuthClient`, `OAuthFlow`, `LoopbackCallback` (PKCE/device/код). |
| `transport.py` | HTTP-транспорт: retry, backoff, кэш GET, дедупликация, rate-limit, async-отчёты. |
| `client.py` | `MetrikaClient`: `Authorization: OAuth`, `get_json/post_json/...`, `from_settings`. |
| `models.py` | Модели API: `Counter`, `Goal`, `MetricItem`, `Report`, `ReportCommand`, `ComparisonRow`, `DateRange`. |
| `goals.py` | `GoalService` (CRUD + идемпотентность), конструкторы 14 типов целей. |
| `metrics.py` | Словарь «человек → `ym:s:...`» и обратно, `MetricDirectory`, метрики целей. |
| `filters.py` | DSL фильтров: `Filter`, `Operator`, `render_filters`, `as_filters`. |
| `counters.py` | `CounterService`: список/чтение/выбор счётчика по сайту, `MetrikaCounter`. |
| `reports.py` | `ReportService`: `get_report` + AI-friendly методы, разбор ответа. |
| `planner.py` | `GoalPlanner`: описание цели → `GoalPlan` (ready/needs_input) → `Goal`. |
| `__init__.py` | Публичный API пакета (ошибки, модели, планировщик). |
| `__main__.py` | CLI `python -m yandex_metrika_agent` / `ymetrika`. |

---

## 7. Настройки (переменные окружения)

| Переменная | Назначение |
| --- | --- |
| `YANDEX_CLIENT_ID` | Идентификатор OAuth-клиента Яндекса. |
| `YANDEX_CLIENT_SECRET` | Секрет OAuth-клиента. |
| `YANDEX_REDIRECT_URI` | Callback (по умолчанию `http://localhost:8765/callback`). |
| `METRIKA_OAUTH_TOKEN` | Готовый токен (без OAuth-потока). |
| `METRIKA_TOKEN_KEY` | Ключ шифрования хранилища (64 hex символа). |
| `METRIKA_TOKEN_DIR` | Каталог токенов (по умолчанию XDG `~/.config/metrika-agent/tokens`). |
| `METRIKA_HTTP_TIMEOUT` | Таймаут запроса, сек. |
| `METRIKA_HTTP_RETRIES` | Число повторов. |
| `METRIKA_LOG_LEVEL` | Уровень логирования. |
| `METRIKA_ENV` | Окружение (`production`). |

---

## 8. Счётчики: выбор по сайту

`CounterService` (`counters.py`), Management API `GET /management/v1/counters` и
`GET /management/v1/counter/{id}`.

- `list()` — нормализованные `MetrikaCounter` (`id, name, site, domain, status,
  owner_login, permission, timezone`). Домен извлекается из `site` (снимается
  протокол и `www.`).
- `get(counter_id)` — один счётчик.
- `resolve(query)` — **все** совпадения по домену/сайту/названию (число → id).
- `resolve_one(query)` — ровно один; если несколько → `ValidationError` со списком
  кандидатов (агент просит уточнить); если ноль → `NotFoundError`.

Сценарий «Покажи статистику example.com»: агент вызывает `resolve`, при одном
совпадении использует его, при нескольких — показывает список. Пользователя не
заставляют вводить `counterId`.

---

## 9. Цели: CRUD, конструкторы, защита от дублей

`GoalService` (`goals.py`), Management API `/management/v1/counter/{id}/goals`.

CRUD: `list`, `get`, `create`, `update`, `delete`.

**Идемпотентность:** `ensure_goal(counter_id, goal)` перед созданием ищет
дубликат (`find_similar`: совпадают тип+существенные параметры **или** название
нечувствительно к регистру/пробелам). Если цель есть — возвращает найденную с
`created=False`, не создавая повтор. Все высокоуровневые `create_*_goal` идут
через `ensure_goal`.

**Конструкторы** (12): `action_goal`, `url_goal`, `phone_goal`, `email_goal`,
`file_goal`, `messenger_goal`, `search_goal`, `social_goal`, `depth_goal`
(тип `number`/глубина), `visit_duration_goal`, `payment_system_goal`,
`composite_goal` (составная `step`). Каждый собирает корректный `Goal` и
валидирует значение (URL, номер `+7...`, положительное число). Модель `Goal`
поддерживает полный список типов `GOAL_TYPES` (в т. ч. `chat`), но отдельного
конструктора для `chat` пока нет — цель собирается валидацией модели.

**Dry-run/destructive:** `update`/`delete` требуют конкретный `id`; `ensure_goal`
и `find_similar` позволяют агенту сначала показать найденную цель (название, id,
тип, условия через `Goal.describe()`) и запросить подтверждение.

---

## 10. GoalPlanner (человеческое описание → намерение цели)

`planner.py`. Определяет **тип цели** по ключевым словам и извлекает из текста
существенные значения (URL `/thank-you`, номер `+7...`, «90 секунд», «5 страниц»).

**Ключевое правило:** если для корректной цели нужно значение, которого нет в
тексте (например, имя JS-события для action-цели), планировщик **не выдумывает**
его, а возвращает статус `needs_input` с готовым вопросом. Создавать цель можно
только в статусе `ready`.

`GoalPlan.status`:

- `ready` — тип и параметры определены, `build_goal(plan)` → `Goal`;
- `needs_input` — чего-то не хватает: `missing` + `question` для пользователя;
- `unknown` — тип не распознан: агент уточняет.

Примеры поведения (проверены):

| Описание | Тип | Статус |
| --- | --- | --- |
| «цель при попадании на /thank-you» | url | ready (url=`/thank-you`) |
| «цель при клике на +7 999 000 00 00» | phone | ready |
| «визит дольше 90 секунд» | visit_duration | ready (90) |
| «просмотр 5 страниц» | number | ready (5) |
| «цель на отправку формы» | action | needs_input (нужно `event`) |
| «переход в whatsapp» | messenger | needs_input (нужна `platform`) |

Значения можно передать явно (из диалога): `plan(desc, event="submitForm")`.

---

## 11. Отчёты: универсальный + AI-friendly

`ReportService` (`reports.py`), Reports API `GET /stat/v1/data`.

Низкоуровневый `get_report(command)` принимает `ReportCommand` (или dict):
переводит человеческие метрики/измерения в `ym:s:...`, собирает `filters` из DSL,
валидирует лимиты API и разбирает ответ в `Report`.

AI-friendly методы (сами выбирают `metrics`/`dimensions`/сортировку):

| Метод | Что возвращает |
| --- | --- |
| `get_traffic(counter, date_from, date_to)` | сводка: visits, users, pageviews, bounce_rate, session_duration, new_users |
| `get_traffic_by_day(...)` | динамика по дням |
| `get_sources(...)` | источники трафика по посещаемости (убыв.) |
| `get_top_pages(..., landing=False)` | популярные/входные страницы по просмотрам |
| `get_goal_stats(counter, goal_id, by_source=False)` | достижения и конверсия цели |
| `compare_periods(counter, period_a, period_b, metrics)` | список `ComparisonRow` (current/previous/delta/delta_percent) |

Ответ разбирается в нормализованные словари, где ключи — **человеческие** имена
метрик/измерений, а не `ym:s:*`.

---

## 12. Словарь метрик и измерений

`metrics.py` — перевод «человек → API» и обратно. `MetricDirectory` работает по
псевдонимам модуля, а с ответом `GET /stat/v1/metrics` (`MetricGroupPage`) сверяет
имена с фактическим списком счётчика и подсказывает варианты при опечатке.

Метрики (значения — из официальных примеров Stat API):

| Человек | API |
| --- | --- |
| `visits` | `ym:s:visits` |
| `users` | `ym:s:users` |
| `pageviews` | `ym:s:pageviews` |
| `bounce_rate` | `ym:s:bounceRate` |
| `session_duration` | `ym:s:avgVisitDurationSeconds` |
| `new_users` | `ym:s:newUsers` |
| `depth` | `ym:s:avgPageViews` |

Измерения: `traffic_source→ym:s:trafficSource`, `search_engine→ym:s:searchEngine`,
`country/region/city→ym:s:region*`, `device→ym:s:deviceType`, `browser→ym:s:browser`,
`os→ym:s:operatingSystemRoot`, `page→ym:s:page`, `landing_page→ym:s:landingPage`,
`date/hour→ym:s:date/hour`.

Метрики цели параметризованы: `goal_reaches(id)` → `ym:s:goal<id>reaches`,
`goal_conversion(id)` → `ym:s:goal<id>conversionRate`.

Лимиты (из документации): до **20 метрик** и **10 измерений** в запросе, единый
префикс `ym:s:`/`ym:pv:`.

---

## 13. DSL фильтров

`filters.py`. Агент описывает условие структурой — `{field, operator, value}` —
а `render_filters` собирает корректную строку `filters` Метрики и переводит
человеческое имя поля в `ym:s:...`.

Операторы: `equals`, `not_equals`, `contains`, `not_contains`, `starts_with`,
`greater(>=)`, `less(<=)`, `in`, `not_in`, `is_null`, `is_not_null` (с русскими
синонимами). Рендер: `field=='v'`, `field@'v'`, `field=.('a','b')`,
`field>5`, `field=n`/`field!n`, соединение `AND`/`OR`.

Пример:
`as_filters([{"field":"traffic_source","operator":"equals","value":"organic"}])`
→ `ym:s:trafficSource=='organic'`. Значения экранируются (кавычки/слеши), поэтому
агент не может сломать запрос или внедрить произвольный синтаксис.

---

## 14. Нормализованные ошибки

Все — подклассы `AgentError` (`errors.py`), несут `to_dict()` для JSON. В ошибки
**не** попадает OAuth-токен.

| Класс | Смысл | Действие агента |
| --- | --- | --- |
| `ConfigError` | нет/некорректна конфигурация | настроить токен/ключ |
| `AuthError` | 401/403, токен истёк/отозван | обновить токен |
| `NotFoundError` | 404 / недоступен | проверить id/права |
| `ScopeError` | мало прав/скоупов | запросить нужный доступ |
| `RateLimitedError` | 420/429 | повторить позже (`retry_after`) |
| `TransportError` | 5xx/таймаут после повторов | экспоненциальный backoff |
| `ValidationError` | данные не прошли проверку | исправить аргументы |
| `ApiError` | иная 4xx Метрики | не повторять, проверить |

Разбор ответа в ошибку нужной категории — `error_from_response()`.

---

## 15. Retry / cache / дедупликация / rate limiting

`Transport` (`transport.py`):

- **retry + экспоненциальный backoff** для 5xx и сетевых ошибок;
- **учёт `Retry-After`** и статуса 420/429;
- **LRU-кэш одинаковых GET** (`cache_ttl`) — счётчики, цели, справочники;
- **single-flight (дедупликация)** параллельных одинаковых GET;
- агрегация: несколько метрик одним запросом (`metrics=a,b,c`), а не N запросов;
- `wait_async_report()` — опрос задачи асинхронного отчёта `/stat/v1/async/{id}`.

---

## 16. CLI

```
python -m yandex_metrika_agent plan "цель при попадании на /thank-you"
python -m yandex_metrika_agent plan "цель на отправку формы"
python -m yandex_metrika_agent plan "цель на отправку формы" --event submitForm
# после `pip install -e .` доступен как `ymetrika ...`
```

Печатает JSON-план цели (`status`, `goal_type`, `name`, `params`, `missing`,
`question`). Код выхода: `0` — готово, `2` — нужно доуточнить, `1` — ошибка.

---

## 17. Тестирование

Стек: `pytest` + `pytest-asyncio` + `respx` (мок HTTP, без реальных credentials).
Планируемое покрытие (см. §23 задания): заголовки OAuth, список счётчиков/целей,
создание action/url-цели, детект дубликата, отчёты traffic/sources/goal-stats,
конверсия фильтра, ошибки 401/403/429, кэш, retry.

> **Текущий статус:** unit-тесты ещё **не добавлены** (папка `tests/` не создана).
> Реализованные модули проверены ручными прогонами (GoalPlanner, словарь метрик,
> DSL фильтров, нормализация счётчика, CLI) и `compileall`/`mypy`.

---

## 18. Что реализовано и что в планах

**Реализовано (MVP):** OAuth + PKCE/loopback/device; зашифрованное хранилище и
много пользователей; транспорт (retry/кэш/дедуп/rate-limit); клиент; сервис
счётчиков с выбором по сайту; CRUD целей + 14 конструкторов + идемпотентность;
GoalPlanner; сервис отчётов (`get_report` + 6 AI-friendly); словарь метрик; DSL
фильтров; нормализованные ошибки; CLI.

**Планами (не реализовано):**

- **AI Tool Layer** — реестр инструментов со строгими JSON-схемами поверх сервисов
  (`metrika_list_counters`, `metrika_get_counter`, `metrika_list_goals`,
  `metrika_create_goal`, `metrika_update_goal`, `metrika_delete_goal`,
  `metrika_get_report`, `metrika_get_traffic`, `metrika_get_sources`,
  `metrika_get_pages`, `metrika_get_goal_stats`, `metrika_compare_periods`).
- **Фасад `YandexMetrikaService`**, объединяющий сервисы за одним интерфейсом.
- **unit/integration-тесты** (папка `tests/`).
- Расширения после MVP: Logs API, Segments, Imports, offline conversions.

Ядро намеренно **независимо от MCP**: это обычный клиент + сервисы + (в планах)
реестр инструментов. MCP можно надеть сверху позже без переделки логики.

---

## 19. Официальные источники

- Обзор API: <https://yandex.com/dev/metrika/ru/>
- Авторизация: <https://yandex.com/dev/metrika/ru/intro/authorization>
- Management API: <https://yandex.com/dev/metrika/ru/management/>
- Goals API: <https://yandex.com/dev/metrika/ru/management/openapi/goal/>
- Reports API: <https://yandex.com/dev/metrika/ru/stat/>
- Примеры отчётов: <https://yandex.ru/dev/metrika/ru/stat/examples>
- Quotas: <https://yandex.com/dev/metrika/ru/intro/quotas>
- Logs API: <https://yandex.com/dev/metrika/ru/logs/>
