"""DSL фильтров Reports API.

Операторы проверены по официальному списку
``https://yandex.com/dev/metrika/ru/stat/relations``:

* ``field=='value'`` — равенство;
* ``field!='value'`` — неравенство;
* ``field=@'substr'`` — является подстрокой;
* ``field!@'substr'`` — не является подстрокой;
* ``field=*'prefix*'`` — «равно с поиском по ``*``» (шаблон; «начинается с»
  выражается префиксом со звёздочкой);
* ``field=~'re'`` / ``field!~'re'`` — регулярное выражение / не попадает;
* ``field=.('a','b')`` — встречается среди значений (до 100 значений);
* ``field!.('a','b')`` — не встречается среди значений;
* ``field>N``, ``field>=N``, ``field<N``, ``field<=N`` — сравнения;
* условия соединяются ``AND``/``OR``, есть унарный ``NOT`` и скобки
  (``https://yandex.com/dev/metrika/ru/stat/segmentation``).

Оператора «пусто/не пусто» в актуальном API нет и реализован он не будет:
неопределённые значения группировок — это конкретные значения (например
``undefined`` у источников трафика), а их попадание в отчёт регулируется
параметром ``include_undefined``.

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


class Operator(str, Enum):
    """Операторы фильтра, доступные агенту (официальный список Метрики)."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    REGEXP = "regexp"
    GREATER = "greater"
    GREATER_OR_EQUAL = "greater_or_equal"
    LESS = "less"
    LESS_OR_EQUAL = "less_or_equal"
    IN = "in"
    NOT_IN = "not_in"

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
            "=@": cls.CONTAINS,
            "contains": cls.CONTAINS,
            "содержит": cls.CONTAINS,
            "подстрока": cls.CONTAINS,
            "!@": cls.NOT_CONTAINS,
            "!contains": cls.NOT_CONTAINS,
            "не_содержит": cls.NOT_CONTAINS,
            "не содержит": cls.NOT_CONTAINS,
            "=*": cls.STARTS_WITH,
            "starts_with": cls.STARTS_WITH,
            "starts with": cls.STARTS_WITH,
            "начинается": cls.STARTS_WITH,
            "шаблон": cls.STARTS_WITH,
            "=~": cls.REGEXP,
            "regex": cls.REGEXP,
            "регулярное": cls.REGEXP,
            ">": cls.GREATER,
            ">=": cls.GREATER_OR_EQUAL,
            "<": cls.LESS,
            "<=": cls.LESS_OR_EQUAL,
            "in": cls.IN,
            "oneof": cls.IN,
            "=.": cls.IN,
            "в_списке": cls.IN,
            "не_в": cls.NOT_IN,
            "!." : cls.NOT_IN,
            "not_in": cls.NOT_IN,
            "not in": cls.NOT_IN,
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
        Operator.REGEXP,
        Operator.GREATER,
        Operator.GREATER_OR_EQUAL,
        Operator.LESS,
        Operator.LESS_OR_EQUAL,
    }
)

