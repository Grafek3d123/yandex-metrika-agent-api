# B2 — Ошибки чтения реальных целей: разбор и фикс (сессия 2026-09-19)

## 0. Область и метод

Только сегодняшняя сессия (2026-09-19). Изменения **не закоммичены** (последний
коммит `690fc44` от 16.09 — Task06 B1, в этот отчёт не входит).

Триггер: пользователь попросил статистику по реальному счётчику `<site>` за
4 месяца. Для входа в новую учётку был пройден **OAuth device-flow**
(`connection_id="new-account"`, пользователь `<account>`). Live-прогон и сборщик
отчётов вскрыли три бага **одного класса** — жёсткая валидация модели `Goal`
ломала **чтение** реальных ответов Management API.

Метод поиска: не офлайн-моки, а **реальный аккаунт** (27 счётчиков, живые
автоцели) + сырой JSON эндпоинта `GET /management/v1/counter/{id}/goals`.

Ключевой принцип фикса: **чтение устойчиво, запись строга**. Агент обязан
прочитать то, что реально отдаёт Метрика, но выдумать/создать цель недопустимого
вида по-прежнему нельзя.

---

## 1. Реестр ошибок

| ID | Симптом | Корень | Статус |
| --- | --- | --- | --- |
| B2-1 | `list/get goals` падает: `Неизвестный тип цели 'contact_data'` | `Goal._known_type` режет неизвестные `type` | ✅ исправлено |
| B2-2 | `list goals` падает: `Цели типа 'phone' нужны условия conditions` | `_check_payload` требует инварианты **записи** при **чтении** | ✅ исправлено |
| B2-3 | `list goals` падает: `Неизвестный тип условия 'all_social'` | `GoalCondition._known_type` режет неизвестные `type` условия | ✅ исправлено |
| O-1 | `Устройства/Язык`: `error code: 4001` | ограничение Reports API для счётчика, **не баг слоя** | ⚠️ задокументировано |

---

## 2. B2-1 — неизвестный тип цели блокировал чтение

### Как проявилась
Первый же live-запрос `GoalService.list(<counter_id>)` (реальный счётчик
`<site>`) упал:

```
pydantic_core.ValidationError: 1 validation error for Goal
type
  Value error, Неизвестный тип цели 'contact_data'. Допустимо: action, url, ...
```

### Диагноз (по сырому JSON API)
Сырой запрос `GET /management/v1/counter/<counter_id>/goals` показал реальные типы:

```
['cdp_order_cancelled', 'cdp_order_in_progress', 'cdp_order_paid',
 'cdp_order_spam', 'contact_data', 'contact_data_sent',
 'messenger', 'phone', 'url']
```

Все незнакомые — **автоцели Метрики** (`goal_source: "auto"`), которые сервис
создаёт сам:

```json
{
  "id": 545418480,
  "name": "Автоцель: заполнил контактные данные",
  "type": "contact_data",
  "goal_source": "auto",
  "status": "Active"
}
```

Наш статический `GOAL_TYPES` перечислял только **создаваемые** типы. Реальный
аккаунт содержит автоцели, которых там нет → модель отвергала весь ответ, и
агент **не мог прочитать статистику ни одного боевого счётчика**.

### Конфликт и его решение
Столкнулись два требования проекта:

1. **«Без выдумывания»** — строгая валидация типа защищает от того, чтобы агент
   выдумал цель несуществующего вида.
2. **Работоспособность на реальных данных** — агент обязан читать то, что
   реально отдаёт Метрика (иначе интеграция бесполезна).

Ослаблять валидацию целиком — значит потерять защиту от галлюцинаций. Оставить
как есть — интеграция падает на первом же реальном счётчике.

**Разрешение — разделить направление потока данных:**
- **Чтение (модель):** неизвестный `type` сохраняется как есть (read-tolerant).
  Модель и так `extra="allow"` — это её штатное поведение для новых полей.
- **Запись (сервис):** `GoalService.create()` по-прежнему отвергает тип вне
  `GOAL_TYPES`. Строгость переехала из модели в точку создания, где выдумывание
  реально опасно.

