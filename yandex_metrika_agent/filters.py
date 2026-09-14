"""DSL фильтров Reports API.

Метрика использует собственный синтаксис параметра ``filters``
(``https://yandex.com/dev/metrika/ru/stat/``):

* ``field=='value'`` — равенство;
* ``field!='value'`` — неравенство;
* ``field@'substr'`` — содержит подстроку;
* ``field!.@'substr'`` — не содержит;
* ``field=.('a','b')`` — значение из перечисления;
* ``field>n`` — сравнение числа (например ``ym:s:pageViews>5``);
* ``field=n`` / ``field!n`` — пусто / не пусто;
* условия соединяются ``AND`` (и, реже, ``OR``), допустимы скобки.

Агенту нельзя давать свободно собирать такую строку — это главный источник
ошибок. Здесь есть типизированный :class:`Filter`: вызывающий описывает поле,
оператор и значение, а :func:`render_filters` превращает это в корректную
строку ``filters`` и попутно переводит человеческие имена полей в
``ym:s:...``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from yandex_metrika_agent.errors import ValidationError
from yandex_metrika_agent.metrics import MetricDirectory

#: Значение-заглушка для операторов «пусто/не пусто».
_NULL_TOKEN = "n"


class Operator(str, Enum):
    """Операторы фильтра, доступные агенту."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    GREATER = "greater"
    GREATER_OR_EQUAL = "greater_or_equal"
    LESS = "less"
    LESS_OR_EQUAL = "less_or_equal"
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"

    @classmethod
    def parse(cls, value: object) -> Operator:
        """Разобрать оператор из строки (с человеческими синонимами)."""

        if isinstance(value, Operator):
            return value
        text = str(value or "").strip().lower()
        synonyms = {
            "=": cls.EQUALS,
            "==": cls.EQUALS,
            "eq": cls.EQUALS,
            "равно": cls.EQUALS,
            "!=": cls.NOT_EQUALS,
            "<>": cls.NOT_EQUALS,
            "ne": cls.NOT_EQUALS,
            "не_равно": cls.NOT_EQUALS,
            "не равно": cls.NOT_EQUALS,
            "@": cls.CONTAINS,
            "contains": cls.CONTAINS,
            "содержит": cls.CONTAINS,
            "!@": cls.NOT_CONTAINS,
            "!contains": cls.NOT_CONTAINS,
            "не содержит": cls.NOT_CONTAINS,
            ">": cls.GREATER,
            ">=": cls.GREATER_OR_EQUAL,
            "<": cls.LESS,
            "<=": cls.LESS_OR_EQUAL,
            "in": cls.IN,
            "oneof": cls.IN,
            "не в": cls.NOT_IN,
            "null": cls.IS_NULL,
            "is null": cls.IS_NULL,
            "пусто": cls.IS_NULL,
            "notnull": cls.IS_NOT_NULL,
            "is not null": cls.IS_NOT_NULL,
            "не пусто": cls.IS_NOT_NULL,
        }
        key = synonyms.get(text, text.replace(" ", "_"))
        try:
            return cls(key)
        except ValueError as exc:
            raise ValidationError(
                f"Неизвестный оператор фильтра: {value!r}.",
                details={"operator": str(value), "allowed": [op.value for op in cls]},
            ) from exc


#: Операторы, которым нужно ровно одно скалярное значение.
_SCALAR_OPS = frozenset(
    {
        Operator.EQUALS,
        Operator.NOT_EQUALS,
        Operator.CONTAINS,
        Operator.NOT_CONTAINS,
        Operator.STARTS_WITH,
        Operator.GREATER,
        Operator.GREATER_OR_EQUAL,
        Operator.LESS,
        Operator.LESS_OR_EQUAL,
    }
)

#: Операторы, которым нужно перечисление значений.
_MULTI_OPS = frozenset({Operator.IN, Operator.NOT_IN})

#: Операторы без значения.
_VALUELESS_OPS = frozenset({Operator.IS_NULL, Operator.IS_NOT_NULL})


def _escape(value: str) -> str:
    """Экранировать одинарные кавычки и обратные слеши внутри значения.

    Метрика разбирает значение между одинарными кавычками; внутренняя кавычка
    экранируется обратным слешем.
    """

    return value.replace("\\", "\\\\").replace("'", "\\'")


