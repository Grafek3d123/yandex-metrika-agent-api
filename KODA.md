# KODA.md — инструкционный контекст проекта

Этот файл — контекст для будущих взаимодействий с репозиторием `YMClientAPI`.
Перед началом работы ознакомься с ним: здесь зафиксированы назначение, архитектура,
команды сборки/запуска, правила разработки и известные расхождения.

---

## 1. Тип проекта

**Проект с кодом.** Python-библиотека + CLI. Признаки: `pyproject.toml`, пакет
`yandex_metrika_agent/` с исходниками, dev-зависимости (`pytest`, `ruff`, `mypy`).

---

## 2. Обзор проекта

**`yandex-metrika-agent-api`** — AI-ориентированный интеграционный слой поверх API
Яндекс Метрики (Management API + Reports API).

Главная идея: AI-агент оперирует **бизнес-понятиями** — сайт, счётчик, цель,
визиты, посетители, страницы, источники, конверсия, период — а не сырыми
`counterId`, `ym:s:visits`, `dimensions`, `filters`. Технические детали (эндпоинты,
имена метрик, заголовки, retry, ошибки) скрыты внутри слоя.

**Технологии:**
- Python ≥ 3.11;
- `httpx` — async HTTP;
- `pydantic` v2 — типизированные открытые модели (неизвестные поля Метрики сохраняются);
- `cryptography` — AES-GCM шифрование токенов;
- `pytest` + `pytest-asyncio` + `respx` — тесты (мок HTTP, без реальных credentials);
- `ruff` — линт/формат; `mypy --strict` — типы.

**Архитектура (слои):**

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

Агент не получает прямого доступа к низкоуровневым HTTP-вызовам — только методы сервисов.

---

## 3. Карта модулей (`yandex_metrika_agent/`)

| Файл | Назначение |
| --- | --- |
| `errors.py` | Типизированные ошибки (`AgentError` и подклассы), разбор ответа в ошибку (`error_from_response`). |
| `config.py` | Настройки из env/XDG, ключ токена, проверка конфигурации. |
| `crypto.py` | AES-GCM шифрование, отпечатки. |
| `tokens.py` | `TokenRecord`, `EncryptedFileStore`, авто-продление (`get_valid`), мьютексы. |
| `oauth.py` | `OAuthClient`, `OAuthFlow`, `LoopbackCallback` (PKCE + device + ручная вставка кода). |
| `transport.py` | HTTP-транспорт: retry, backoff, LRU-кэш GET, single-flight, rate-limit, `wait_async_report`. |
| `client.py` | `MetrikaClient`: `Authorization: OAuth`, `get_json/post_json/...`, `from_settings`. |
| `models.py` | Модели API: `Counter`, `Goal`, `MetricItem`, `Report`, `ReportCommand`, `ComparisonRow`, `DateRange`, `GOAL_TYPES`. |
| `goals.py` | `GoalService` (CRUD + идемпотентность `ensure_goal`), конструкторы 12 типов целей. |
| `metrics.py` | Словарь «человек → `ym:s:...`» и обратно, `MetricDirectory`, метрики цели. |
| `filters.py` | DSL фильтров: `Filter`, `Operator`, `render_filters`, `as_filters`. |
| `counters.py` | `CounterService`: список/чтение/выбор счётчика по сайту/домену (`resolve`/`resolve_one`). |
| `reports.py` | `ReportService`: `get_report` + AI-friendly (`get_traffic`, `get_sources`, `get_top_pages`, `get_goal_stats`, `compare_periods`). |
| `planner.py` | `GoalPlanner`: описание цели → `GoalPlan` (ready/needs_input/unknown) → `Goal`. |
| `log.py` | Логирование (анонимизация чувствительных данных). |
| `__init__.py` | Публичный API пакета (ошибки, модели, планировщик). |
| `__main__.py` | CLI `python -m yandex_metrika_agent` / `ymetrika` (команда `plan`). |

---

## 4. Ключевые файлы вне пакета

| Файл | Что в нём |
| --- | --- |
| `pyproject.toml` | Метаданные, зависимости, скрипт `ymetrika`, конфиги ruff/mypy/pytest. |
| `README.md` | Описание **целевого** CLI (`counters`, `logins`, `manage`, `config`). |
| `docs/yandex-metrika-ai.md` | Детальная документация результатов: архитектура, OAuth, модули, словарь метрик, DSL фильтров, ошибки, roadmap. |
| `Tasks/Task01.md` | ТЗ интеграции (1083 строки): требования к архитектуре, OAuth, API, тестам. |
| `.env.example` | Шаблон настроек (OAuth-клиент, device flow, скоупы, токен-ключ, runtime). |
| `.gitignore` | Исключения (секреты: `.env`, `*.token`, `*.enc`, `tokens/`). |

---

## 5. Сборка, запуск, тестирование

**Установка:**
```bash
pip install -e .
# dev-зависимости (dev-группа, НЕ extras):
uv sync                          # или: pip install pytest pytest-asyncio respx ruff mypy
```

**Запуск CLI:**
```bash
python -m yandex_metrika_agent plan "цель при попадании на /thank-you"
python -m yandex_metrika_agent plan "цель на отправку формы" --event submitForm
# после установки пакета доступен как: ymetrika plan "..."
# коды выхода: 0 — готово, 2 — нужно доуточнить, 1 — ошибка
```

**Линт / формат / типы:**
```bash
ruff check yandex_metrika_agent      # линт
ruff format yandex_metrika_agent     # формат
mypy                                 # строгая проверка типов
```

