# Yandex Metrika Agent CLI

Агентский CLI для работы с API Яндекс.Метрики: счётчики, логины, визиты,
источники, удержания, события, вебвизор, экшены, файлы, аудитории.

## Установка

```bash
pip install -e .
```

## Настройка

Требуется OAuth-токен Метрики: <https://oauth.yandex.ru/>.

```bash
metrika-agent config set-token            # интерактивно (скрытый ввод)
metrika-agent config set-token --token ... --client-id ... --secret ...
# либо переменные окружения:
#   METRIKA_OAUTH_TOKEN, METRIKA_CLIENT_ID, METRIKA_CLIENT_SECRET, METRIKA_REDIRECT_URI
metrika-agent config show                 # без секретов
metrika-agent config test
metrika-agent config reset
```

## Формат вывода

Полезная нагрузка всегда в поле `data`:

```json
{"ok": true, "data": {...}}
{"ok": false, "error": {"type": "NotConfigured", "code": "not_configured", "message": "..."}}
```

## Команды

Справка по всем командам и параметрам: `metrika-agent --help`, `metrika-agent <cmd> --help`.

### Счётчики и метаданные

| Команда | API |
| --- | --- |
| `metrika-agent counters list` | `counters` |
| `metrika-agent counters get` | `counters` |
| `metrika-agent counters access` | `counters/access` |
| `metrika-agent counters permissions` | `counters/permissions` |
| `metrika-agent counters goals` | `goals` |
| `metrika-agent counters labels` | `labels` |
| `metrika-agent counters api-access` | `registry` |
| `metrika-agent columns list` | `columns` |
| `metrika-agent dictionary list` | `dictionary` |
| `metrika-agent tracksites list` | `tracksites` |
| `metrika-agent offline-segments list/download` | `offline-segments` |

### Логины

| Команда | API-метод |
| --- | --- |
| `metrika-agent logins visits-stream` | `visits` |
| `metrika-agent logins visits` | `visits/download` |
| `metrika-agent logins logins-stream` | `logins` |
| `metrika-agent logins logins` | `logins/download` |
| `metrika-agent logins sources` | `sources` |
| `metrika-agent logins goals` | `goals` |
| `metrika-agent logins retention` | `retention` |
| `metrika-agent logins lookalike` | `lookalike` |
| `metrika-agent logins events-stream` | `events` |
| `metrika-agent logins events` | `events/download` |
| `metrika-agent logins webvisor` | `webvisor` |
| `metrika-agent logins webvisor-session` | `webvisor-session` |
| `metrika-agent logins actions` | `actions` |
| `metrika-agent logins files` | `files` |
| `metrika-agent logins audiences` | `audiences` |

### Управление сущностями

`metrika-agent manage create|update|delete` с `--entity counter|goal|audience|offline-segment|column|tracksite`.
`audience` и `offline-segment` поддерживают только `delete`.

### Параметры `logins`

- `--date1`, `--date2` (обязательны), `--limit` (по умолчанию 100)
- `--metrics`, `--dimension`, `--goals`, `--audience-name`, `--segment-sequence`
- `--session-access-pattern`, `--source`, `--source-goal-type`
- `--retention-return-offset`, `--lookalike-start-offset`, `--lookalike-end-offset`
- `--field`, `--trace-fields`, `--compact`
- `--offset` + `--page-size` + `--max-records` — постраничная выгрузка
- `--concatenate` — вместо JSON-массива печатать записи построчно (JSONL)
- `--output FILE|dir/|-` — выгрузка (`download`-методы), `-` = stdout

### Условные переходы (sequence)

```bash
--segment-sequence 'goal:signup;within:30m'
--segment-sequence 'param:utm_source=telegram;within:1h'
--segment-sequence 'param:price>100;within:1d'
--segment-sequence 'sequence:start=param:page=/cart;limit=3'
--segment-sequence 'userSequence:start=goal:buy;limit=5'
```

`start=` принимает `goal:<имя>` или `param:<ключ><>=<|>|<><значение>`, либо `auto`
(первое условие строки).

## Ошибки и выгрузка

| Код | Смысл | Действие агента |
| --- | --- | --- |
| `1` `api_error` | ошибка Метрики (4xx) | не повторять, проверить параметры |
| `2` `quota_exceeded` | превышена квота запросов | `retry_after` из `details`, повторить позже |
| `3` `timeout` | таймаут | повторить с экспоненциальной задержкой |
| `4` `auth_error` | протух/неверный токен | `metrika-agent config test`, обновить токен |
| `5` `not_configured` | нет конфигурации | настроить токен |
| `6` `bad_request` | невалидные параметры CLI | исправить аргументы |
| `7` `network_error` | сеть недоступна | повторить с экспоненциальной задержкой |

Выгрузка: `--offset`/`--page-size` (или `--limit`+`--offset`) + `--max-records`
для остановки, `--output` для сохранения. Прогресс — в `stderr`.

## Переменные окружения

| Переменная | Назначение |
| --- | --- |
| `METRIKA_OAUTH_TOKEN` | OAuth-токен |
| `METRIKA_CLIENT_ID`, `METRIKA_CLIENT_SECRET`, `METRIKA_REDIRECT_URI` | refresh-поток |
| `METRIKA_CONFIG_FILE` | путь к конфигу (по умолчанию `~/.config/metrika-agent/config.json`) |
| `METRIKA_BASE_URL` | альтернативный хост API |
| `METRIKA_TIMEOUT` | таймаут запроса, сек |
| `METRIKA_MAX_RETRIES` | число повторов сетевых/5xx ошибок |

## Лимиты Метрики (из документации)

- Интервалы дат: `days` 1–31, `weeks` 1–12, `months`/`quarters`/`years` 1–24.
- `visits`/`logins`: ≤5 метрик и ≤2 измерений; `sources`/`goals`/`retention`:
  ≤5 метрик и ≤5 измерений; `events`: ≤3 метрик и ≤5 измерений; `actions`: ≤3 и ≤3.
- `webvisor`/`webvisor-session`: ≤1000 записей и ≤31 день; `webvisor-session` — только 1 день.
- `visits/download`: ≤100 000 записей, `logins/download`: ≤1 000 000.
- `lookalike`: ≤100 записей, ≥1000 сегментов в источнике, `retention-return-offset` 1–28.
- `events/download`: ≤30 млн записей и ≤90 дней, только `ym:s:visit`, `ym:s:click`, `ym:s:search`.
- `logins-stream`/`events-stream` — платный тариф.
- `counters list`: не более 1 000 000 счётчиков.
- Квоты: 1000 запросов/мин, 250 000 000 единиц/сутки; цена запроса 1–25 единиц.

## Тесты

```bash
pip install -e ".[dev]"
pytest
```
