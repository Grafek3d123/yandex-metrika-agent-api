# Task03 — Реальный приёмочный прогон против API Яндекс Метрики

Дата прогона: **2026-09-15** (финальный зелёный), первый прогон OAuth: 2026-09-14.
Скрипт: [`scripts/task03_acceptance.py`](../scripts/task03_acceptance.py).
Артефакты: [`scripts/task03_results.json`](../scripts/task03_results.json),
[`scripts/task03_run.log`](../scripts/task03_run.log) (DEBUG, без токенов).

## 1. Итог

**SUMMARY: status=ok, failed_steps=[], 16/16 шагов, 3.22 с.**

Все 18 пунктов `Tasks/Task03.md` подтверждены (пункты «создать счётчик»,
«определить счётчик» покрыты шагами 3–4 и 12).

## 2. Окружение

| Параметр | Значение |
| --- | --- |
| Пользователь Яндекса | `ERTaipit` |
| OAuth | browser-flow + PKCE, loopback-callback `http://localhost:8765/callback` |
| connection_id | `task03` (токен в зашифрованном `EncryptedFileStore`, AES-GCM) |
| OAuth-клиент | собственный (`YANDEX_CLIENT_ID/SECRET` из `.env`, не коммитится) |
| API | production, `api-metrika.yandex.net` |

Скоупы: Яндекс не вернул поле `scopes` в token-ответе (список пуст). Право
`metrika:read` подтверждён чтением (list_counters/goals/reports), право
`metrika:write` — успешными create_counter/create_goal/update_goal/delete_goal.

## 3. Шаги прогона (реальные данные)

| # | Шаг ТЗ | Шаг скрипта | Результат |
| --- | --- | --- | --- |
| 1 | Реальный OAuth | `oauth` | ok — user `ERTaipit`, повторный прогон использует сохранённый токен (первый прогон 14.09 прошёл полный browser-flow + PKCE) |
| 2 | metrika:read + metrika:write | подтверждено шагами 3–13 | ok — чтение и запись работают |
| 3 | list_counters | `list_counters` | ok — 3 счётчика аккаунта |
| 4 | Создать тестовый счётчик | `create_counter` | ok — **`112675662`** (`Task03 acceptance 20260915-211653`, `task03-acceptance.example`) через `POST /management/v1/counters` |
| 5 | Определить счётчик | `resolve_counter` | ok — `resolve_one("Task03 acceptance 20260915-211653")` → 112675662 |
| 6 | Реальные goals | `list_goals` | ok — целей 0 (чистый счётчик) |
| 7 | Создать тестовую цель | `create_goal` | ok — **`621047148`** (url-цель, `contain /thank-you`), идемпотентно через `ensure_goal` |
| 8 | Получить через get | `get_goal` | ok — имя/тип/условия совпадают |
| 9 | Изменить | `update_goal` | ok — переименование в `task03 renamed 20260915-211653` (PUT `…/goal/{id}`) |
| 10 | Статистика цели | `get_goal_stats` | ok —Reports API ответил нулями (трафика нет): visits/users/goal_reaches/conversion = 0 |
| 11 | Удалить | `delete_goal` | ok — цель 621047148 удалена |
| 12 | Реальный Reports API response | `get_traffic` | ok — `/stat/v1/data` вернул totals: visits/users/pageviews/bounce_rate/session_duration/new_users (нулевой период) |
| 13 | traffic | `get_traffic` | ok (см. 12) |
| 14 | sources | `get_sources` | ok — 0 строк (источников нет) |
| 15 | pages | `get_top_pages` | ok — 0 строк (`ym:pv:pageviews` × `ym:pv:URL`) |
| 16 | needs_input для неоднозначного counter | `ambiguous_counter` | ok — второй счётчик с тем же именем (**112675663**, site `task03-acceptance-2.example`) → `resolve_one` бросил `ValidationError` с `details.candidates` из 2 счётчиков |
| 17 | Отсутствие токенов в логах | `no_tokens_in_logs` | ok — `leaked=[]`: access/refresh-токены не найдены в `task03_run.log` (DEBUG-уровень) и в JSON-выводе |
| 18 | Полный результат | этот файл + артефакты | ok |

## 4. Найденные и исправленные баги (ценность прогона)

1. **`Goal.to_request` — `is_favorite`**: PUT с полем `is_favorite` (копировалось
   из полученной цели) → API 400 `invalid_json, path: goal.is_favorite`
   («Could not read JSON, line 1 column 150»). Фактический API не принимает
   поле, хотя openapi-схема editGoal его описывает. Исправлено: поле
   исключено из тела PUT/POST (`models.py`).
2. **`Goal.to_request` — `is_retargeting`**: та же ошибка (column 153,
   `path: goal.is_retargeting`). Аналогично исключено из тела запроса.
3. **`get_top_pages` — несуществующие измерения**: `ym:s:page`, `ym:s:landingPage`,
   `ym:s:exitPage` отвергаются Reports API (`error 4001`, проверено живыми
   запросами). Правильная комбинация для страниц: метрика `ym:pv:pageviews`
   (строчная `v`) × измерение `ym:pv:URL`; группы `ym:s:` и `ym:pv:` в одном
   запросе не смешиваются. Исправлено: словарь метрик (`metrics.py`),
   `get_top_pages` переведён на pv-пару, параметр `landing` удалён (измерения
   входной страницы в Reports API нет).
4. **Windows-кодировка консоли**: cp866 ломал кириллицу в выводе скрипта —
   потоки переведены в UTF-8 (`sys.stdout/stderr.reconfigure`).

Обновлены: `models.py`, `metrics.py`, `reports.py`, `tests/test_metrics.py`,
`docs/yandex-metrika-ai.md` (словарь метрик).

## 5. Ограничения API, подтверждённые прогоном

- Счётчики удаляются через ``DELETE /management/v1/counter/{counterId}``
  (ответ ``{"success": true}``). Добавлен `CounterService.delete()`; в
  cleanup-прогоне 2026-09-15 удалены все 5 тестовых счётчиков (см. §6).
- Яндекс может не вернуть `scopes` в token-ответе — работоспособность
  подтверждается самими запросами.
- `GET /stat/v1/data` с `ym:s:`-метриками и `ym:pv:`-измерением → ошибка 4011.

## 6. Очистка тестовых ресурсов

Все тестовые счётчики **удалены** (cleanup 2026-09-15,
[`scripts/task03_cleanup.py`](../scripts/task03_cleanup.py), фильтр по префиксу
имени `Task03 acceptance`). В аккаунте осталось **0** счётчиков.

| ID | Имя | Статус |
| --- | --- | --- |
| 112600191 | Task03 acceptance 20260914-231627 | удалён |
| 112602881 | Task03 acceptance 20260914-232515 | удалён |
| 112602891 | Task03 acceptance 20260914-232515 | удалён |
| 112675662 | Task03 acceptance 20260915-211653 | удалён (вместе с целью 621158581) |
| 112675663 | Task03 acceptance 20260915-211653 | удалён |


## 7. Воспроизведение

```powershell
.venv\Scripts\python.exe scripts\task03_acceptance.py
# при отсутствии токена в хранилище откроется браузер (OAuth browser-flow + PKCE)
```

Качество после исправлений: **152 pytest — зелёные, ruff чистый,
mypy --strict — без ошибок (35 файлов).**