**Тесты:**
```bash
pytest                               # testpaths=tests в pyproject
```

> ⚠️ **TODO:** папка `tests/` ещё не создана — автотесты отсутствуют. Реализованные
> модули проверены ручными прогонами (`compileall`, `mypy`, CLI).

---

## 6. Настройки (переменные окружения)

Шаблон — в `.env.example` (файл `.env` игнорируется, не коммитить).

| Переменная | Назначение |
| --- | --- |
| `YANDEX_CLIENT_ID`, `YANDEX_CLIENT_SECRET` | Собственный OAuth-клиент Яндекса. |
| `YANDEX_DEVICE_FLOW` | Device flow вместо browser-flow (`true`). |
| `YANDEX_REDIRECT_URI` | Callback (по умолчанию `http://localhost:8765/callback`). |
| `METRIKA_OAUTH_TOKEN` | Готовый токен без OAuth-потока. |
| `METRIKA_TOKEN_KEY` | AES-GCM ключ хранилища (64 hex символа). |
| `METRIKA_TOKEN_DIR` | Каталог токенов (по умолчанию XDG `~/.config/metrika-agent/tokens`). |
| `METRIKA_ENV` | `production` / `sandbox`. |
| `METRIKA_HTTP_TIMEOUT`, `METRIKA_HTTP_RETRIES` | Таймаут и число повторов. |
| `METRIKA_LOG_LEVEL` | `INFO` / `DEBUG`. |

Скоупы OAuth-клиента: `metrika:read` (чтение), `metrika:write` (изменение целей).
Отдельных скоупов `read:metrika:manage`/`read:metrika:logs` **не существует**.

---

## 7. Правила разработки

**Стиль кода (ruff):**
- `line-length = 100`, `target-version = py311`, двойные кавычки;
- обязательны аннотации типов (`ANN`), запрещён `print` (`T20`) — вывод через CLI/JSON;
- включены security-правила (`S`), bugbear (`B`), simplify (`SIM`), isort (`I`);
- допустим `Any` в JSON/httpx-пейлоадах (`ANN401`), `assert` в тестах (`S101`).

**Типы (mypy):**
- `strict = true`, `warn_unreachable`, плагин `pydantic.mypy`;
- тестам ослаблены требования к аннотациям; внешние либы без stubs — `ignore_missing_imports`.

**Практики:**
- **Секреты не светятся:** OAuth-токен исключён из логов, `repr`, сообщений об
  ошибках и ответов API. Все ошибки — подклассы `AgentError` с `to_dict()`.
- **Идемпотентность:** `ensure_goal` не создаёт дубликат (поиск похожей цели).
- **Без выдумывания:** `GoalPlanner` при нехватке значения возвращает `needs_input`
  и вопрос, а не подставляет фиктивные данные. Цель создаётся только в `ready`.
- **Приоритет официальной документации:** эндпоинты/параметры/метрики — только из
  актуальной документации Яндекса (ссылки в `docs/yandex-metrika-ai.md` §19).
- **Многопользовательность** через `connection_id` (отдельный зашифрованный файл
  на подключение).

---

## 8. Известные расхождения и статус

- **CLI не соответствует README.** README описывает полный агентский CLI
  (`metrika-agent counters|logins|manage|config`), но реализована только команда
  `plan` (планирование цели из фразы). Скрипт называется `ymetrika`, а не
  `metrika-agent`.
- **Dev-зависимости** — в `[dependency-groups]`, а не в extras `[dev]`, поэтому
  `pip install -e ".[dev]"` из README не работает (ставить через `uv sync` или
  вручную).
- **Тесты отсутствуют** (папка `tests/` не создана).
- **ruff** выдаёт много `RUF001/002/003` (кириллица в докстрингах/строках) — это
  намеренные русские комментарии, не баги.

**Реализовано (MVP):** OAuth + PKCE/loopback/device; зашифрованное хранилище и
много пользователей; транспорт (retry/кэш/дедуп/rate-limit); клиент; сервис
счётчиков с выбором по сайту; CRUD целей + 12 конструкторов + идемпотентность;
GoalPlanner; сервис отчётов (`get_report` + 6 AI-friendly методов); словарь
метрик; DSL фильтров; нормализованные ошибки; CLI `plan`.

**В планах (не реализовано):** полный CLI из README; AI Tool Layer (реестр
инструментов со строгими JSON-схемами: `metrika_list_counters`,
`metrika_create_goal`, `metrika_get_report` и т. д.); фасад `YandexMetrikaService`;
unit/integration-тесты; расширения (Logs API, Segments, Imports, offline
conversions).

Ядро намеренно **независимо от MCP** — это обычный клиент + сервисы. MCP можно
надеть сверху позже без переделки логики.

---

## 9. Как использовать содержимое

- **Перед правкой кода:** сверяйся с `docs/yandex-metrika-ai.md` (что уже есть) и
  `Tasks/Task01.md` (что требуется по ТЗ).
- **Планирование цели из текста:** `python -m yandex_metrika_agent plan "<описание>"`.
- **Новые эндпоинты/метрики:** бери только из официальной документации (список
  ссылок — в `docs/yandex-metrika-ai.md` §19), ничего не выдумывай.
- **Секреты:** держи в `.env` / env-переменных; никогда не коммить (`tokens/`,
  `*.enc`, `*.token` в `.gitignore`).
- **Проверка изменений:** `ruff check` + `mypy` + `python -m compileall`.
