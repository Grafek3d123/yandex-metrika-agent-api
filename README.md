# yandex-metrika-agent-api

AI-ориентированный интеграционный слой поверх API Яндекс Метрики
(Management API + Reports API) для Python ≥ 3.11.

Главный принцип: AI-агент оперирует **бизнес-понятиями** — сайт, счётчик, цель,
визиты, посетители, страницы, источники, конверсия, период — а не `counterId`,
`ym:s:visits`, `dimensions`, `filters`. Технические детали (эндпоинты, имена
метрик, заголовки, retry, ошибки) скрыты внутри слоя.

## Установка

```bash
pip install -e .
# dev-зависимости (pytest, respx, ruff, mypy):
pip install pytest pytest-asyncio respx ruff mypy
```

## Быстрый старт

### AI Tool Layer (рекомендуемый вход для агента)

```python
import asyncio
from yandex_metrika_agent import MetrikaClient, MetrikaTools

async def main() -> None:
    client = MetrikaClient(token="<OAUTH_TOKEN>")
    tools = MetrikaTools(client)
    try:
        # Схемы всех 14 инструментов — зарегистрировать у AI-агента
        specs = tools.specs()

        # Бизнес-вызов: счётчик по домену, период словами
        answer = await tools.call(
            "metrika_get_traffic",
            {"counter": "example.com", "date_from": "2026-09-01", "date_to": "2026-09-07"},
        )
        print(answer["status"])   # ok | needs_input | error
        print(answer["data"])     # visits, users, pageviews, bounce_rate, ...

        # Цель из человеческого описания; если данных не хватает — вопрос
        answer = await tools.call(
            "metrika_create_goal",
            {"counter": "example.com", "description": "цель на отправку формы"},
        )
        # answer["status"] == "needs_input", answer["question"] == "Какое JS-событие..."
    finally:
        await client.aclose()

asyncio.run(main())
```

### Сервисы напрямую

```python
from yandex_metrika_agent import (
    CounterService, GoalService, ReportService, GoalPlanner, MetrikaClient,
)

client = MetrikaClient(token="...")
counters = CounterService(client)
counter = await counters.resolve_one("example.com")   # один счётчик по сайту

goals = GoalService(client)
result = await goals.create_url_goal(counter.id, name="Спасибо", url="/thank-you")
# result.created — создана или уже существовала (идемпотентно)

reports = ReportService(client)
summary = await reports.get_traffic(counter.id, date_from="2026-09-01", date_to="2026-09-07")
```

### Планирование цели из фразы (CLI)

```bash
python -m yandex_metrika_agent plan "цель при попадании на /thank-you"
python -m yandex_metrika_agent plan "цель на отправку формы" --event submitForm
# после установки доступно: ymetrika plan "..."
# коды выхода: 0 — готово, 2 — нужно доуточнить, 1 — ошибка
```

## AI-инструменты (14)

| Инструмент | Назначение |
| --- | --- |
| `metrika_list_counters` | список счётчиков пользователя |
| `metrika_get_counter` | один счётчик по id/домену/названию |
| `metrika_list_goals` | цели счётчика |
| `metrika_get_goal` | цель по id |
| `metrika_create_goal` | создание цели из описания или явного типа (идемпотентно) |
| `metrika_update_goal` | изменение цели (название, цена) |
| `metrika_delete_goal` | удаление цели (необратимо; требует подтверждения) |
| `metrika_get_report` | произвольный отчёт (человеческие имена метрик/измерений) |
| `metrika_get_traffic` | сводка посещаемости |
| `metrika_get_traffic_by_day` | динамика по дням |
| `metrika_get_sources` | источники трафика |
| `metrika_get_top_pages` | популярные страницы |
| `metrika_get_goal_stats` | достижения и конверсия цели |
| `metrika_compare_periods` | сравнение двух периодов |

Единый конверт ответа:

```json
{"status": "ok", "data": {...}}
{"status": "needs_input", "question": "...", "missing": ["event"]}
{"status": "error", "error": {"error": "NotFoundError", "message": "...", "details": {}}}
{"status": "confirmation_required", "confirmation_id": "...", "question": "...", "data": {...}}
```

Правило безопасности: если для цели не хватает существенного значения (имя
JS-события, URL, телефон), инструмент возвращает `needs_input` с вопросом и
**не выдумывает** значение. Создание цели идемпотентно: повтор не создаёт
дубликат (совпадение по типу и существенным параметрам).

## Подтверждение разрушающих операций (destructive safety)

Инструменты классифицированы по уровню опасности (`ToolSafety`):
`READ_ONLY`, `MUTATING` (create/update), `DESTRUCTIVE` (только `metrika_delete_goal`).
Класс `safety` обязателен у каждого инструмента — незадекларированный инструмент
не собрать. Guard подтверждения живёт **в реестре ниже уровня handler'а**,
поэтому новый разрушающий инструмент нельзя случайно реализовать без проверки.

