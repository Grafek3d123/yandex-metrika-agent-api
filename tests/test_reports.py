"""Тесты ReportService на мок-ответах Reports API (respx)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from yandex_metrika_agent.errors import ValidationError
from yandex_metrika_agent.reports import ReportService

DATA_URL = "https://api-metrika.yandex.net/stat/v1/data"


def _report_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": {
            "metrics": ["ym:s:visits"],
            "dimensions": [],
            "date1": "2026-09-01",
            "date2": "2026-09-07",
        },
        "data": [],
        "totals": [1234],
        "sampled": False,
        "total_rows": 0,
        "metric_names": ["ym:s:visits"],
        "dimension_names": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio()
@respx.mock
async def test_get_report_translates_human_names(client: object) -> None:
    route = respx.get(DATA_URL).mock(
        return_value=httpx.Response(200, json=_report_payload())
    )
    service = ReportService(client)  # type: ignore[arg-type]
    report = await service.get_report(
        {"counter_id": 441, "metrics": ["visits"], "date1": "2026-09-01", "date2": "2026-09-07"}
    )
    assert report.totals == [1234]
    params = route.calls[0].request.url.params
    assert params["metrics"] == "ym:s:visits"
    assert params["id"] == "441"


@pytest.mark.asyncio()
@respx.mock
async def test_get_report_rows_as_dicts(client: object) -> None:
    respx.get(DATA_URL).mock(
        return_value=httpx.Response(
            200,
            json=_report_payload(
                data=[
                    {
                        "dimensions": [{"name": "2026-09-01"}],
                        "metrics": [100],
                    }
                ],
                dimension_names=["ym:s:date"],
                totals=[100],
            ),
        )
    )
    service = ReportService(client)  # type: ignore[arg-type]
    report = await service.get_report(
        {"counter_id": 441, "metrics": ["visits"], "dimensions": ["date"]}
    )
    rows = service.rows_as_dicts(report)
    assert rows == [{"date": "2026-09-01", "visits": 100}]


@pytest.mark.asyncio()
@respx.mock
async def test_get_traffic_summary(client: object) -> None:
    respx.get(DATA_URL).mock(
        return_value=httpx.Response(
            200,
            json=_report_payload(
                metric_names=[
                    "ym:s:visits",
                    "ym:s:users",
                    "ym:s:pageviews",
                    "ym:s:bounceRate",
                    "ym:s:avgVisitDurationSeconds",
                    "ym:s:newUsers",
                ],
                totals=[100, 80, 300, 25.5, 120, 10],
            ),
        )
    )
    service = ReportService(client)  # type: ignore[arg-type]
    summary = await service.get_traffic(441, date_from="2026-09-01", date_to="2026-09-07")
    assert summary["visits"] == 100
    assert summary["users"] == 80
    assert summary["bounce_rate"] == 25.5


@pytest.mark.asyncio()
@respx.mock
async def test_get_sources_sorted(client: object) -> None:
    respx.get(DATA_URL).mock(
        return_value=httpx.Response(
            200,
            json=_report_payload(
                data=[
                    {"dimensions": [{"name": "organic"}], "metrics": [500, 400]},
                    {"dimensions": [{"name": "ad"}], "metrics": [200, 150]},
                ],
                dimension_names=["ym:s:trafficSource"],
                metric_names=["ym:s:visits", "ym:s:users"],
                totals=[700, 550],
            ),
        )
    )
    service = ReportService(client)  # type: ignore[arg-type]
    rows = await service.get_sources(441)
    assert rows[0]["traffic_source"] == "organic"
    assert rows[0]["visits"] == 500


@pytest.mark.asyncio()
@respx.mock
async def test_get_goal_stats(client: object) -> None:
    respx.get(DATA_URL).mock(
        return_value=httpx.Response(
            200,
            json=_report_payload(
                metric_names=[
                    "ym:s:visits",
                    "ym:s:users",
                    "ym:s:goal55reaches",
                    "ym:s:goal55conversionRate",
                ],
                totals=[1000, 800, 50, 5.0],
            ),
        )
    )
    service = ReportService(client)  # type: ignore[arg-type]
    stats = await service.get_goal_stats(441, 55)
    assert stats["goal_reaches"] == 50
    assert stats["goal_conversion_rate"] == 5.0
    assert stats["visits"] == 1000


@pytest.mark.asyncio()
@respx.mock
async def test_compare_periods(client: object) -> None:
    respx.get(DATA_URL).mock(
        side_effect=[
            httpx.Response(200, json=_report_payload(totals=[100])),
            httpx.Response(200, json=_report_payload(totals=[150])),
        ]
    )
    service = ReportService(client)  # type: ignore[arg-type]
    rows = await service.compare_periods(
        441,
        period_a=("2026-08-01", "2026-08-07"),
        period_b=("2026-09-01", "2026-09-07"),
    )
    assert rows[0].previous == 100
    assert rows[0].current == 150
    assert rows[0].delta == 50
    assert rows[0].delta_percent == 50.0


@pytest.mark.asyncio()
@respx.mock
async def test_mixed_prefixes_rejected(client: object) -> None:
    service = ReportService(client)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        await service.get_report(
            {"counter_id": 441, "metrics": ["ym:s:visits"], "dimensions": ["ym:pv:URL"]}
        )


@pytest.mark.asyncio()
@respx.mock
async def test_unknown_metric_rejected(client: object) -> None:
    service = ReportService(client)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        await service.get_report({"counter_id": 441, "metrics": ["not_a_metric"]})


# --- B1 (Task06): сортировка по измерению ------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_get_traffic_by_day_sorts_by_date_dimension(client: object) -> None:
    """B1: динамика по дням формирует сортировку по измерению ``date``."""
    route = respx.get(DATA_URL).mock(
        return_value=httpx.Response(
            200,
            json=_report_payload(
                data=[{"dimensions": [{"name": "2026-09-01"}], "metrics": [100, 80]}],
                dimension_names=["ym:s:date"],
                metric_names=["ym:s:visits", "ym:s:users"],
                totals=[100, 80],
            ),
        )
    )
    service = ReportService(client)  # type: ignore[arg-type]
    rows = await service.get_traffic_by_day(441, date_from="2026-09-01", date_to="2026-09-07")
    params = route.calls[0].request.url.params
    assert params["sort"] == "ym:s:date"
    assert params["dimensions"] == "ym:s:date"
    assert rows == [{"date": "2026-09-01", "visits": 100, "users": 80}]


@pytest.mark.asyncio()
@respx.mock
async def test_get_report_sorts_by_dimension(client: object) -> None:
    """B1: ``get_report`` с dimensions=["date"] и sort=["date"] проходит валидацию."""
    route = respx.get(DATA_URL).mock(
        return_value=httpx.Response(200, json=_report_payload(dimension_names=["ym:s:date"]))
    )
    service = ReportService(client)  # type: ignore[arg-type]
    await service.get_report(
        {"counter_id": 441, "metrics": ["visits"], "dimensions": ["date"], "sort_by": ["date"]}
    )
    assert route.calls[0].request.url.params["sort"] == "ym:s:date"


@pytest.mark.asyncio()
@respx.mock
async def test_sort_by_metric_still_works(client: object) -> None:
    """Регресс: сортировка по метрике (со знаком) сохранена."""
    route = respx.get(DATA_URL).mock(
        return_value=httpx.Response(200, json=_report_payload())
    )
    service = ReportService(client)  # type: ignore[arg-type]
    await service.get_report(
        {"counter_id": 441, "metrics": ["visits"], "sort_by": ["-visits"]}
    )
    assert route.calls[0].request.url.params["sort"] == "-ym:s:visits"


@pytest.mark.asyncio()
@respx.mock
async def test_unknown_sort_token_rejected(client: object) -> None:
    """Неизвестный токен сортировки (ни метрика, ни измерение) → ValidationError."""
    service = ReportService(client)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        await service.get_report(
            {"counter_id": 441, "metrics": ["visits"], "sort_by": ["not_a_sort"]}
        )