#: Операторы, которым нужно перечисление значений.
_MULTI_OPS = frozenset({Operator.IN, Operator.NOT_IN})


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
        value: скаляр или список (для ``in``/``not_in``).
        negate: обернуть условие в официальный унарный ``NOT(...)``.
    """

    field: str
    operator: Operator
    value: object = None
    negate: bool = False

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
            if len(self.value) > 100:
                raise ValidationError(
                    "В одном условии фильтрации не больше 100 значений.",
                    details={"field": self.field, "operator": op.value, "count": len(self.value)},
                )

    # --- Рендер --------------------------------------------------------------

    def render(self, directory: MetricDirectory | None = None) -> str:
        """Собрать строку условия; человеческое имя поля переводит ``directory``.

        При ``negate=True`` условие оборачивается официальным
        ``NOT(...)`` (унарный оператор из документации по сегментации).
        """

        condition = self._render_condition(directory)
        return f"NOT({condition})" if self.negate else condition

    def _render_condition(self, directory: MetricDirectory | None = None) -> str:
        resolver = directory or MetricDirectory()
        field = resolver.dimension(self.field, allow_unknown=True)
        op = self.operator
        if op is Operator.EQUALS:
            return f"{field}=='{_escape(str(self.value))}'"
        if op is Operator.NOT_EQUALS:
            return f"{field}!='{_escape(str(self.value))}'"
        if op is Operator.CONTAINS:
            return f"{field}=@'{_escape(str(self.value))}'"
        if op is Operator.NOT_CONTAINS:
            return f"{field}!@'{_escape(str(self.value))}'"
        if op is Operator.STARTS_WITH:
            # Официальный оператор «равно с поиском по *»: префикс + шаблон.
            return f"{field}=*'{_escape(str(self.value))}*'"
        if op is Operator.REGEXP:
            return f"{field}=~'{_escape(str(self.value))}'"
        if op in {
            Operator.GREATER,
            Operator.GREATER_OR_EQUAL,
            Operator.LESS,
            Operator.LESS_OR_EQUAL,
        }:
            symbol = {
                Operator.GREATER: ">",
                Operator.GREATER_OR_EQUAL: ">=",
                Operator.LESS: "<",
                Operator.LESS_OR_EQUAL: "<=",
            }[op]
            return f"{field}{symbol}{_number(self.value, field=field)}"
        if op is Operator.IN:
            return f"{field}=.({self._joined_values()})"
        return f"{field}!.({self._joined_values()})"

    def _joined_values(self) -> str:
        values = self.value if isinstance(self.value, (list, tuple, set)) else [self.value]
        return ",".join(f"'{_escape(str(item))}'" for item in values)


class Logic(str, Enum):
    """Соединитель между условиями."""

    AND = "AND"
    OR = "OR"


def render_filters(
    filters: Iterable[Filter | str | Sequence[Filter | str]] | None,
    *,
    directory: MetricDirectory | None = None,
    logic: Logic = Logic.AND,
) -> str:
    """Собрать параметр ``filters`` из списка условий.

    Args:
        filters: условия. Вложенный список/кортеж — скобочная группа с тем же
            ``logic`` внутри: ``[a, [b, c]]`` при AND даёт ``a AND (b AND c)``.
            Готовые строки (уже в синтаксисе Метрики) проходят как есть —
            продвинутый путь для конструкций, которых нет в DSL.
        directory: словарь для перевода человеческих имён полей.
        logic: соединитель между условиями верхнего уровня.

    Ограничения Метрики: до 20 фильтров, длина строки до 10 000 символов.
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
        elif isinstance(item, (list, tuple)):
            group = render_filters(item, directory=directory, logic=logic)
            if group:
                parts.append(group if _is_group(group) else f"({group})")
        else:
            raise ValidationError(
                "Фильтр должен быть Filter, строкой или списком.",
                details={"type": type(item).__name__},
            )
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    joiner = f" {logic.value} "
    joined = joiner.join(parts)
    if len(parts) > 20:
        raise ValidationError(
            "В фильтре не больше 20 условий.",
            details={"count": len(parts)},
        )
    if len(joined) > 10_000:
        raise ValidationError(
            "Строка фильтра длиннее 10 000 символов.",
            details={"length": len(joined)},
        )
    return joined


def _is_group(text: str) -> bool:
    """Уже готовая скобочная группа: ``(a OR b) AND c`` не оборачиваем второй раз."""

    return text.startswith("(") and text.endswith(")")


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
    return Filter(
        field=str(field),
        operator=Operator.parse(operator),
        value=mapping.get("value"),
        negate=bool(mapping.get("negate", False)),
    )


__all__ = [
    "Filter",
    "Logic",
    "Operator",
    "as_filters",
    "render_filters",
]