`metrika_delete_goal` **не выполняет DELETE без валидного подтверждения**:

1. Первый вызов возвращает `confirmation_required` с `confirmation_id` и
   сводкой операции (action, `counter_id`, `goal_id`); API-запрос не уходит.
2. Доверенный хост после явного согласия пользователя получает одноразовый
   токен: `tools.approve_confirmation(confirmation_id, approved_by="user")`.
   Этот метод **не является инструментом** и недоступен из AI Tool Layer.
3. Повторный вызов с `confirmation_token` выполняет ровно один DELETE.

Токен — HMAC под секретом процесса: агент не может его подделать или передать
`confirmed=true`. Он привязан к точным параметрам (`connection_id`,
`counter_id`, `goal_id`, набор аргументов), ограничен по времени (TTL) и
одноразовый. Чужой/просроченный/повторно использованный токен → `DELETE`
не выполняется (`confirmation_required` или `error`).

```python
answer = await tools.call("metrika_delete_goal", {"counter": "example.com", "goal_id": 77})
# answer["status"] == "confirmation_required"; цель цела
token = tools.approve_confirmation(answer["confirmation_id"], approved_by="user")
answer = await tools.call("metrika_delete_goal", {
    "counter": "example.com", "goal_id": 77,
    "confirmation_token": token["confirmation_token"],
})  # answer["status"] == "ok" — выполнен ровно один DELETE
```

Ядро независимо от MCP: реестр `Tool(name, description, inputSchema, handler, safety)`
со `specs()`/`call()` можно обернуть в MCP-сервер без переделки логики.

## Настройка (переменные окружения)

| Переменная | Назначение |
| --- | --- |
| `METRIKA_OAUTH_TOKEN` | готовый токен без OAuth-потока |
| `YANDEX_CLIENT_ID`, `YANDEX_CLIENT_SECRET` | собственный OAuth-клиент Яндекса |
| `YANDEX_DEVICE_FLOW` | device flow вместо browser-flow (`true`) |
| `YANDEX_REDIRECT_URI` | callback (по умолчанию `http://localhost:8765/callback`) |
| `METRIKA_TOKEN_KEY` | AES-GCM ключ хранилища (64 hex символа) |
| `METRIKA_TOKEN_DIR` | каталог токенов (по умолчанию XDG `~/.config/metrika-agent/tokens`) |
| `METRIKA_ENV` | `production` / `sandbox` |
| `METRIKA_HTTP_TIMEOUT`, `METRIKA_HTTP_RETRIES` | таймаут и число повторов |
| `METRIKA_LOG_LEVEL` | `INFO` / `DEBUG` |

Скоупы OAuth-клиента: `metrika:read`, `metrika:write`.

## Архитектура

```
AI Agent
   ↓  MetrikaTools (14 инструментов, строгие JSON-схемы, ok/needs_input/error)
Сервисы:  CounterService · GoalService(+GoalPlanner) · ReportService
   ↓
MetrikaClient            — заголовок Authorization, разбор JSON/ошибок
   ↓
Transport                — retry, backoff, кэш GET (по connection), single-flight, rate-limit
   ↓
HTTP API Яндекс Метрики  — Management v1 (/management/v1), Reports v1 (/stat/v1)

OAuth / EncryptedFileStore — авторизация и безопасное хранение токенов (сквозной слой)
```

Подробности — в [docs/yandex-metrika-ai.md](docs/yandex-metrika-ai.md):
OAuth-потоки, словарь метрик, DSL фильтров, поведение GoalPlanner, ошибки,
ограничения API.

## Лимиты Метрики (из документации)

- До 20 метрик и до 10 измерений в запросе отчёта; единый префикс `ym:s:`/`ym:pv:`.
- До 20 условий фильтра, длина строки фильтра до 10 000 символов.
- Квоты (запросы/сек на IP, параллельные запросы, отчёты/5 мин, 5 000
  запросов/сутки на пользователя) обрабатываются rate-limiter транспорта.

## Тесты и качество

```bash
pytest          # 150+ unit-тестов на respx (без реальных credentials)
ruff check yandex_metrika_agent
mypy            # strict
```

## Статус и roadmap

Реализовано: OAuth+PKCE, шифрованное хранилище, транспорт, клиент, сервисы
(счётчики/цели/отчёты), GoalPlanner, AI Tool Layer, 150+ тестов.

В планах: полный агентский CLI (`counters|logins|manage|config`), MCP-обёртка,
Logs API, Segments, Imports, offline conversions.
