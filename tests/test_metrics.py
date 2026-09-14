"""Тесты словаря метрик: алиасы, метрики целей, humanize, подсказки."""

from __future__ import annotations

import pytest

from yandex_metrika_agent.errors import ValidationError
from yandex_metrika_agent.metrics import (
    MAX_DIMENSIONS,
    MAX_METRICS,
    MetricDirectory,
    goal_conversion,
    goal_reaches,
    is_api_identifier,
)


def test_metric_alias_to_api() -> None:
    directory = MetricDirectory()
    assert directory.metric("visits") == "ym:s:visits"
    assert directory.metric("users") == "ym:s:users"
    assert directory.metric("bounce_rate") == "ym:s:bounceRate"
    assert directory.metric("session_duration") == "ym:s:avgVisitDurationSeconds"


def test_ready_identifier_passes_through() -> None:
    directory = MetricDirectory()
    assert directory.metric("ym:s:visits") == "ym:s:visits"
    assert directory.metric("ym:pv:URLPath") == "ym:pv:URLPath"


def test_dimension_alias_to_api() -> None:
    directory = MetricDirectory()
    assert directory.dimension("date") == "ym:s:date"
    assert directory.dimension("traffic_source") == "ym:s:trafficSource"
    assert directory.dimension("page") == "ym:s:page"


def test_unknown_metric_rejected_with_suggestions() -> None:
    directory = MetricDirectory()
    with pytest.raises(ValidationError) as exc:
        directory.metric("visits_typo")
    assert exc.value.details.get("suggestions") is not None or exc.value.message


def test_dedupe_preserves_order() -> None:
    directory = MetricDirectory()
    assert directory.metrics(["visits", "users", "visits"]) == ["ym:s:visits", "ym:s:users"]


def test_humanize_reverse_translation() -> None:
    directory = MetricDirectory()
    assert directory.humanize("ym:s:visits") == "visits"
    assert directory.humanize("ym:s:trafficSource") == "traffic_source"


def test_goal_metric_helpers() -> None:
    assert goal_reaches(42) == "ym:s:goal42reaches"
    assert goal_conversion(42) == "ym:s:goal42conversionRate"
    assert is_api_identifier(goal_reaches(42))


def test_goal_metric_invalid_id() -> None:
    with pytest.raises(ValidationError):
        goal_reaches(0)


def test_title_for_goal_metric() -> None:
    directory = MetricDirectory()
    assert "Цель 42" in directory.title("ym:s:goal42reaches")


def test_limits_constants() -> None:
    assert MAX_METRICS == 20
    assert MAX_DIMENSIONS == 10


def test_is_api_identifier() -> None:
    assert is_api_identifier("ym:s:visits")
    assert not is_api_identifier("visits")
    assert not is_api_identifier("ym:s:")