```python
# models.py — Goal._known_type: только нормализация, без отказа
@field_validator("type")
@classmethod
def _known_type(cls, value: str) -> str:
    # Чтение устойчиво: Метрика создаёт автоцели с типами вне нашего перечисления
    # (contact_data, cdp_order_paid и т. п.). Неизвестный тип сохраняем как есть,
    # иначе разбор ответа API падает. Строгая проверка — при создании
    # в GoalService.create.
    return value.strip().lower()
```

```python
# goals.py — GoalService.create: строгость на записи сохранена
if goal.type not in GOAL_TYPES:
    raise ValidationError(
        f"Нельзя создать цель неизвестного типа {goal.type!r}. "
        f"Допустимо: {', '.join(GOAL_TYPES)}.",
        details={"type": goal.type, "allowed": list(GOAL_TYPES)},
    )
```

---

## 3. B2-2 — инварианты записи срабатывали при чтении

### Как проявилась
После фикса B2-1 `list goals` упал снова, но уже иначе:

```
ValidationError: Value error, Цели типа 'phone' нужны условия conditions
  input_value={'id': 585281057, 'name': ..., 'hide_phone_number': False}
```

### Диагноз
`GET /counter/{id}/goals` возвращает **сокращённые** объекты целей: телефонная
цель в списке приходит **без** `conditions` (детали — только в `GET .../goal/{id}`).
А `@model_validator(mode="after") _check_payload` требовал `conditions` для
`phone`/`url`/`action`/... безусловно — то есть навязывал **инвариант создания**
объекту, который просто **прочитан** из ответа.

### Конфликт и его решение
Конфликт: «инвариант модели должен enforced всегда» vs «фактический контракт
списка отдаёт неполные объекты».

**Разрешение — вынести бизнес-инварианты из модельного валидатора в явный
метод записи.** Модель стала чистой read-моделью (принимает ответ как есть),
а проверка обязательных данных переехала в `Goal.validate_for_write()`, который
вызывается только при создании:

```python
# models.py — вместо @model_validator _check_payload
def validate_for_write(self) -> None:
    """Проверить обязательные данные типа перед СОЗДАНИЕМ цели.
    Инварианты относятся к записи, а не чтению: список целей возвращает
    сокращённые объекты (phone без conditions), жёсткая проверка в модели
    ломала разбор. Вызывается из GoalService.create."""
    if self.type in GOAL_TYPES_REQUIRING_CONDITIONS and not self.conditions:
        raise ValueError(...)
    ...
```

```python
# goals.py — create вызывает проверку перед POST
try:
    goal.validate_for_write()
except ValueError as exc:
    raise ValidationError(str(exc)) from exc
```

Побочный плюс: `describe()`, `signature()`, `title` уже корректно работают с
любым (в т. ч. неизвестным) типом — фикс чтения их не задел.

---

## 4. B2-3 — неизвестный тип условия `all_social`

### Как проявилась
При сборе отчёта `<site>` раздел «Цели» снова упал (в примечаниях сбора):

```
ValidationError: Неизвестный тип условия 'all_social'.
Допустимо: exact, start, contain, regexp, messenger, file, all_files, ...
```

### Диагноз
Тот же класс бага, но на уровень глубже — в `GoalCondition.type`. Автоцель
«переход в соцсеть» использует тип условия **`all_social`**, которого нет в
`CONDITION_TYPES`. Жёсткий `field_validator` валидатора условий ронял разбор
всей цели.

### Конфликт и его решение
Тот же конфликт «строгость vs реальные данные», что и B2-1. То же разделение:

```python
# models.py — GoalCondition._known_type: чтение устойчиво
@field_validator("type")
@classmethod
def _known_type(cls, value: str | None) -> str | None:
    # all_social у автоцелей сохраняем как есть; строгая проверка — при
    # создании в Goal.validate_for_write.
    return value.strip().lower() if value is not None else None
```

