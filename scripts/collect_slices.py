"""Собрать ОДИН срез по счётчику и дописать его в JSON-пакет (щадящий режим).

Запуск:  python scripts/collect_slices.py --counter <ID> --out <site>_full_data.json \
               --slice summary
Каждый вызов делает ровно ОДИН запрос к /stat/v1/data (или management для целей)
и мержит результат в reports/<out>. Пауза между запусками обеспечивается самим
процессом сборки (по одному срезу за раз).

Срезы: summary years months sources source_domains search search_terms
       organic_seg ad_seg organic_by_engine ad_landing referal social
       cities devices start_top start_bounce pages period_now period_prev goals
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.config import load_settings
from yandex_metrika_agent.goals import GoalService
from yandex_metrika_agent.metrics import goal_conversion, goal_reaches

SUMMARY = [
    "ym:s:visits", "ym:s:users", "ym:s:pageviews", "ym:s:hits", "ym:s:bounces",
    "ym:s:bounceRate", "ym:s:avgVisitDurationSeconds", "ym:s:pageDepth", "ym:s:newUsers",
]
TREND = [
    "ym:s:visits", "ym:s:users", "ym:s:pageviews", "ym:s:bounceRate",
    "ym:s:avgVisitDurationSeconds", "ym:s:newUsers", "ym:s:pageDepth",
]


def parse_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    dims = payload.get("dimension_names") or []
    mets = payload.get("metric_names") or []
    rows: list[dict[str, Any]] = []
    for row in payload.get("data") or []:
        item: dict[str, Any] = {}
        for i, d in enumerate(row.get("dimensions") or []):
            name = d.get("name") if isinstance(d, dict) else d
            if name is None and isinstance(d, dict):
                ident = d.get("id")
                name = ident.get("name") if isinstance(ident, dict) else ident
            item[dims[i] if i < len(dims) else f"dim{i}"] = name
        for i, m in enumerate(row.get("metrics") or []):
            item[mets[i] if i < len(mets) else f"met{i}"] = m
        rows.append(item)
    return rows


class C:
    def __init__(self, client: MetrikaClient, counter: int, d1: date, d2: date) -> None:
        self.client = client
        self.counter = counter
        self.d1 = d1
        self.d2 = d2
        self.notes: list[str] = []

    async def q(
        self, label, metrics, dimensions=None, *, d1=None, d2=None,
        sort=None, limit=100, filters=None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "id": self.counter,
            "date1": (d1 or self.d1).isoformat(),
            "date2": (d2 or self.d2).isoformat(),
            "metrics": ",".join(metrics),
            "limit": limit,
        }
        if dimensions:
            params["dimensions"] = ",".join(dimensions)
        if sort:
            params["sort"] = sort
        if filters:
            params["filters"] = filters
        try:
            payload = await self.client.get_json("/stat/v1/data", params=params)
            if "data" not in payload and payload.get("id") is not None:
                payload = await self.client.transport.wait_async_report(payload["id"])
            return {
                "totals": payload.get("totals"),
                "rows": parse_rows(payload),
                "sampled": payload.get("sampled"),
                "sample_share": payload.get("sample_share"),
            }
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"{label}: {type(exc).__name__}: {str(exc)[:160]}")
            return {"totals": None, "rows": [], "error": str(exc)[:200]}


async def collect_goal_stats(c: C) -> tuple[list, list, int | None]:
    gs = GoalService(c.client)
    try:
        listed = await gs.list(c.counter)
        goals = [{"id": g.id, "name": g.name, "type": g.type} for g in listed if g.id]
    except Exception as exc:  # noqa: BLE001
        c.notes.append(f"goals list: {type(exc).__name__}: {str(exc)[:160]}")
        return [], [], None
    ids = [g["id"] for g in goals]
    reaches: dict[str, Any] = {}
    conv: dict[str, Any] = {}
    for s in range(0, len(ids), 9):
        chunk = ids[s : s + 9]
        r = await c.q(f"reaches{s}", [goal_reaches(i) for i in chunk])
        if r["totals"]:
            for n, t in zip((goal_reaches(i) for i in chunk), r["totals"], strict=False):
                reaches[n] = t
        cv = await c.q(f"conv{s}", [goal_conversion(i) for i in chunk])
        if cv["totals"]:
            for n, t in zip((goal_conversion(i) for i in chunk), cv["totals"], strict=False):
                conv[n] = t
    per = [
        {**g, "reaches": reaches.get(goal_reaches(g["id"])),
         "conversion_rate": conv.get(goal_conversion(g["id"]))}
        for g in goals
    ]
    per.sort(key=lambda g: g["reaches"] or 0, reverse=True)
    top = [g for g in per if (g["reaches"] or 0) > 0][:8]
    top_id = top[0]["id"] if top else None
    return per, top, top_id


async def run(counter: int, out_name: str, slice_name: str, site: str = "") -> int:
    d2 = date.today()
    target = Path("reports") / out_name
    target.parent.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {}
    if target.exists():
        out = json.loads(target.read_text(encoding="utf-8"))
    d1 = date.fromisoformat(out.get("created", "2014-01-01"))
    recent = d2 - timedelta(days=26 * 30)

    async with MetrikaClient.from_settings(
        settings=load_settings(), connection_id="new-account"
    ) as client:
        c = C(client, counter, d1, d2)
        if site:
            out["site"] = site
        out.setdefault("site", "")
        out["counter_id"] = counter
        out.setdefault("created", d1.isoformat())
        out["period"] = {"date1": d1.isoformat(), "date2": d2.isoformat()}

        if slice_name == "goals":
            per, top, top_id = await collect_goal_stats(c)
            out["goals"] = per
            out["top_goals"] = top
            out["top_goal_id"] = top_id
        elif slice_name == "top_goal_months":
            tid = out.get("top_goal_id")
            if tid:
                out["top_goal_months"] = await c.q(
                    "top_goal_months", [goal_reaches(tid), goal_conversion(tid)],
                    ["ym:s:month"], d1=recent, sort="ym:s:month", limit=26,
                )
        elif slice_name == "summary":
            out["summary"] = await c.q("summary", SUMMARY)
        elif slice_name == "years":
            out["years"] = await c.q("years", TREND, ["ym:s:year"], sort="ym:s:year", limit=50)
        elif slice_name == "months":
            out["months"] = await c.q("months", TREND, ["ym:s:month"], d1=recent,
                                      sort="ym:s:month", limit=26)
        elif slice_name == "sources":
            tid = out.get("top_goal_id")
            m = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:avgVisitDurationSeconds"]
            if tid:
                m += [goal_reaches(tid), goal_conversion(tid)]
            out["sources"] = await c.q("sources", m, ["ym:s:trafficSourceName"],
                                       sort="-ym:s:visits", limit=20)
        elif slice_name == "source_domains":
            out["source_domains"] = await c.q("source_domains", ["ym:s:visits", "ym:s:bounceRate"],
                                              ["ym:s:trafficSource"], sort="-ym:s:visits", limit=20)
        elif slice_name == "search":
            out["search"] = await c.q("search", ["ym:s:visits", "ym:s:bounceRate"],
                                      ["ym:s:searchEngineName"], sort="-ym:s:visits", limit=10)
        elif slice_name == "search_terms":
            out["search_terms"] = await c.q("search_terms", ["ym:s:visits", "ym:s:bounceRate"],
                                            ["ym:s:searchPhrase"], sort="-ym:s:visits", limit=25)
        elif slice_name == "organic_seg":
            tid = out.get("top_goal_id")
            m = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:avgVisitDurationSeconds"]
            if tid:
                m += [goal_reaches(tid), goal_conversion(tid)]
            out["organic_seg"] = await c.q("organic_seg", m, filters="ym:s:trafficSource=='organic'")
        elif slice_name == "ad_seg":
            tid = out.get("top_goal_id")
            m = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:avgVisitDurationSeconds"]
            if tid:
                m += [goal_reaches(tid), goal_conversion(tid)]
            out["ad_seg"] = await c.q("ad_seg", m, filters="ym:s:trafficSource=='ad'")
        elif slice_name == "organic_by_engine":
            out["organic_by_engine"] = await c.q(
                "organic_by_engine", ["ym:s:visits", "ym:s:bounceRate"], ["ym:s:searchEngineName"],
                sort="-ym:s:visits", limit=8, filters="ym:s:trafficSource=='organic'",
            )
        elif slice_name == "ad_landing":
            tid = out.get("top_goal_id")
            m = ["ym:s:visits", "ym:s:bounceRate"]
            if tid:
                m += [goal_reaches(tid), goal_conversion(tid)]
            out["ad_landing"] = await c.q(
                "ad_landing", m, ["ym:s:startURLPath"], sort="-ym:s:visits", limit=15,
                filters="ym:s:trafficSource=='ad'",
            )
        elif slice_name == "referal":
            out["referal"] = await c.q("referal", ["ym:s:visits", "ym:s:bounceRate"],
                                       ["ym:s:referalSource"], sort="-ym:s:visits", limit=15)
        elif slice_name == "social":
            out["social"] = await c.q("social", ["ym:s:visits", "ym:s:bounceRate"],
                                      ["ym:s:socialNetwork"], d1=recent,
                                      sort="-ym:s:visits", limit=10)
        elif slice_name == "cities":
            tid = out.get("top_goal_id")
            m = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate"]
            if tid:
                m += [goal_reaches(tid), goal_conversion(tid)]
            out["cities"] = await c.q("cities", m, ["ym:s:regionCity"], sort="-ym:s:visits", limit=15)
        elif slice_name == "devices":
            tid = out.get("top_goal_id")
            m = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate"]
            if tid:
                m += [goal_reaches(tid), goal_conversion(tid)]
            out["devices"] = await c.q("devices", m, ["ym:s:deviceCategory"],
                                       sort="-ym:s:visits", limit=6)
        elif slice_name == "start_top":
            tid = out.get("top_goal_id")
            m = ["ym:s:visits", "ym:s:bounceRate", "ym:s:avgVisitDurationSeconds"]
            if tid:
                m += [goal_reaches(tid), goal_conversion(tid)]
            out["start_top"] = await c.q("start_top", m, ["ym:s:startURLPath"],
                                         sort="-ym:s:visits", limit=20)
        elif slice_name == "start_bounce":
            out["start_bounce"] = await c.q(
                "start_bounce", ["ym:s:visits", "ym:s:bounceRate"], ["ym:s:startURLPath"],
                sort="-ym:s:bounceRate", limit=15, filters="ym:s:visits>=200",
            )
        elif slice_name == "pages":
            out["pages"] = await c.q("pages", ["ym:pv:pageviews"], ["ym:pv:URL"],
                                     d1=d2 - timedelta(days=365), sort="-ym:pv:pageviews", limit=20)
        elif slice_name == "period_now":
            out["period_now"] = await c.q(
                "period_now",
                ["ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:newUsers",
                 "ym:s:avgVisitDurationSeconds", "ym:s:pageDepth"],
                d1=d2 - timedelta(days=365),
            )
        elif slice_name == "period_prev":
            out["period_prev"] = await c.q(
                "period_prev",
                ["ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:newUsers",
                 "ym:s:avgVisitDurationSeconds", "ym:s:pageDepth"],
                d1=d2 - timedelta(days=730), d2=d2 - timedelta(days=365),
            )
        else:
            print("UNKNOWN slice", slice_name)
            return 2

        notes = out.get("notes", [])
        notes = [n for n in notes if not any(n == x for x in c.notes)]
        out["notes"] = list(dict.fromkeys(out.get("notes", []) + c.notes))
        target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    done = [k for k, v in out.items() if isinstance(v, dict) and "rows" in v]
    print(f"OK slice={slice_name} err={c.notes} file_sections={len(done)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--counter", type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--slice", required=True)
    p.add_argument("--site", default="")
    a = p.parse_args()
    sys.exit(asyncio.run(run(a.counter, a.out, a.slice, a.site)))
