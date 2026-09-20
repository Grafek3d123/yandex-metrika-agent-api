"""Модели Management API v1 и Reports API.

Поля и допустимые значения соответствуют официальной документации:

* счётчики и цели — ``https://api-metrika.yandex.net/management/v1``;
* отчёты — ``https://api-metrika.yandex.net/stat/v1``.

Модели открыты (``extra="allow"``): Метрика добавляет поля, и они должны
доходить до вызывающего, а не отбрасываться. Проверки на уровне модели — только
те, что гарантированно ломали бы запрос (длина имени, известный тип цели,
набор условий для типа).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Максимальная длина названия цели (ограничение API).
GOAL_NAME_MAX_LENGTH = 255
#: Максимальная длина значения условия.
GOAL_CONDITION_URL_MAX_LENGTH = 16_384

#: Типы целей Management API v1.
GoalType = Literal[
    "action",
    "url",
    "number",
    "step",
    "phone",
    "email",
    "messenger",
    "file",
    "search",
    "social",
    "payment_system",
    "visit_duration",
    "chat",
]

GOAL_TYPES: tuple[str, ...] = (
    "action",
    "url",
    "number",
    "step",
    "phone",
    "email",
    "messenger",
    "file",
    "search",
    "social",
    "payment_system",
    "visit_duration",
    "chat",
)

#: Условия вида «по значению»: JS-событие, URL, телефон, e-mail.
VALUE_CONDITION_TYPES: tuple[str, ...] = ("exact", "start", "contain", "regexp")

#: Типы условий, специфичные для отдельных видов целей.
SPECIAL_CONDITION_TYPES: tuple[str, ...] = (
    "messenger",
    "file",
    "all_files",
    "search",
    "social",
    "payment_system",
    "action",
    "contain_action",
    "regexp_action",
)

CONDITION_TYPES: tuple[str, ...] = VALUE_CONDITION_TYPES + SPECIAL_CONDITION_TYPES

#: Цели, у которых условия обязательны.
GOAL_TYPES_REQUIRING_CONDITIONS: frozenset[str] = frozenset(
    {"action", "url", "phone", "email", "messenger", "file", "search", "social", "chat"}
)

#: Цели, которым вместо условий нужен числовой параметр.
GOAL_TYPES_REQUIRING_NUMBER: frozenset[str] = frozenset({"number", "visit_duration"})

#: Человеческие названия типов — подсказка агенту и CLI.
GOAL_TYPE_TITLES: dict[str, str] = {
    "action": "JS-событие",
    "url": "Посещение страницы",
    "number": "Количество просмотров",
    "step": "Составная цель",
    "phone": "Клик по номеру телефона",
    "email": "Клик по email",
    "messenger": "Переход в мессенджер",
    "file": "Скачивание файлов",
    "search": "Поиск по сайту",
    "social": "Клик в соцсети",
    "payment_system": "Возврат из платёжной системы",
    "visit_duration": "Продолжительность визита",
    "chat": "Чат",
}


class _Model(BaseModel):
    """База моделей: неизвестные поля сохраняем, пустые строки нормализуем."""

    model_config = ConfigDict(extra="allow", populate_by_name=True, str_strip_whitespace=True)


def _dedupe(values: list[str]) -> list[str]:
    """Убрать повторы, сохранив порядок (агент часто дублирует поля)."""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip()
        if key and key.lower() not in seen:
            seen.add(key.lower())
            result.append(key)
    return result


# --- Счётчики ---------------------------------------------------------------


class Counter(_Model):
    """Счётчик Метрики (фрагмент ответа Management API)."""

    id: int
    name: str | None = None
    status: str | None = None
    type: str | None = None
    first_watch_date: datetime | None = None
    site_age: str | None = None
    webvisor: bool | None = None
    track_links: bool | None = None
    track_text: bool | None = None
    upload_purposes: bool | None = None
    image_url: str | None = None

    @property
    def title(self) -> str:
        """Название или понятный суррогат для вывода."""

        return self.name or f"Счётчик {self.id}"


class CounterBrief(_Model):
    """Краткое представление счётчика для ответов агенту."""

    id: int
    name: str | None = None
    status: str | None = None
    type: str | None = None
    first_watch_date: datetime | None = None

    @classmethod
    def of(cls, counter: Counter) -> CounterBrief:
        """Свернуть полный счётчик до краткого описания."""

        return cls(
            id=counter.id,
            name=counter.name,
            status=counter.status,
            type=counter.type,
            first_watch_date=counter.first_watch_date,
        )


class CountersPage(_Model):
    """Страница списка счётчиков."""

    items: list[Counter] = Field(default_factory=list)
    total: int | None = None

    @property
    def ids(self) -> list[int]:
        return [item.id for item in self.items]


# --- Цели -------------------------------------------------------------------


class GoalCondition(_Model):
    """Условие цели.

    Для целей ``action``/``url``/``phone``/``email`` поле ``type`` принимает
    ``exact|start|contain|regexp``, значение — в поле ``url`` (так исторически
    называется поле API и для имени JS-события, и для номера телефона).

    Для цели ``chat`` официальная схема использует ``field``
    (``chat_answered``/``chat_platform``/``chat_tag``) со значениями
    ``answered``/``platform``/``tag``.
    """

    type: str | None = None
    url: str | None = None
    field: str | None = None
    answered: bool | None = None
    platform: str | None = None
    tag: str | None = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str | None) -> str | None:
        # Чтение устойчиво: Метрика создаёт автоцели с типами условий вне нашего
        # перечисления (например, ``all_social`` у автоцели «соцсети»). Неизвестный
        # тип условия сохраняем как есть, иначе разбор ответа API падает и список
        # целей реального счётчика не читается. Строгая проверка типа условия
        # выполняется при СОЗДАНИИ цели в :meth:`Goal.validate_for_write`.
        return value.strip().lower() if value is not None else None

    @model_validator(mode="after")
    def _has_value(self) -> GoalCondition:
        """Условие должно что-то содержать: оператор со значением или поле чата."""

        if self.type is None and not any(
            v is not None for v in (self.url, self.field, self.answered, self.platform, self.tag)
        ):
            raise ValueError("Условие цели пустое: нужен type/url или поля чата.")
        return self

    def signature(self) -> tuple[Any, ...]:
        """Канонический вид для сравнения и защиты от дублей."""

        return (
            self.type or "",
            (self.url or "").strip().lower(),
            (self.field or "").strip().lower(),
            self.answered,
            (self.platform or "").strip().lower(),
            (self.tag or "").strip().lower(),
        )

    def describe(self) -> str:
        """Человекочитаемое описание условия."""

        if self.field == "chat_platform":
            return f"платформа чата: {self.platform or '?'}"
        if self.field == "chat_answered":
            return "чат отвечен" if self.answered else "чат без ответа"
        if self.field == "chat_tag":
            return f"метка чата: {self.tag or '?'}"
        labels = {
            "exact": "совпадает с",
            "start": "начинается с",
            "contain": "содержит",
            "regexp": "соответствует регулярному выражению",
        }
        label = labels.get(self.type or "", self.type or "условие")
        value = self.url if self.url is not None else ""
        return f"{label} «{value}»" if value else label


class GoalStep(_Model):
    """Шаг составной цели."""

    name: str
    type: str = "url"
    conditions: list[GoalCondition] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        lowered = value.strip().lower()
        if lowered not in GOAL_TYPES:
            raise ValueError(f"Неизвестный тип шага {value!r}.")
        return lowered


class Goal(_Model):
    """Цель Метрики (запрос создания/ответ API)."""

    id: int | None = None
    name: str
    type: str
    default_price: float | None = None
    is_favorite: bool | None = None
    status: str | None = None
    conditions: list[GoalCondition] | None = None
    steps: list[GoalStep] | None = None
    depth: int | None = None
    duration: int | None = None
    hide_phone_number: bool | None = None
    is_retargeting: bool | None = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        # Чтение устойчиво: Метрика создаёт автоцели с типами вне нашего
        # перечисления (``contact_data``, ``contact_data_sent``, ``cdp_order_paid``
        # и т. п. — ``goal_source: "auto"``). Неизвестный тип сохраняем как есть,
        # иначе разбор ответа API падает и агент не может прочитать статистику
        # реального счётчика. Строгая проверка типа выполняется при СОЗДАНИИ цели
        # в :meth:`GoalService.create` — там, где нельзя допустить выдуманного типа.
        return value.strip().lower()

    @field_validator("name")
    @classmethod
    def _name_length(cls, value: str) -> str:
        if not value:
            raise ValueError("Название цели не может быть пустым.")
        if len(value) > GOAL_NAME_MAX_LENGTH:
            raise ValueError(
                f"Название цели длиннее {GOAL_NAME_MAX_LENGTH} символов.",
            )
        return value

    def _all_write_conditions(self) -> list[GoalCondition]:
        """Все условия цели и её шагов — для строгой проверки при создании."""

        conditions: list[GoalCondition] = list(self.conditions or ())
        for step in self.steps or ():
            conditions.extend(step.conditions or ())
        return conditions

    def validate_for_write(self) -> None:
        """Проверить обязательные данные типа перед СОЗДАНИЕМ цели.

        Инварианты относятся к записи, а не к чтению: список целей
        ``GET /counter/{id}/goals`` возвращает сокращённые объекты (например,
        ``phone``-цель без ``conditions``), и жёсткая проверка прямо в модели
        ломала бы разбор реальных ответов. Вызывается из
        :meth:`GoalService.create`. Бросает :class:`ValueError` при нарушении.
        """

        if self.type in GOAL_TYPES_REQUIRING_CONDITIONS and not self.conditions:
            raise ValueError(
                f"Цели типа {self.type!r} нужны условия conditions: "
                f"{GOAL_TYPE_TITLES.get(self.type, self.type)}.",
            )
        # Типы условий при создании должны быть известными (защита от выдуманного
        # типа на записи; чтение устойчиво к неизвестным типам автоцелей).
        for condition in self._all_write_conditions():
            if condition.type is not None and condition.type not in CONDITION_TYPES:
                raise ValueError(
                    f"Неизвестный тип условия {condition.type!r}. "
                    f"Допустимо: {', '.join(CONDITION_TYPES)}.",
                )
        if self.type == "number" and self.depth is None:
            raise ValueError("Цели типа 'number' нужно значение depth — количество просмотров.")
        if self.type == "visit_duration" and self.duration is None:
            raise ValueError("Цели типа 'visit_duration' нужно значение duration — секунд.")
        if self.type == "step" and not self.steps:
            raise ValueError("Составной цели типа 'step' нужны шаги steps.")
        if self.depth is not None and self.depth < 2:
            raise ValueError("depth должен быть не меньше 2 просмотров.")
        if self.duration is not None and self.duration < 1:
            raise ValueError("duration должен быть не меньше 1 секунды.")

    @property
    def title(self) -> str:
        """Тип по-русски."""

        return GOAL_TYPE_TITLES.get(self.type, self.type)

    def signature(self) -> tuple[Any, ...]:
        """Канонический отпечаток цели для защиты от дублей.

        Название в подпись не входит: идемпотентность структурная — цель
        считается существующей по типу и существенным параметрам (условия,
        шаги, глубина, длительность), а не по имени.
        """

        conditions = sorted(
            (condition.signature() for condition in self.conditions or ()),
        )
        steps = tuple(
            (step.name.strip().lower(), step.type, sorted(c.signature() for c in step.conditions))
            for step in (self.steps or ())
        )
        return (
            self.type,
            tuple(conditions),
            steps,
            self.depth,
            self.duration,
        )

    def to_request(self) -> dict[str, Any]:
        """Тело для ``POST/PUT``: только заполненные поля."""

        payload: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.conditions is not None:
            payload["conditions"] = [
                condition.model_dump(exclude_none=True) for condition in self.conditions
            ]
        if self.steps is not None:
            payload["steps"] = [step.model_dump(exclude_none=True) for step in self.steps]
        if self.depth is not None:
            payload["depth"] = self.depth
        if self.duration is not None:
            payload["duration"] = self.duration
        if self.default_price is not None:
            payload["default_price"] = self.default_price
        # is_favorite не отправляем: фактический API отвергает поле в PUT/POST
        # (invalid_json, path: goal.is_favorite), хотя openapi его описывает.
        if self.hide_phone_number is not None:
            payload["hide_phone_number"] = self.hide_phone_number
        # is_retargeting не отправляем: фактический API отвергает поле в
        # PUT/POST (invalid_json, path: goal.is_retargeting).
        return payload

    def describe(self) -> str:
        """Описание цели для человека и для ответов агенту."""

        parts = [f"{self.title} — {self.name}"]
        for condition in self.conditions or ():
            parts.append(f"условие: {condition.describe()}")
        if self.depth is not None:
            parts.append(f"глубина: {self.depth} просмотров")
        if self.duration is not None:
            parts.append(f"длительность визита: {self.duration} с")
        if self.steps:
            parts.append(f"шагов: {len(self.steps)}")
        if self.default_price is not None:
            parts.append(f"цена: {self.default_price}")
        return "; ".join(parts)


class GoalsPage(_Model):
    """Список целей счётчика."""

    items: list[Goal] = Field(default_factory=list)

    @property
    def by_name(self) -> dict[str, Goal]:
        """Название (в нижнем регистре) -> цель."""

        return {goal.name.strip().lower(): goal for goal in self.items}


# --- Справочник метрик ------------------------------------------------------


class MetricItem(_Model):
    """Метрика или измерение из ``/stat/v1/metrics``.

    ``metric`` — имя, которое подставляют в параметр ``metrics``/``dimensions``.
    ``segment`` заполнен у сегментированных метрик (``visitors`` по типам
    устройств и т. п.).
    """

    metric: str
    title: str | None = None
    description: str | None = None
    type: str | None = None
    category: str | None = None
    is_complex: bool | None = None
    segment: str | None = None

    def matches(self, query: str) -> bool:
        """Совпадение с запросом человека или агента (нечувствительно к регистру)."""

        needle = " ".join(query.strip().lower().split())
        if not needle:
            return False
        for candidate in (self.metric, self.title or "", self.category or ""):
            haystack = candidate.strip().lower()
            if not haystack:
                continue
            if haystack == needle or needle in haystack or haystack in needle:
                return True
        words = set(needle.split())
        return bool(words) and bool(words & set((self.description or "").lower().split()))


class MetricGroupPage(_Model):
    """Внешний каталог метрик/измерений для сверки псевдонимов.

    Публичный Reports API НЕ отдаёт список метрик по HTTP — справочник
    опубликован только в документации (``stat/attrandmetr/dim_all``). Эта
    модель описывает каталог, который интегратор может получить из внешнего
    источника (собственная БД, внутренний справочник, будущий endpoint), и
    передать в :class:`~yandex_metrika_agent.metrics.MetricDirectory`.

    Метрика группирует метрики: стандартные, экспериментальные, сегменты,
    вычисляемые. Для агента важна не группировка, а возможность ответить
    «такой метрики нет, но есть вот такие».
    """

    counter_id: int | None = None
    standard_metrics: list[MetricItem] = Field(default_factory=list)
    experimental_metrics: list[MetricItem] = Field(default_factory=list)
    standard_segments: list[MetricItem] = Field(default_factory=list)
    calculated_metrics: list[Any] = Field(default_factory=list)
    visibility: bool | None = None

    @property
    def groups(self) -> dict[str, list[MetricItem]]:
        """Группы справочника одним словарём."""

        return {
            "standard_metrics": self.standard_metrics,
            "experimental_metrics": self.experimental_metrics,
            "standard_segments": self.standard_segments,
        }

    @property
    def all_metrics(self) -> list[MetricItem]:
        """Все метрики и измерения одним списком (без сегментов)."""

        return [*self.standard_metrics, *self.experimental_metrics]

    @property
    def all_dimensions(self) -> list[MetricItem]:
        """Все измерения (сегменты)."""

        return list(self.standard_segments)

    @property
    def metric_names(self) -> list[str]:
        return [item.metric for item in self.all_metrics]

    def lookup(self, name: str) -> MetricItem | None:
        """Найти элемент справочника по точному имени метрики/измерения."""

        wanted = (name or "").strip().lower()
        for item in [*self.all_metrics, *self.all_dimensions]:
            if item.metric.lower() == wanted:
                return item
        return None

    def find(self, query: str, *, limit: int = 10) -> list[MetricItem]:
        """Поиск по имени, заголовку, категории и описанию."""

        found: list[MetricItem] = []
        for item in [*self.all_metrics, *self.all_dimensions]:
            if item.matches(query):
                found.append(item)
                if len(found) >= limit:
                    break
        return found

    def titles(self) -> dict[str, str]:
        """Имя метрики -> человекочитаемый заголовок."""

        return {
            item.metric: item.title or item.metric
            for item in [*self.all_metrics, *self.all_dimensions]
        }


# --- Отчёты -----------------------------------------------------------------


class ReportRow(_Model):
    """Строка отчёта ``StaticRow``: значения группировок и метрик.

    Формат подтверждён ответом ``GET /stat/v1/data``: каждое значение
    группировки — объект с обязательным ``name`` и возможными дополнительными
    полями (``id`` и т. п.), метрики — числа в порядке параметра ``metrics``.
    """

    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[float | int | str | None] = Field(default_factory=list)

    @property
    def labels(self) -> list[str]:
        """Человеческие значения измерений строки."""

        labels: list[str] = []
        for dimension in self.dimensions:
            name = dimension.get("name")
            if name is None:
                ident = dimension.get("id")
                name = ident.get("name") if isinstance(ident, dict) else ident
            labels.append(str(name if name is not None else "?"))
        return labels


class ReportQuery(_Model):
    """Эхо исходного запроса в поле ``query`` ответа ``/stat/v1/data``."""

    timezone: str | None = None
    preset: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    sort: list[str] = Field(default_factory=list)
    date1: str | None = None
    date2: str | None = None
    filters: str | None = None
    limit: int | None = None
    offset: int | None = None


class Report(_Model):
    """Ответ ``GET /stat/v1/data`` (официальная схема Reports API).

    Поля соответствуют документации ``.../stat/openapi/data_1``: таблица
    ``data``, плоский массив итогов ``totals``, признаки семплирования
    ``sampled``/``sample_share``, ``total_rows`` и эхо запроса ``query``.
    ``metric_names``/``dimension_names`` Метрика отдаёт в фактических ответах;
    если их нет, сервис подставляет имена из запроса.
    """

    query: ReportQuery | None = None
    data: list[ReportRow] = Field(default_factory=list)
    total_rows: int | None = None
    total_rows_rounded: bool | None = None
    sampled: bool | None = None
    contains_sensitive_data: bool | None = None
    sample_share: float | None = None
    sample_size: int | None = None
    sample_space: int | None = None
    data_lag: int | bool | None = None
    totals: list[float | int | str | None] = Field(default_factory=list)
    metric_names: list[str] = Field(default_factory=list)
    dimension_names: list[str] = Field(default_factory=list)

    @property
    def is_empty_report(self) -> bool:
        """Отчёт пуст (нет строк за период)."""

        return not self.data


class ReportTask(_Model):
    """Задача асинхронного отчёта."""

    id: int | None = None
    status: str | None = None
    counter_id: int | None = None
    created_date: datetime | None = None
    name: str | None = None
    params: dict[str, Any] | None = None


#: Задачи асинхронных отчётов описаны в docs (``POST /stat/v1/async``).


class ReportLimits(_Model):
    """Ограничения формирования отчёта из ``GET /stat/v1/limits``."""

    metrics: int | None = None
    dimensions: int | None = None
    rows: int | None = None
    date1: str | None = None
    date2: str | None = None
    dates_quantity: int | None = None
    download: int | None = None
    download_rows: int | None = None
    concurrent: int | None = None
    is_unlimited: bool | None = None


class ReportCapabilities(_Model):
    """Возможности формирования отчётов для конкретного счётчика."""

    counter_id: int
    limits: ReportLimits | None = None
    visible: list[str] = Field(default_factory=lambda: ["all", "first"])
    extensions: list[str] = Field(
        default_factory=lambda: ["csv", "tsv", "csvn", "csvsemit", "googleads", "yandexads"]
    )

    @property
    def max_rows(self) -> int | None:
        """Сколько строк отдаст обычный запрос отчёта."""

        return self.limits.rows if self.limits else None

    @property
    def max_download_rows(self) -> int | None:
        """Сколько строк отдаст выгрузка (async-задача)."""

        return self.limits.download_rows if self.limits else None


class ReportCommand(_Model):
    """Запрос отчёта в том виде, в каком его формулирует человек или агент.

    Это не дословный параметр API, а намерение: «сколько заявок за прошлую
    неделю». :meth:`to_params` превращает его в параметры ``GET /stat/v1/data``
    (параметры других эндпоинтов — ``/comparison``, ``/bytime`` — здесь не
    хранятся), а :meth:`describe` — в строку пояснения для пользователя.
    """

    counter_id: int
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    date1: date | None = None
    date2: date | None = None
    filters: list[str] = Field(default_factory=list)
    sort_by: list[str] = Field(default_factory=list)
    limit: int | None = None
    offset: int | None = None
    #: Язык значений группировок (``ru``/``en``/``tr``).
    lang: str | None = None
    #: Часовой пояс периода выборки в формате ``±hh:mm``.
    timezone: str | None = None
    #: Включать строки с неопределённым значением первой группировки.
    include_undefined: bool | None = None
    #: Размер выборки (семплирование), например ``1000000``.
    accuracy: int | None = None
    #: Разрешить API увеличить accuracy до рекомендованного.
    proposed_accuracy: bool | None = None
    #: Шаблон отчёта (``sources_summary``, ``goals`` ...).
    preset: str | None = None
    #: Логины клиентов Директа для отчёта «Директ-расходы».
    direct_client_logins: list[str] = Field(default_factory=list)

    @field_validator("metrics", "dimensions", "filters", "sort_by", "direct_client_logins",
                     mode="before")
    @classmethod
    def _as_list(cls, value: Any) -> list[Any]:
        """Разрешить одиночную строку вместо списка."""

        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value)]

    @field_validator("limit", mode="after")
    @classmethod
    def _check_limit(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("limit должен быть больше нуля.")
        return value

    @field_validator("offset", mode="after")
    @classmethod
    def _check_offset(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("offset в Reports API начинается с 1.")
        return value

    @model_validator(mode="after")
    def _check_dates(self) -> ReportCommand:
        if (self.date1 is None) != (self.date2 is None):
            raise ValueError("Нужны обе даты периода: date1 и date2.")
        if self.date1 and self.date2 and self.date2 < self.date1:
            raise ValueError("date2 не может быть раньше date1.")
        return self

    @property
    def period(self) -> str:
        """Период одной строкой для вывода."""

        if not self.date1 or not self.date2:
            return "последние 7 дней (по умолчанию API)"
        if self.date1 == self.date2:
            return self.date1.isoformat()
        return f"{self.date1.isoformat()} — {self.date2.isoformat()}"

    def normalized(self) -> ReportCommand:
        """Копия с очищенными дубликатами и приведёнными списками."""

        return self.model_copy(
            update={
                "metrics": _dedupe(self.metrics),
                "dimensions": _dedupe(self.dimensions),
                "filters": _dedupe(self.filters),
                "sort_by": _dedupe(self.sort_by),
                "direct_client_logins": _dedupe(self.direct_client_logins),
            }
        )

    def to_params(self) -> dict[str, Any]:
        """Собрать параметры ``GET /stat/v1/data``.

        Пустые значения опускаются: Метрика по-разному реагирует на пустые
        строки в параметрах, и лучше их не передавать вовсе.
        """

        command = self.normalized()
        if not command.metrics:
            raise ValueError("Для отчёта нужна хотя бы одна метрика.")
        params: dict[str, Any] = {
            "id": command.counter_id,
            "metrics": ",".join(command.metrics),
        }
        if command.dimensions:
            params["dimensions"] = ",".join(command.dimensions)
        if command.date1 and command.date2:
            params["date1"] = command.date1.isoformat()
            params["date2"] = command.date2.isoformat()
        if command.filters:
            params["filters"] = ";".join(command.filters)
        if command.sort_by:
            params["sort"] = ",".join(command.sort_by)
        if command.limit is not None:
            params["limit"] = command.limit
        if command.offset:
            params["offset"] = command.offset
        if command.lang:
            params["lang"] = command.lang
        if command.timezone:
            params["timezone"] = command.timezone
        if command.include_undefined is not None:
            params["include_undefined"] = "true" if command.include_undefined else "false"
        if command.accuracy is not None:
            params["accuracy"] = command.accuracy
        if command.proposed_accuracy is not None:
            params["proposed_accuracy"] = "true" if command.proposed_accuracy else "false"
        if command.preset:
            params["preset"] = command.preset
        if command.direct_client_logins:
            params["direct_client_logins"] = ",".join(command.direct_client_logins)
        return params

    def describe(self) -> str:
        """Человеческое описание запроса."""

        parts = [f"метрики: {', '.join(self.metrics) or '—'}"]
        if self.dimensions:
            parts.append(f"разбивка: {', '.join(self.dimensions)}")
        parts.append(f"период: {self.period}")
        if self.filters:
            parts.append(f"фильтры: {'; '.join(self.filters)}")
        return "; ".join(parts)


class ComparisonRow(_Model):
    """Сравнение значения измерения в два периода."""

    label: str
    current: float | int | None = None
    previous: float | int | None = None
    delta: float | int | None = None
    delta_percent: float | None = None


class DateRange(_Model):
    """Диапазон дат отчёта."""

    date1: date
    date2: date

    @model_validator(mode="after")
    def _check_order(self) -> DateRange:
        if self.date2 < self.date1:
            raise ValueError("date2 не может быть раньше date1.")
        return self

    @property
    def days(self) -> int:
        """Количество дней включительно."""

        return (self.date2 - self.date1).days + 1

    def shifted_back(self, days: int) -> DateRange:
        """Тот же диапазон, сдвинутый на ``days`` дней в прошлое."""

        from datetime import timedelta

        return DateRange(
            date1=self.date1 - timedelta(days=days),
            date2=self.date2 - timedelta(days=days),
        )


__all__ = [
    "CONDITION_TYPES",
    "GOAL_TYPES",
    "GOAL_TYPES_REQUIRING_CONDITIONS",
    "GOAL_TYPES_REQUIRING_NUMBER",
    "GOAL_TYPE_TITLES",
    "VALUE_CONDITION_TYPES",
    "ComparisonRow",
    "Counter",
    "CounterBrief",
    "CountersPage",
    "DateRange",
    "Goal",
    "GoalCondition",
    "GoalStep",
    "GoalsPage",
    "Report",
    "ReportCapabilities",
    "ReportCommand",
    "ReportLimits",
    "ReportQuery",
    "ReportRow",
    "ReportTask",
]
