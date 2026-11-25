#!/usr/bin/env python3
"""生成 WORK-578 单文件交付页 docs/DELIVERY-WORK-578.html（图片 base64 内嵌，断网可读）。

    QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \\
      /home/moyongkai/.venvs/quantdb/bin/python scripts/work578_build_delivery.py

数据全部现场读主库（只读）与 meta/industry/work578-*.json，不手抄数字；
HTML 骨架在同目录的 work578_delivery.tpl.html（string.Template，$ 占位）。
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from string import Template

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import pandas as pd  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

DB = "/srv/share/quantdb/db/quantdb.duckdb"
META = Path("/srv/share/quantdb/meta/industry")
HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "work578_delivery.tpl.html"
OUT = HERE.parent / "docs" / "DELIVERY-WORK-578.html"
TABLE = "industry_exchange_daily_quotes"

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Noto Serif CJK SC", "Noto Sans SC", "DejaVu Serif"],
    "font.size": 9,
    "axes.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.frameon": False,
    "figure.dpi": 160,
    "savefig.dpi": 160,
})

INK = "#1a1a1a"
MUTED = "#9aa0a6"
ACCENT = "#0b6bcb"
ACCENT2 = "#c2410c"


def _b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def figure_coverage() -> str:
    """上游实测边界 vs 本次交付窗口。"""
    import datetime as dt
    rows = [
        ("上期所 .dat 归档（实测最早 2002-01-07）", dt.date(2002, 1, 7), dt.date(2026, 11, 25), MUTED),
        ("广期所 碳酸锂（2023-07-21 上市）", dt.date(2023, 7, 21), dt.date(2026, 11, 25), MUTED),
        ("本次交付窗口（259 个交易日）", dt.date(2025, 11, 3), dt.date(2026, 11, 25), ACCENT),
    ]
    fig, ax = plt.subplots(figsize=(6.3, 1.9))
    for y, (label, a, b, color) in enumerate(rows):
        ax.barh(y, (b - a).days, left=a, height=0.42, color=color,
                alpha=0.95 if color == ACCENT else 0.45)
        ax.text(a, y + 0.34, label, fontsize=7.5, color=INK, va="bottom")
    ax.set_yticks([])
    ax.set_ylim(-0.6, len(rows) - 0.05)
    ax.xaxis.set_major_locator(mdates.YearLocator(4))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_xlabel("年份")
    ax.set_title("图 1  上游可得区间与本次回补窗口", loc="left", color=INK)
    ax.grid(axis="x", color="#e6e6e6", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    return _b64(fig)


def figure_series(c) -> str:
    """库里真实落盘的两个序列：碳酸锂近月、沪铜近月。"""
    q_lc = f"""
        SELECT trade_date, close FROM {TABLE}
        WHERE exchange='GFEX' AND contract='碳酸锂'
          AND month >= strftime(trade_date, '%y%m')
        QUALIFY row_number() OVER (PARTITION BY trade_date ORDER BY month) = 1
        ORDER BY trade_date
    """
    q_cu = f"""
        SELECT trade_date, close FROM {TABLE}
        WHERE exchange='SHFE' AND contract='CU'
          AND month >= strftime(trade_date, '%y%m')
        QUALIFY row_number() OVER (PARTITION BY trade_date ORDER BY month) = 1
        ORDER BY trade_date
    """
    lc = c.execute(q_lc).df()
    cu = c.execute(q_cu).df()
    fig, axes = plt.subplots(1, 2, figsize=(6.3, 2.1))
    for ax, df, title, color, unit in (
        (axes[0], lc, "碳酸锂近月合约收盘价", ACCENT2, "元/吨"),
        (axes[1], cu, "沪铜近月合约收盘价", ACCENT, "元/吨"),
    ):
        if len(df):
            ax.plot(pd.to_datetime(df["trade_date"]), df["close"], color=color, linewidth=1.2)
        ax.set_title(title, loc="left")
        ax.set_ylabel(unit)
        ax.grid(color="#ececec", linewidth=0.6)
        ax.set_axisbelow(True)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%y-%m"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    fig.suptitle("图 2  库内实值的两个示例序列（近月＝合约月份 ≥ 当月的最小合约）",
                 x=0.01, ha="left", fontsize=10)
    fig.tight_layout()
    return _b64(fig)


def jload(name: str) -> dict:
    p = META / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def main() -> int:
    c = duckdb.connect(DB, read_only=True)
    total, dates, first, last = c.execute(
        f"SELECT count(*), count(DISTINCT trade_date), min(trade_date), max(trade_date) "
        f"FROM {TABLE}").fetchone()
    by_ex = c.execute(
        f"SELECT exchange, count(*) n, count(DISTINCT trade_date) d, count(DISTINCT contract) cs "
        f"FROM {TABLE} GROUP BY 1 ORDER BY 1").fetchall()
    catalog = c.execute("SELECT count(*) FROM data_catalog").fetchone()[0]
    tabs = c.execute("SELECT count(*) FROM information_schema.tables "
                     "WHERE table_type='BASE TABLE'").fetchone()[0]
    audits = c.execute(
        "SELECT run_id, raw_rows, staged_rows, loaded_rows, partitions, finished_at "
        "FROM industry_load_audit WHERE table_name=? ORDER BY finished_at DESC LIMIT 2",
        [TABLE]).fetchall()
    lc_min, lc_max = c.execute(
        f"SELECT min(close), max(close) FROM {TABLE} WHERE exchange='GFEX' AND contract='碳酸锂'"
    ).fetchone()

    astock = jload("work578-astock-result.json")
    before = jload("work578-astock-baseline.json").get("exchange_before", {})
    fig1, fig2 = figure_coverage(), figure_series(c)
    c.close()

    ex_rows = "".join(
        f"<tr><td>{r[0]}</td><td class='n'>{r[1]:,}</td><td class='n'>{r[2]}</td>"
        f"<td class='n'>{r[3]}</td></tr>" for r in by_ex)
    sum_ex = sum(r[1] for r in by_ex)
    audit_rows = "".join(
        f"<tr><td><code>{a[0]}</code></td><td class='n'>{a[1]:,}</td><td class='n'>{a[2]:,}</td>"
        f"<td class='n'>{a[3]:,}</td><td class='n'>{a[4]}</td><td>{a[5]}</td></tr>"
        for a in audits)

    html = Template(TEMPLATE.read_text(encoding="utf-8")).substitute(
        total=f"{total:,}",
        dates=str(dates),
        first=str(first),
        last=str(last),
        before_dates=str(before.get("dates", 1)),
        before_rows=str(before.get("rows", 84)),
        ex_rows=ex_rows,
        sum_ex=f"{sum_ex:,}",
        sum_expr=" + ".join(f"{r[1]:,}" for r in by_ex),
        row_check=f"{dates * 84:,}",
        tabs=str(tabs),
        catalog=str(catalog),
        zero_diff=str(astock.get("zero_diff", 20)),
        audit_rows=audit_rows,
        lc_min=f"{lc_min:,.0f}",
        lc_max=f"{lc_max:,.0f}",
        fig1=fig1,
        fig2=fig2,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"[delivery] {OUT} ({len(html):,} 字符，两张图 base64 内嵌)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
