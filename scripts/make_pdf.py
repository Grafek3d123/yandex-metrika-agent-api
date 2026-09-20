"""Собрать PDF-дашборд с графиками по маркетинговому аудиту одного сайта.

Читает JSON-пакет (reports/<...>_full_data.json), строит набор графиков
matplotlib (с кириллицей через Segoe UI) и собирает PDF через reportlab.

Запуск:
  python scripts/make_pdf.py --data reports/<site>_full_data.json \
      --title "<domain.ru>" --out reports/marketing_report_<site>.pdf \
      --accent "#0f5c8c"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from reportlab.lib import colors as rlcolors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# --- Шрифты (кириллица, лёгкие начертания Segoe UI) --------------------------

from reportlab.pdfbase.pdfmetrics import registerFontFamily  # noqa: E402

_FONTS = {
    "Segoe": r"C:\Windows\Fonts\segoeui.ttf",
    "Segoe-Bold": r"C:\Windows\Fonts\segoeuib.ttf",
    "Segoe-Light": r"C:\Windows\Fonts\segoeuil.ttf",
    "Segoe-Semi": r"C:\Windows\Fonts\segoeuisl.ttf",
}
for _name, _p in _FONTS.items():
    if Path(_p).exists():
        fm.fontManager.addfont(_p)
        pdfmetrics.registerFont(TTFont(_name, _p))

# Псевдонимы, если Segoe UI недоступен
if "Segoe" not in pdfmetrics.getRegisteredFontNames():
    _ARIAL = r"C:\Windows\Fonts\arial.ttf"
    _ARIAL_B = r"C:\Windows\Fonts\ariblk.ttf"
    for _p in (_ARIAL, _ARIAL_B):
        if Path(_p).exists():
            fm.fontManager.addfont(_p)
    pdfmetrics.registerFont(TTFont("Segoe", _ARIAL))
    pdfmetrics.registerFont(TTFont("Segoe-Bold", _ARIAL_B))
    _FONTS = {"Segoe": _ARIAL, "Segoe-Bold": _ARIAL_B}

registerFontFamily("Segoe", normal="Segoe", bold="Segoe-Bold",
                   italic="Segoe", boldItalic="Segoe-Bold")

plt.rcParams["font.family"] = "Segoe UI"
plt.rcParams["axes.unicode_minus"] = False

GRID = {"color": "#d8d8d8", "linewidth": 0.7}
SPINE = "#b0b0b0"


def _style_ax(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(SPINE)
    ax.grid(axis="y", **GRID)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=9, colors="#444444")


def _fmt_k(value, _pos=None):
    if abs(value) >= 1000:
        return f"{value/1000:.0f}k"
    return f"{value:.0f}"


# --- Извлечение значений ----------------------------------------------------


def _nums(row: dict, prefix: str = "met") -> list[float]:
    out: list[float] = []
    i = 0
    while f"{prefix}{i}" in row:
        v = row[f"{prefix}{i}"]
        out.append(float(v) if isinstance(v, (int, float)) else 0.0)
        i += 1
    return out


def _rows(data: dict, key: str) -> list[dict]:
    return data.get(key, {}).get("rows", []) or []


def _totals(data: dict, key: str) -> list[float]:
    t = data.get(key, {}).get("totals") or []
    return [float(x) if isinstance(x, (int, float)) else 0.0 for x in t]


# --- Графики -----------------------------------------------------------------


def chart_years(data: dict, accent: str, path: Path) -> str:
    rows = _rows(data, "years")
    if not rows:
        return ""
    years = [int(r.get("year", 0)) for r in rows]
    visits = [float(r.get("visits", 0) or 0) for r in rows]
    bounce = [float(r.get("bounce_rate", 0) or 0) for r in rows]

    fig, ax1 = plt.subplots(figsize=(9.2, 4.2))
    ax1.bar([str(y) for y in years], visits, color=accent, width=0.68, zorder=3)
    ax1.set_ylabel("Визиты в год", fontsize=10, color="#333333")
    ax1.yaxis.set_major_formatter(FuncFormatter(_fmt_k))
    _style_ax(ax1)
    peak = max(range(len(visits)), key=lambda i: visits[i])
    ax1.annotate(f"{visits[peak]/1000:.0f}k", (peak, visits[peak]), textcoords="offset points",
                 xytext=(0, 3), ha="center", fontsize=8, color=accent, fontweight="bold")

    ax2 = ax1.twinx()
    ax2.plot([str(y) for y in years], bounce, color="#c0392b", marker="o",
             ms=4, lw=2, zorder=4, label="Доля отказов, %")
    ax2.set_ylabel("Доля отказов, %", fontsize=10, color="#c0392b")
    ax2.tick_params(labelsize=9, colors="#c0392b")
    ax2.spines["top"].set_visible(False)
    ax1.set_title("Визиты и доля отказов по годам", fontsize=13, color="#222222", pad=12)
    ax1.set_xlabel("")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return str(path)


def chart_channels(data: dict, accent: str, path: Path) -> str:
    rows = _rows(data, "sources")
    if not rows:
        return ""
    order = ["Search engine traffic", "Ad traffic", "Direct traffic", "Internal traffic",
             "Social network traffic", "Link traffic", "Mailing traffic"]
    ru = {
        "Search engine traffic": "Органика", "Ad traffic": "Яндекс.Директ",
        "Direct traffic": "Прямые", "Internal traffic": "Внутренний",
        "Social network traffic": "Соцсети", "Link traffic": "По ссылкам",
        "Mailing traffic": "Email",
    }
    total = sum(_nums(r)[0] for r in rows) or 1
    rows_sorted = sorted(rows, key=lambda r: _nums(r)[0], reverse=True)
    names, vals = [], []
    for r in rows_sorted:
        d = r.get("dim0", "")
        if d not in ru:
            continue
        names.append(ru[d])
        vals.append(_nums(r)[0])
    fig, ax = plt.subplots(figsize=(9.2, 3.9))
    cols = []
    for n in names:
        cols.append("#2e8b57" if n == "Органика" else
                    "#c0392b" if n == "Яндекс.Директ" else accent)
    bars = ax.barh(names[::-1], vals[::-1], color=cols[::-1], zorder=3, height=0.62)
    ax.set_xlabel("Визиты", fontsize=10, color="#333333")
    ax.xaxis.set_major_formatter(FuncFormatter(_fmt_k))
    for b, v in zip(bars, vals[::-1]):
        ax.text(b.get_width() + total * 0.005, b.get_y() + b.get_height() / 2,
                f"{v/total*100:.1f}%", va="center", fontsize=9, color="#333333")
    ax.set_xlim(0, max(vals) * 1.18)
    _style_ax(ax)
    ax.grid(axis="x", **GRID)
    ax.grid(axis="y", visible=False)
    ax.set_title("Каналы трафика (доля визитов)", fontsize=13, color="#222222", pad=12)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return str(path)


def chart_organic_vs_ad(data: dict, accent: str, path: Path) -> str:
    org = _totals(data, "organic_seg")
    ad = _totals(data, "ad_seg")
    if len(org) < 3 or len(ad) < 3:
        return ""
    # conv = индекс 5 (если осмысленно), иначе bounce = индекс 2
    use_conv = len(org) >= 6 and org[5] > 0.1 and ad[5] > 0.05
    metric_idx = 5 if use_conv else 2
    unit = "Конверсия в цель, %" if use_conv else "Доля отказов, %"
    o, a = org[metric_idx], ad[metric_idx]
    fig, ax = plt.subplots(figsize=(9.2, 3.6))
    bars = ax.bar(["Органика", "Яндекс.Директ"], [o, a],
                  color=["#2e8b57", "#c0392b"], width=0.5, zorder=3)
    for b, v in zip(bars, [o, a]):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.2f}%", ha="center",
                va="bottom", fontsize=12, fontweight="bold", color="#222222")
    if use_conv and a > 0:
        ax.set_title(f"Органика vs Директ: {unit.lower()} (органика в {o/a:.0f}× лучше)",
                     fontsize=13, color="#222222", pad=12)
    else:
        ax.set_title(f"Органика vs Директ: {unit.lower()} (у Директа в {a/o:.1f}× хуже)",
                     fontsize=13, color="#222222", pad=12)
    ax.set_ylabel(unit, fontsize=10, color="#333333")
    ax.set_ylim(0, max(o, a) * 1.22)
    _style_ax(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return str(path)


def chart_start_pages(data: dict, accent: str, path: Path) -> str:
    rows = _rows(data, "start_top")[:12]
    if not rows:
        return ""
    names = [r.get("dim0", "") for r in rows]
    visits = [_nums(r)[0] for r in rows]
    bounce = [_nums(r)[1] for r in rows]

    def shorten(u: str, n=42):
        u = u.replace("https://", "").replace("http://", "")
        return u if len(u) <= n else u[: n - 1] + "…"

    cmap = plt.get_cmap("RdYlGn_r")
    bmax = max(bounce) or 1
    cols = [cmap(min(b / 60, 1.0)) for b in bounce]
    fig, ax = plt.subplots(figsize=(9.2, 5.0))
    bars = ax.barh([shorten(n) for n in names][::-1], visits[::-1], color=cols[::-1], zorder=3)
    ax.set_xscale("log")
    ax.set_xlabel("Визиты (лог. шкала)", fontsize=10, color="#333333")
    for b, bn in zip(bars, bounce[::-1]):
        ax.text(b.get_width() * 1.1, b.get_y() + b.get_height() / 2,
                f"{bn:.0f}% отказ", va="center", fontsize=8, color="#555555")
    _style_ax(ax)
    ax.grid(axis="x", **GRID)
    ax.grid(axis="y", visible=False)
    ax.set_title("Топ входных страниц (цвет = доля отказов)", fontsize=13, color="#222222", pad=12)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return str(path)


def chart_devices(data: dict, accent: str, path: Path, show_conv: bool = True) -> str:
    rows = [r for r in _rows(data, "devices") if r.get("dim0") in ("PC", "Smartphones", "Tablets")]
    if not rows:
        return ""
    ru = {"PC": "ПК", "Smartphones": "Смартфоны", "Tablets": "Планшеты"}
    names = [ru[r["dim0"]] for r in rows]
    visits = [_nums(r)[0] for r in rows]
    bounce = [_nums(r)[2] for r in rows]
    conv = [_nums(r)[4] if len(_nums(r)) > 4 else 0 for r in rows]
    x = range(len(names))
    fig, (axa, axb) = plt.subplots(1, 2, figsize=(9.2, 3.7))
    b1 = axa.bar(x, visits, color=accent, zorder=3)
    axa.set_xticks(list(x), names, fontsize=9)
    axa.set_title("Визиты", fontsize=11, color="#222222")
    axa.yaxis.set_major_formatter(FuncFormatter(_fmt_k))
    for b, v in zip(b1, visits):
        axa.text(b.get_x() + b.get_width() / 2, v, f"{v/1000:.0f}k" if v > 1000 else f"{v:.0f}",
                 ha="center", va="bottom", fontsize=9)
    _style_ax(axa)
    if show_conv:
        axb.bar([i - 0.2 for i in x], bounce, width=0.4, color="#c0392b", zorder=3, label="Отказы %")
        axb.bar([i + 0.2 for i in x], conv, width=0.4, color="#2e8b57", zorder=3, label="Конв. %")
        axb.set_title("Отказы vs конверсия", fontsize=11, color="#222222")
    else:
        axb.bar(list(x), bounce, width=0.5, color="#c0392b", zorder=3, label="Отказы %")
        axb.set_title("Доля отказов", fontsize=11, color="#222222")
    axb.set_xticks(list(x), names, fontsize=9)
    axb.legend(fontsize=8, frameon=False)
    _style_ax(axb)
    fig.suptitle("Устройства", fontsize=13, color="#222222", y=1.02)
    fig.tight_layout()
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def chart_leak(data: dict, accent: str, path: Path) -> str:
    """Для сайтов с околонулевой конверсией: трафик vs заявки."""
    total_visits = _totals(data, "summary")[0] if _totals(data, "summary") else 0
    reaches = sum(float(g.get("reaches") or 0) for g in data.get("goals", []))
    if total_visits <= 0 or reaches > total_visits * 0.005:
        return ""
    fig, ax = plt.subplots(figsize=(9.2, 2.6))
    ax.barh(["Визиты", "Заявки (цели)"], [total_visits, max(reaches, total_visits * 0.002)],
            color=[accent, "#c0392b"], zorder=3, height=0.55)
    ax.set_xscale("log")
    ax.text(total_visits, 0, f"  {total_visits:,.0f}".replace(",", " "), va="center", fontsize=11)
    ax.text(max(reaches, total_visits * 0.002), 1,
            f"  {reaches:.0f} заявок", va="center", fontsize=11, color="#c0392b",
            fontweight="bold")
    ax.set_title("Утечка воронки: трафик почти не конвертируется в заявки",
                 fontsize=13, color="#222222", pad=12)
    _style_ax(ax)
    ax.grid(axis="x", **GRID)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return str(path)


def chart_search_terms(data: dict, accent: str, path: Path) -> str:
    rows = _rows(data, "search_terms")[:12]
    if not rows:
        return ""
    names = [r.get("dim0", "")[:34] for r in rows]
    visits = [_nums(r)[0] for r in rows]
    bounce = [_nums(r)[1] for r in rows]
    cmap = plt.get_cmap("RdYlGn_r")
    cols = [cmap(min(b / 60, 1.0)) for b in bounce]
    fig, ax = plt.subplots(figsize=(9.2, 4.4))
    bars = ax.barh(names[::-1], visits[::-1], color=cols[::-1], zorder=3)
    ax.set_xlabel("Визиты (органика)", fontsize=10, color="#333333")
    for b, bn in zip(bars, bounce[::-1]):
        ax.text(b.get_width() + max(visits) * 0.005, b.get_y() + b.get_height() / 2,
                f"{bn:.0f}%", va="center", fontsize=8, color="#555555")
    ax.set_xlim(0, max(visits) * 1.12)
    _style_ax(ax)
    ax.grid(axis="x", **GRID)
    ax.grid(axis="y", visible=False)
    ax.set_title("Поисковые фразы (цвет = доля отказов)", fontsize=13, color="#222222", pad=12)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return str(path)


# --- Сборка PDF --------------------------------------------------------------

TITLE = ParagraphStyle("t", fontName="Segoe-Semi", fontSize=21, leading=25,
                       textColor=rlcolors.HexColor("#16323f"))
SUB = ParagraphStyle("s", fontName="Segoe", fontSize=10, leading=14,
                     textColor=rlcolors.HexColor("#6b7b83"))
H2 = ParagraphStyle("h2", fontName="Segoe-Semi", fontSize=13, leading=16,
                    textColor=rlcolors.HexColor("#16323f"), spaceBefore=10, spaceAfter=5)
CAP = ParagraphStyle("c", fontName="Segoe", fontSize=8.8, leading=12.5,
                     textColor=rlcolors.HexColor("#6b7b83"))
_KLABEL = ParagraphStyle("kl", fontName="Segoe", fontSize=7.6, leading=9.2,
                         textColor=rlcolors.HexColor("#7c8b92"), alignment=1)
_KVALUE = ParagraphStyle("kv", fontName="Segoe", fontSize=16, leading=18,
                         textColor=rlcolors.HexColor("#16323f"), alignment=1)
_KDELTA = ParagraphStyle("kd", fontName="Segoe-Semi", fontSize=8.4, leading=10, alignment=1)


def kpi_table(items: list[tuple[str, str, str]], accent: str) -> Table:
    """Сетка 3 колонки × N рядов. Заголовки переносятся через Paragraph."""
    per_row = 3
    cells = []
    for label, value, delta in items:
        dneg = delta.startswith("−") or delta.startswith("-")
        dcol = "#c0392b" if dneg else "#2e8b57"
        kdel = ParagraphStyle("kd2", parent=_KDELTA, textColor=rlcolors.HexColor(dcol))
        card = [Paragraph(label, _KLABEL), Spacer(1, 2),
                Paragraph(value, _KVALUE), Spacer(1, 1),
                Paragraph(delta or "&nbsp;", kdel)]
        cells.append(card)
    while len(cells) % per_row:
        cells.append([Paragraph("", _KLABEL)])

    rows = [cells[i: i + per_row] for i in range(0, len(cells), per_row)]
    colw = (17.0 / per_row) * cm
    t = Table(rows, colWidths=[colw] * per_row)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), rlcolors.HexColor("#f4f8fa")),
        ("BOX", (0, 0), (-1, -1), 0.7, rlcolors.HexColor("#dbe6ea")),
        ("INNERGRID", (0, 0), (-1, -1), 2.2, rlcolors.HexColor("#ffffff")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def build(data: dict, title: str, out: Path, accent: str, charts_dir: Path,
          insights: dict) -> None:
    charts_dir.mkdir(parents=True, exist_ok=True)

    def cp(name):
        return charts_dir / f"{data.get('site','site')}_{name}.png"

    # Цели неинформативны, если суммарные достижения ничтожны относительно визитов
    # (например, цели были пересозданы и их история обнулена).
    _tv = _totals(data, "summary")
    _total_visits = _tv[0] if _tv else 0.0
    _reaches = sum(float(g.get("reaches") or 0) for g in data.get("goals", []))
    goals_ok = _total_visits > 0 and _reaches >= _total_visits * 0.001

    imgs = []
    imgs.append(("Динамика трафика за всё время", chart_years(data, accent, cp("years")),
                 insights.get("years", "")))
    imgs.append(("Каналы трафика", chart_channels(data, accent, cp("channels")),
                 insights.get("channels", "")))
    ov = chart_organic_vs_ad(data, accent, cp("ovs"))
    if ov:
        imgs.append(("Органика против Директа", ov, insights.get("ovs", "")))
    imgs.append(("Целевые страницы", chart_start_pages(data, accent, cp("start")),
                 insights.get("start", "")))
    st = chart_search_terms(data, accent, cp("terms"))
    if st:
        imgs.append(("Поисковый спрос", st, insights.get("terms", "")))
    imgs.append(("Аудитория по устройствам",
                 chart_devices(data, accent, cp("devices"), show_conv=goals_ok),
                 insights.get("devices", "")))

    # KPI
    s = _totals(data, "summary")
    pn = _totals(data, "period_now")
    pp = _totals(data, "period_prev")

    def yoy(cur, prev, unit=""):
        if prev and cur is not None:
            d = (cur - prev) / prev * 100
            return f"{'+' if d>=0 else '−'}{abs(d):.0f}%{unit}"
        return ""

    def dur(sec):
        m = int(sec // 60)
        return f"{m}:{int(sec%60):02d}"

    kpis = []
    if s:
        kpis.append(("Визиты · всё время", f"{s[0]/1e6:.1f}M" if s[0] > 1e6 else f"{s[0]:,.0f}".replace(",", " "), "без выборки"))
        kpis.append(("Отказы · всё время", f"{s[5]:.1f}%", ""))
        kpis.append(("Глубина · всё время", f"{s[7]:.2f}", ""))
    if pn and pp:
        kpis.append(("Визиты · 12 мес", f"{pn[0]:,.0f}".replace(",", " "), yoy(pn[0], pp[0])))
        kpis.append(("Отказы · 12 мес", f"{pn[2]:.1f}%", f"{pn[2]-pp[2]:+.1f} п.п."))
        kpis.append(("Время · 12 мес", dur(pn[4]), yoy(pn[4], pp[4])))

    story: list = []
    story.append(Paragraph(f"Маркетинговый аудит: {title}", TITLE))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"Данные Яндекс.Метрики · счётчик {data.get('counter_id')} · период "
        f"{data['period']['date1']} — {data['period']['date2']} · без выборки", SUB))
    story.append(Spacer(1, 12))
    if kpis:
        story.append(kpi_table(kpis, accent))
    story.append(Spacer(1, 14))

    if not goals_ok:
        warn = ParagraphStyle(
            "warn", fontName="Segoe", fontSize=9.5, leading=13.5,
            textColor=rlcolors.HexColor("#7a4a00"), backColor=rlcolors.HexColor("#fff5e0"),
            borderColor=rlcolors.HexColor("#e0b860"), borderWidth=0.8,
            borderPadding=8, spaceBefore=2, spaceAfter=6)
        story.append(Paragraph(
            "<b>Внимание: история целей обнулена.</b> Цели были пересозданы, поэтому их "
            "достижения есть только с последнего периода, а за прошлые годы — нули при "
            "сплошных данных. Конверсия целей за всё время здесь намеренно не приводится и "
            "не является диагнозом: она занижена раздутым знаменателем. Выводы по трафику, "
            "каналам, посадочным и аудитории — полные и достоверные.", warn))
        story.append(Spacer(1, 12))

    for i, (head, img, cap) in enumerate(imgs):
        if not img:
            continue
        story.append(Paragraph(head, H2))
        story.append(Image(img, width=17.0 * cm, height=17.0 * cm * _ratio(img)))
        if cap:
            story.append(Paragraph(cap, CAP))
        story.append(Spacer(1, 10))

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Segoe", 7.5)
        canvas.setFillColor(rlcolors.HexColor("#9aa7ad"))
        canvas.drawString(2 * cm, 1.1 * cm, f"{title} · маркетинговый аудит по данным Яндекс.Метрики")
        canvas.drawRightString(A4[0] - 2 * cm, 1.1 * cm, f"стр. {canvas.getPageNumber()}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(out), pagesize=A4, topMargin=1.6 * cm,
                            bottomMargin=1.6 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
                            title=f"Аудит {title}")
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def _ratio(img_path: str) -> float:
    from PIL import Image as PILImage  # noqa: PLC0415

    with PILImage.open(img_path) as im:
        w, h = im.size
    return h / w


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--accent", default="#0f5c8c")
    p.add_argument("--charts-dir", default="reports/charts")
    a = p.parse_args()

    data = json.loads(Path(a.data).read_text(encoding="utf-8"))
    ins_path = Path(a.data).with_suffix(".insights.json")
    insights = json.loads(ins_path.read_text(encoding="utf-8")) if ins_path.exists() else {}

    out = Path(a.out)
    build(data, a.title, out, a.accent, Path(a.charts_dir), insights)
    print(f"PDF: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
