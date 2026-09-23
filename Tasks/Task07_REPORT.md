# Task07 — Отчёт: Преобразование в чистую Metrika capability

## Удалено

### AI Tool Layer
- `yandex_metrika_agent/ai_tools/` — весь пакет:
  - `__init__.py` — фасад `MetrikaTools`
  - `base.py` — `Tool`, `ToolSafety`, реестр, confirmation guard (HMAC-токены)
  - `counters.py` — `metrika_list_counters`, `metrika_get_counter`
  - `goals.py` — `metrika_list_goals`, `metrika_get_goal`, `metrika_create_goal`,
    `metrika_update_goal`, `metrika_delete_goal`
  - `analytics.py` — `metrika_get_report`, `metrika_get_traffic`,
    `metrika_get_traffic_by_day`, `metrika_get_sources`, `metrika_get_top_pages`,
    `metrika_get_goal_stats`, `metrika_compare_periods`
- `yandex_metrika_agent/planner.py` — `GoalPlanner` (интерпретация человеческого
  описания → `ready / needs_input / unknown`)
- `yandex_metrika_agent/__main__.py` — CLI `python -m yandex_metrika_agent plan "..."`

### Тесты AI-слоя
- `tests/test_ai_tools.py` — 42 теста на specs()/call()/response envelope
- `tests/test_destructive_safety.py` — 14 тестов на HMAC confirmation guard
- `tests/test_planner.py` — 18 тестов на natural-language planning

### Приёмочные скрипты AI-задач
- `scripts/task03_acceptance.py`
- `scripts/task03_cleanup.py`
- `scripts/task04_live_check.py`
- `scripts/task05_acceptance.py`

### Документация AI-архитектуры
- `KODA.md` — инструкционный контекст AI-агента
- `docs/yandex-metrika-ai.md` — 502 строки AI-ориентированной документации
- `Tasks/` — 14 файлов ТЗ и отчётов предыдущих задач

## Оставлено

Настоящая Metrika capability:

| Модуль | Назначение |
|--------|-----------|
| `client.py` | `MetrikaClient` — Authorization, JSON, from_settings |
| `transport.py` | HTTP: retry, backoff, rate-limit, single-flight, async reports |
| `counters.py` | `CounterService`: list, get, resolve, resolve_one |
| `goals.py` | `GoalService`: CRUD, ensure_goal, конструкторы типов |
| `reports.py` | `ReportService`: get_report, get_traffic, get_sources, get_top_pages |
| `oauth.py` | OAuthClient, OAuthFlow, PKCE, device flow |
| `tokens.py` | EncryptedFileStore, TokenRecord, get_valid |
| `crypto.py` | AES-GCM шифрование |
| `config.py` | Settings из env/XDG |
| `errors.py` | Метрика-типизированные исключения |
| `models.py` | Pydantic-модели API |
| `metrics.py` | Словарь метрик/измерений Reports API |
| `filters.py` | DSL фильтров |
| `log.py` | Логирование с анонимизацией секретов |

## Перенесено в orchestrator

Следующая ответственность **не** принадлежит Metrika client и должна
реализовываться в будущем private orchestrator:

- Интерпретация natural language ("цель на отправку формы")
- Бизнес-планирование (GoalPlanner → ready/needs_input/unknown)
- Confirmation workflow для разрушающих операций
- AI response envelopes (`ok` / `needs_input` / `error` / `confirmation_required`)
- Tool registry, `specs()`, `call()`
- MCP-обёртка
- CLI для планирования из фразы

## Изменено

### Public API
| До | После |
|----|-------|
| `from yandex_metrika_agent import MetrikaTools` | **Удалено** |
| `from yandex_metrika_agent import GoalPlanner` | **Удалено** |
| `AgentError` | `MetrikaError` |
| `__all__` — 12 несуществующих имён | Исправлен, 20 реально экспортируемых имён |
| `user_agent = "metrika-agent/0.1 (+...AI assistant...)"` | `"yandex-metrika-client/0.1"` |
| `config_dir() → ~/.config/metrika-agent` | `~/.config/yandex-metrika` |
| `_HKDF_INFO = b"metrika-agent/token-store"` | `b"yandex-metrika/token-store"` |
| `log prefix: "metrika_agent.*"` | `"yandex_metrika.*"` |
| `oauth device prefix: "metrika-agent"` | `"yandex-metrika"` |
| README: AI Tool Layer, 14 tools, confirmation workflow | README: typed SDK, сервисы, API |

### Прочее
- Исправлена строка > 100 символов в `tests/test_goals.py` (E501)
- Добавлен `ReportCommand` в импорт `__init__.py`

## Осталось под вопросом

| Элемент | Вопрос |
|---------|--------|
| `scripts/` (13 демо-скриптов) | Оставить как примеры или убрать? Сейчас оставлены. |
| Имя пакета `yandex_metrika_agent` | Менять на `yandex_metrika` — breaking change для всех потребителей. |
| `pyproject.toml` → `name` | `"yandex-metrika-agent-api"` — менять при переименовании пакета. |

## Финальная архитектура

```
                 Private Orchestrator (будущий)
                         │
                         ▼
              Metrika Python Capability
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
     CounterService   GoalService   ReportService
          │              │              │
          └──────────────┼──────────────┘
                         ▼
                   MetrikaClient
                         ▼
                      Transport
                    (retry/rate-limit/
                     single-flight)
                         ▼
                 Yandex Metrika API
              (Management v1 / stat v1)

OAuth + EncryptedFileStore (сквозной слой)
```

## Метрики

| | До | После |
|--|-----|-------|
| Файлов Python (пакет) | 19 | 14 |
| Тестов | 187 | 137 |
| Строк удалено | — | ~8 900 |
| Ruff (package + tests) | 1 error | 0 errors |
| pytest | 187 passed | 137 passed |
