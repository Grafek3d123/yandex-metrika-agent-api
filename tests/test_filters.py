"""Тесты DSL фильтров: операторы, NOT/скобки, экранирование, лимиты."""

from __future__ import annotations

import pytest

from yandex_metrika_agent.errors import ValidationError
from yandex_metrika_agent.filters import Filter, Logic, Operator, as_filters, render_filters
from yandex_metrika_agent.metrics import MetricDirectory


def _f(field: str, op: Operator, value: object) -> Filter:
    return Filter(field=field, operator=op, value=value)


# --- Операторы ---------------------------------------------------------------


def test_equals_operator() -> None:
    assert render_filters([_f("ym:s:trafficSource", Operator.EQUALS, "ad")]) == (
        "ym:s:trafficSource=='ad'"
    )


def test_not_equals_operator() -> None:
    assert render_filters([_f("ym:s:trafficSource", Operator.NOT_EQUALS, "ad")]) == (
        "ym:s:trafficSource!='ad'"
    )


def test_contains_operators() -> None:
    assert render_filters([_f("ym:pv:URL", Operator.CONTAINS, "thank")]) == "ym:pv:URL=@'thank'"
    assert render_filters([_f("ym:pv:URL", Operator.NOT_CONTAINS, "test")]) == "ym:pv:URL!@'test'"


def test_starts_with_uses_official_star_syntax() -> None:
    assert render_filters([_f("ym:pv:URL", Operator.STARTS_WITH, "/blog")]) == "ym:pv:URL=*'/blog*'"


def test_regexp_operator() -> None:
    assert render_filters([_f("ym:pv:URL", Operator.REGEXP, "^/a")]) == "ym:pv:URL=~'^/a'"


def test_numeric_comparison() -> None:
    assert render_filters([_f("ym:s:visits", Operator.GREATER, 10)]) == "ym:s:visits>10"
    assert render_filters([_f("ym:s:visits", Operator.LESS_OR_EQUAL, 2.5)]) == "ym:s:visits<=2.5"


def test_numeric_comparison_requires_number() -> None:
    with pytest.raises(ValidationError):
        render_filters([_f("ym:s:visits", Operator.GREATER, "abc")])


def test_in_and_not_in_operators() -> None:
    f = _f("ym:s:trafficSource", Operator.IN, ["ad", "organic"])
    assert render_filters([f]) == "ym:s:trafficSource=.('ad','organic')"
    f2 = _f("ym:s:trafficSource", Operator.NOT_IN, ["ad"])
    assert render_filters([f2]) == "ym:s:trafficSource!.('ad')"


def test_escape_single_quotes() -> None:
    assert render_filters([_f("ym:pv:URL", Operator.EQUALS, "o'clock")]) == "ym:pv:URL=='o\\'clock'"


# --- Человеческие имена ------------------------------------------------------


def test_human_field_translated_via_directory() -> None:
    directory = MetricDirectory()
    f = _f("traffic_source", Operator.EQUALS, "organic")
    rendered = render_filters([f], directory=directory)
    assert rendered == "ym:s:trafficSource=='organic'"


# --- Логика и скобки ---------------------------------------------------------


def test_and_logic_joined() -> None:
    filters = [
        _f("ym:s:trafficSource", Operator.EQUALS, "ad"),
        _f("ym:s:isNewUser", Operator.EQUALS, "Yes"),
    ]
    assert render_filters(filters) == (
        "ym:s:trafficSource=='ad' AND ym:s:isNewUser=='Yes'"
    )


def test_or_logic_joined() -> None:
    filters = [
        _f("ym:s:trafficSource", Operator.EQUALS, "ad"),
        _f("ym:s:visits", Operator.GREATER, 5),
    ]
    rendered = render_filters(filters, logic=Logic.OR)
    assert " OR " in rendered


def test_nested_group_wrapped_in_parentheses() -> None:
    filters: list[object] = [
        _f("ym:s:trafficSource", Operator.EQUALS, "ad"),
        [
            _f("ym:s:isNewUser", Operator.EQUALS, "Yes"),
            _f("ym:s:visits", Operator.GREATER, 5),
        ],
    ]
    rendered = render_filters(filters)  # type: ignore[arg-type]
    assert rendered == (
        "ym:s:trafficSource=='ad' AND "
        "(ym:s:isNewUser=='Yes' AND ym:s:visits>5)"
    )


def test_ready_string_passes_through() -> None:
    assert render_filters(["ym:s:visits>100"]) == "ym:s:visits>100"


def test_empty_filters_return_empty_string() -> None:
    assert render_filters(None) == ""
    assert render_filters([]) == ""


def test_more_than_20_conditions_rejected() -> None:
    filters: list[Filter] = [_f(f"ym:s:visits{i}", Operator.EQUALS, i) for i in range(21)]
    with pytest.raises(ValidationError):
        render_filters(filters)


# --- as_filters --------------------------------------------------------------


def test_as_filters_from_agent_dict() -> None:
    result = as_filters({"field": "traffic_source", "operator": "equals", "value": "ad"})
    assert len(result) == 1
    assert isinstance(result[0], Filter)
    rendered = render_filters(result, directory=MetricDirectory())
    assert rendered == "ym:s:trafficSource=='ad'"


def test_operator_parse_synonyms() -> None:
    assert Operator.parse("=") is Operator.EQUALS
    assert Operator.parse("contains") is Operator.CONTAINS
    assert Operator.parse("not_in") is Operator.NOT_IN
    with pytest.raises(ValidationError):
        Operator.parse("nope")