Строгость на записи восстановлена **рекурсивно** — проверяются условия цели и
её шагов (`_all_write_conditions`), чтобы составные цели тоже были под защитой:

```python
# models.py — внутри validate_for_write
for condition in self._all_write_conditions():
    if condition.type is not None and condition.type not in CONDITION_TYPES:
        raise ValueError(f"Неизвестный тип условия {condition.type!r}. ...")
```

### Результат
После фикса раздел «Цели» отчёта `<site>` заполнился 18 целями, включая
автоцели (`form`, `social`, `contact_data`, `contact_data_sent`, `phone`,
`messenger`, `email`) и пользовательские (`vk_click`, `lead_form`, …).

---

## 5. O-1 — устройства и язык (НЕ баг, ограничение API)

`ym:s:deviceType` и `ym:s:language` в отчётах `<site>` вернули:

```
ApiError: Incorrectly specified attribute, value: ym:s:deviceType, error code: 4001
```

Проверено прямыми запросами: `device_type`, `device_model`, `language` — все
под `4001` для этого счётчика. Это **ограничение Reports API** (измерение
недоступно/переименовано на стороне Метрики), а не дефект слоя. Решение — не
«чинить», а **честно отражать** в отчёте: сбой одного срез-отчёта не рвёт сбор
(изоляция по блокам), ошибка попадает в раздел «Примечания сбора». Окружение
при этом доступно через срезы ОС и браузеров.

---

## 6. Итоговое решение конфликта (сводно)

Единый паттерн для B2-1/2/3 — **асимметричная валидация**:

| Операция | Поведение | Где |
| --- | --- | --- |
| **Чтение** (`list`/`get`/`model_validate`) | read-tolerant: неизвестные `type` цели/условия сохраняются | `models.py` (field_validator) |
| **Запись** (`create`) | строгая: тип цели ∈ `GOAL_TYPES`, типы условий ∈ `CONDITION_TYPES`, обязательные поля | `goals.py:create` + `Goal.validate_for_write` |

Так сохранены оба требования без компромисса: агент читает реальные данные
Метрики (включая автоцели), но **не может создать** цель или условие
выдуманного вида.

---

## 7. Regression-тесты (8 новых)

`tests/test_goals.py`:

| Тест | Что фиксирует |
| --- | --- |
| `test_goal_model_reads_unknown_type` | модель читает `contact_data` |
| `test_list_goals_tolerates_auto_types` | список с автоцелью читается целиком |
| `test_create_rejects_unknown_type` | `create` отвергает неизвестный тип цели |
| `test_goal_model_reads_phone_without_conditions` | `phone` без `conditions` читается |
| `test_create_phone_without_conditions_rejected` | `create` требует `conditions` для `phone` |
| `test_goal_condition_reads_unknown_type` | модель читает условие `all_social` |
| `test_list_goals_tolerates_social_auto_goal` | список с `all_social` читается |
| `test_create_rejects_unknown_condition_type` | `create` отвергает `all_social` |

---

## 8. Проверки

| Гейт | Результат |
| --- | --- |
| `pytest` | **187 passed** (было 179, +8 regression) |
| `ruff check` | All checks passed |
| `mypy` (strict) | no issues found in 36 source files |
| `compileall` | OK |
| Live e2e (`test_new_account.py`, `<account>`) | счётчики/цели/трафик/конверсия — все шаги зелёные |
| Live отчёт (`<site>_report.py`) | 12 разделов, 18 целей, 2 примечания (только O-1) |

### Объём изменений
```
yandex_metrika_agent/models.py  | read-tolerant Goal.type + GoalCondition.type;
                                  _check_payload → validate_for_write (+_all_write_conditions)
yandex_metrika_agent/goals.py   | GoalService.create: строгая проверка типа + вызов validate_for_write
tests/test_goals.py             | +8 regression-тестов
```
167 insertions(+), 20 deletions(-). Секреты (OAuth-токены) в diff/логи не попадают.

### Статус
Фикс в рабочем дереве, git-мутаций не выполнялось.