@dataclass(frozen=True)
class Filter:
    """Одно условие фильтра.

    Args:
        field: человеческое имя измерения (``traffic_source``) или ``ym:s:...``.
        operator: :class:`Operator` или его строковый синоним.
        value: скаляр, список (для ``in``/``not_in``) или ``None`` (для is_null).
    """

    field: str
    operator: Operator
    value: object = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "operator", Operator.parse(self.operator))
        op = self.operator
        if op in _SCALAR_OPS:
            if self.value is None or (isinstance(self.value, str) and not self.value):
                raise ValidationError(
                    f"Оператору {op.value} нужно значение.",
                    details={"field": self.field, "operator": op.value},
                )
            if isinstance(self.value, (list, tuple, set)):
                raise ValidationError(
                    f"Оператор {op.value} принимает одно значение, не список.",
                    details={"field": self.field, "operator": op.value},
                )
        elif op in _MULTI_OPS:
            if not isinstance(self.value, (list, tuple, set)) or not self.value:
                raise ValidationError(
                    f"Оператору {op.value} нужен непустой список значений.",
                    details={"field": self.field, "operator": op.value},
                )
        elif op in _VALUELESS_OPS:
            if self.value not in (None, ""):
                raise ValidationError(
                    f"Оператор {op.value} не принимает значение.",
                    details={"field": self.field, "operator": op.value},
                )

    # --- Рендер --------------------------------------------------------------

    def render(self, directory: MetricDirectory | None = None) -> str:
        """Собрать строку условия; человеческое имя поля переводит ``directory``."""

        resolver = directory or MetricDirectory()
        field = resolver.dimension(self.field, allow_unknown=True)
        op = self.operator
        if op is Operator.EQUALS:
            return f"{field}=='{_escape(str(self.value))}'"
        if op is Operator.NOT_EQUALS:
            return f"{field}!='{_escape(str(self.value))}'"
        if op is Operator.CONTAINS:
            return f"{field}@'{_escape(str(self.value))}'"
        if op is Operator.NOT_CONTAINS:
            return f"{field}!.@'{_escape(str(self.value))}'"
        if op is Operator.STARTS_WITH:
            # У Метрики нет отдельного «начинается с» — моделируем регуляркой.
            return f"{field}=~'^{_escape(str(self.value))}'"
        if op in {Operator.GREATER, Operator.GREATER_OR_EQUAL, Operator.LESS, Operator.LESS_OR_EQUAL}:
            symbol = {
                Operator.GREATER: ">",
                Operator.GREATER_OR_EQUAL: ">=",
                Operator.LESS: "<",
                Operator.LESS_OR_EQUAL: "<=",
            }[op]
            return f"{field}{symbol}{_number(self.value, field=field)}"
        if op is Operator.IN:
            return f"{field}=.({self._joined_values()})"
        if op is Operator.NOT_IN:
            return f"{field}!=.({self._joined_values()})"
        if op is Operator.IS_NULL:
            return f"{field}={_NULL_TOKEN}"
        return f"{field}!{_NULL_TOKEN}"

    def _joined_values(self) -> str:
        values = self.value if isinstance(self.value, (list, tuple, set)) else [self.value]
        return ",".join(f"'{_escape(str(item))}'" for item in values)


class Logic(str, Enum):
    """Соединитель между условиями."""

    AND = "AND"
    OR = "OR"


def render_filters(
    filters: Iterable[Filter | str] | None,
    *,
    directory: MetricDirectory | None = None,
    logic: Logic = Logic.AND,
) -> str:
    """Собрать параметр ``filters`` из списка условий.

    Готовые строки (уже в синтаксисе Метрики) проходят как есть — это нужно,
    чтобы продвинутые вызывающие могли добавить скобочную группу. Пусто —
    пустая строка.
    """

    if not filters:
        return ""
    parts: list[str] = []
    for item in filters:
        if isinstance(item, str):
            text = item.strip()
            if text:
                parts.append(text)
        elif isinstance(item, Filter):
            parts.append(item.render(directory))
        else:
            raise ValidationError(
                "Фильтр должен быть Filter или строкой.",
                details={"type": type(item).__name__},
            )
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    joiner = f" {logic.value} "
    return joiner.join(parts)


def _number(value: object, *, field: str) -> str:
    """Проверить, что значение для числового сравнения — число."""

    try:
        number = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Для сравнения нужно число.",
            details={"field": field, "value": str(value)},
        ) from exc
    if number.is_integer():
        return str(int(number))
    return str(number)


def as_filters(value: Sequence[object] | object | None) -> list[Filter | str]:
    """Привести вход (Filter/строку/dict/список) к списку фильтров.

    Словарь вида ``{"field": ..., "operator": ..., "value": ...}`` — это то,
    что отдаёт AI-агент в JSON инструмента: здесь он превращается в Filter.
    """

    if value is None:
        return []
    if isinstance(value, (Filter, str)):
        return [value]
    if isinstance(value, dict):
        return [_from_mapping(value)]
    if isinstance(value, (list, tuple, set)):
        result: list[Filter | str] = []
        for item in value:
            result.extend(as_filters(item))
        return result
    raise ValidationError(
        "Не удалось разобрать фильтр.",
        details={"value": str(value)},
    )


def _from_mapping(mapping: dict[str, object]) -> Filter:
    field = mapping.get("field")
    operator = mapping.get("operator")
    if not field or operator is None:
        raise ValidationError(
            "Фильтру нужны field и operator.",
            details={"keys": sorted(mapping)},
        )
    return Filter(field=str(field), operator=Operator.parse(operator), value=mapping.get("value"))


__all__ = [
    "Filter",
    "Logic",
    "Operator",
    "as_filters",
    "render_filters",
]
