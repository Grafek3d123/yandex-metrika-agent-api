"""Выгрузить все срезы маркетингового аудита в CSV (UTF-8 BOM для Excel).

Создаёт папку reports/exports/<site>/ и пишет:
  golden_landing_pages.csv  — «золотые» посадочные (рейтинг + тир)
  bounce_pages.csv          — страницы-отказники
  search_terms.csv          — семантика (поисковые фразы) с приоритетом для Директа
  channels.csv              — каналы трафика
  goals.csv                 — цели и конверсия
  geo_cities.csv            — география
  devices.csv               — устройства
  organic_by_engine.csv     — органика по поисковым системам
  ad_landing_pages.csv      — посадочные Яндекс.Директа
  traffic_by_year.csv       — динамика по годам

Запуск: python scripts/export_csv.py --data reports/<site>_full_data.json \
            [--brand-words <подстроки,бренда,ниши>]   # пометить бренд в семантике
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def rows_of(data: dict, key: str) -> list[dict]:
    return data.get(key, {}).get("rows", []) or []


def nums(row: dict, prefix: str = "met") -> list[float]:
    out: list[float] = []
    i = 0
    while f"{prefix}{i}" in row:
        v = row[f"{prefix}{i}"]
        out.append(float(v) if isinstance(v, (int, float)) else 0.0)
        i += 1
    return out


def write(path: Path, header: list[str], rows: list[list]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


def r2(x: float) -> str:
    return f"{x:.2f}"


def tier_by_conv(conv: float, visits: float) -> str:
    if conv >= 5:
        return "ЗОЛОТАЯ"
    if conv >= 1:
        return "хорошая"
    if conv >= 0.3:
        return "средняя"
    return "слабая"


def tier_by_bounce(bounce: float) -> str:
    if bounce <= 12:
        return "ЗОЛОТАЯ"
    if bounce <= 20:
        return "хорошая"
    if bounce <= 30:
        return "средняя"
    return "слабая"


def export(data: dict, out_root: Path, brand_words: tuple[str, ...] = ()) -> dict:
    site = data.get("site", "site")
    out = out_root / site
    summary: dict[str, int] = {}

    # --- 1. Золотые посадочные (входные страницы) ---
    st = rows_of(data, "start_top")
    convs = [nums(r)[4] for r in st if len(nums(r)) > 4]
    has_conv = bool(convs) and max(convs) >= 0.5
    gold_rows: list[list] = []
    for r in st:
        n = nums(r)
        if len(n) < 2:
            continue
        url, visits, bounce = r.get("dim0", ""), n[0], n[1]
        dur = n[2] if len(n) > 2 else 0
        reaches = n[3] if len(n) > 3 else 0
        conv = n[4] if len(n) > 4 else 0
        tier = tier_by_conv(conv, visits) if has_conv else tier_by_bounce(bounce)
        gold_rows.append([url, f"{visits:.0f}", r2(bounce), r2(dur), f"{reaches:.0f}",
                          r2(conv), tier])
    key = (lambda x: float(x[5])) if has_conv else (lambda x: -float(x[2]))
    gold_rows.sort(key=key, reverse=True)
    summary["golden_landing_pages.csv"] = write(
        out / "golden_landing_pages.csv",
        ["URL", "Визиты", "Отказы,%", "Время,сек", "Дост.цели", "Конверсия,%", "Оценка"],
        gold_rows,
    )

    # --- 2. Отказники ---
    sb = rows_of(data, "start_bounce")
    b_rows = [[r.get("dim0", ""), f"{nums(r)[0]:.0f}", r2(nums(r)[1]),
               "критично" if nums(r)[1] >= 50 else "высокие"] for r in sb]
    summary["bounce_pages.csv"] = write(
        out / "bounce_pages.csv", ["URL", "Визиты", "Отказы,%", "Уровень"], b_rows,
    )

    # --- 3. Семантика (поисковые фразы) ---
    terms = rows_of(data, "search_terms")
    visits_list = sorted(nums(r)[0] for r in terms)
    med = visits_list[len(visits_list) // 2] if visits_list else 0
    t_rows: list[list] = []
    for r in terms:
        n = nums(r)
        phrase, visits, bounce = r.get("dim0", ""), n[0], n[1]
        brand = any(s in phrase.lower() for s in brand_words) if brand_words else ""
        prio = "высокий" if (bounce <= 15 and visits >= med) else "средний"
        t_rows.append([phrase, f"{visits:.0f}", r2(bounce), prio,
                       "да" if brand else ""])
    summary["search_terms.csv"] = write(
        out / "search_terms.csv",
        ["Фраза", "Визиты", "Отказы,%", "Приоритет_для_Директа", "Бренд/ниша"], t_rows,
    )

    # --- 4. Каналы ---
    src = rows_of(data, "sources")
    total = sum(nums(r)[0] for r in src) or 1
    c_rows = [[r.get("dim0", ""), f"{nums(r)[0]:.0f}", r2(nums(r)[0] / total * 100),
               r2(nums(r)[2]) if len(nums(r)) > 2 else "",
               r2(nums(r)[3]) if len(nums(r)) > 3 else "",
               r2(nums(r)[5]) if len(nums(r)) > 5 else ""] for r in src]
    summary["channels.csv"] = write(
        out / "channels.csv",
        ["Канал", "Визиты", "Доля,%", "Отказы,%", "Время,сек", "Конв.топ-цели,%"], c_rows,
    )

    # --- 5. Цели ---
    g_rows = [[g.get("name", ""), g.get("type", ""), f"{(g.get('reaches') or 0):.0f}",
               r2(g.get("conversion_rate") or 0)] for g in data.get("goals", [])]
    summary["goals.csv"] = write(
        out / "goals.csv", ["Цель", "Тип", "Достижения", "Конверсия,%"], g_rows,
    )

    # --- 6. Гео ---
    cities = rows_of(data, "cities")
    geo_rows = [[r.get("dim0", ""), f"{nums(r)[0]:.0f}",
                 r2(nums(r)[2]) if len(nums(r)) > 2 else "",
                 r2(nums(r)[4]) if len(nums(r)) > 4 else ""] for r in cities]
    summary["geo_cities.csv"] = write(
        out / "geo_cities.csv", ["Город", "Визиты", "Отказы,%", "Конв.топ-цели,%"], geo_rows,
    )

    # --- 7. Устройства ---
    dev = rows_of(data, "devices")
    d_rows = [[r.get("dim0", ""), f"{nums(r)[0]:.0f}", r2(nums(r)[2]) if len(nums(r)) > 2 else "",
               r2(nums(r)[4]) if len(nums(r)) > 4 else ""] for r in dev]
    summary["devices.csv"] = write(
        out / "devices.csv", ["Устройство", "Визиты", "Отказы,%", "Конв.топ-цели,%"], d_rows,
    )

    # --- 8. Органика по системам ---
    eng = rows_of(data, "organic_by_engine")
    e_rows = [[r.get("dim0", ""), f"{nums(r)[0]:.0f}", r2(nums(r)[1]) if len(nums(r)) > 1 else ""]
              for r in eng]
    summary["organic_by_engine.csv"] = write(
        out / "organic_by_engine.csv", ["Поисковая система", "Визиты", "Отказы,%"], e_rows,
    )

    # --- 9. Посадочные Директа ---
    ad = rows_of(data, "ad_landing")
    ad_rows = [[r.get("dim0", ""), f"{nums(r)[0]:.0f}", r2(nums(r)[1]),
                r2(nums(r)[3]) if len(nums(r)) > 3 else ""] for r in ad]
    summary["ad_landing_pages.csv"] = write(
        out / "ad_landing_pages.csv",
        ["Посадочная", "Визиты", "Отказы,%", "Конв.топ-цели,%"], ad_rows,
    )

    # --- 10. По годам ---
    yrs = rows_of(data, "years")
    y_rows = [[r.get("year", ""), f"{float(r.get('visits',0) or 0):.0f}",
               f"{float(r.get('users',0) or 0):.0f}",
               r2(float(r.get("bounce_rate", 0) or 0)),
               r2(float(r.get("page_depth", 0) or 0))] for r in yrs]
    summary["traffic_by_year.csv"] = write(
        out / "traffic_by_year.csv",
        ["Год", "Визиты", "Посетители", "Отказы,%", "Глубина"], y_rows,
    )

    return {"site": site, "dir": str(out), "files": summary}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out-root", default="reports/exports")
    p.add_argument("--brand-words", default="",
                   help="Подстроки бренд/ниши через запятую для пометки семантики")
    a = p.parse_args()
    brand_words = tuple(w.strip().lower() for w in a.brand_words.split(",") if w.strip())
    data = json.loads(Path(a.data).read_text(encoding="utf-8"))
    res = export(data, Path(a.out_root), brand_words)
    print(f"SITE {res['site']} -> {res['dir']}")
    for name, cnt in res["files"].items():
        print(f"  {name:28} {cnt} строк")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
