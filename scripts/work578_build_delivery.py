#!/usr/bin/env python3
"""生成 WORK-578 单文件交付页 docs/DELIVERY-WORK-578.html（图片 base64 内嵌，断网可读）。

    QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \\
      /home/moyongkai/.venvs/quantdb/bin/python scripts/work578_build_delivery.py

纪律：
  * 所有数字现场读主库（只读）与 meta/industry/work578-*.json，不手抄、**不设默认值**；
  * 证据文件缺失时直接退出（fail-closed），不生成「看起来 PASS」的页面；
  * HTML 骨架在同目录的 work578_delivery.tpl.html（string.Template，$ 占位）。
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
BACKFILL_LOG = "/srv/share/quantdb/logs/work578-backfill.log"

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


def figure_coverage(first, last, dates: int) -> str:
    """上游实测边界 vs 本次交付窗口（窗口端点来自库内实值）。"""
    import datetime as dt
    rows = [
        ("上期所 .dat 归档（实测最早 2002-01-07）", dt.date(2002, 1, 7), last, MUTED),
        ("广期所 碳酸锂（2023-07-21 上市）", dt.date(2023, 7, 21), last, MUTED),
        (f"本次交付窗口（{dates} 个交易日）", first, last, ACCENT),
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


def jload(name: str, why: str) -> dict:
    path = META / name
    if not path.exists():
        raise SystemExit(
            f"[delivery] 缺证据文件 {path}（{why}）。先跑：\n"
            f"  python scripts/work578_verify.py astock --against /srv/backup/quantdb/db/quantdb.duckdb\n"
            f"并保留 {META/'work578-astock-baseline.json'}；不允许用默认值假装 PASS。"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    c = duckdb.connect(DB, read_only=True)
    total, dates, first, last = c.execute(
        f"SELECT count(*), count(DISTINCT trade_date), min(trade_date), max(trade_date) "
        f"FROM {TABLE}").fetchone()
    per_day = c.execute(
        f"SELECT min(n), max(n) FROM (SELECT trade_date, count(*) n FROM {TABLE} "
        f"GROUP BY 1)").fetchone()
    by_ex = c.execute(
        f"SELECT exchange, count(*) n, count(DISTINCT trade_date) d, count(DISTINCT contract) cs "
        f"FROM {TABLE} GROUP BY 1 ORDER BY 1").fetchall()
    catalog = c.execute("SELECT count(*) FROM data_catalog").fetchone()[0]
    tabs = c.execute("SELECT count(*) FROM information_schema.tables "
                     "WHERE table_schema='main' AND table_type='BASE TABLE'").fetchone()[0]
    audits = c.execute(
        "SELECT run_id, raw_rows, staged_rows, loaded_rows, partitions, finished_at "
        "FROM industry_load_audit WHERE table_name=? ORDER BY finished_at DESC LIMIT 2",
        [TABLE]).fetchall()
    lc_min, lc_max = c.execute(
        f"SELECT min(close), max(close) FROM {TABLE} WHERE exchange='GFEX' AND contract='碳酸锂'"
    ).fetchone()

    astock = jload("work578-astock-result.json", "A 股零 diff 证据")
    before = jload("work578-astock-baseline.json", "回补前交易所表规模").get("exchange_before")
    if not before:
        raise SystemExit("[delivery] work578-astock-baseline.json 缺 exchange_before，重跑 snapshot")
    if "zero_diff" not in astock or "tables" not in astock:
        raise SystemExit("[delivery] work578-astock-result.json 缺 zero_diff/tables，重跑 astock")
    if astock["zero_diff"] != astock["tables"]:
        raise SystemExit(f"[delivery] A 股零 diff 未达成 {astock['zero_diff']}/{astock['tables']}，"
                         "拒绝生成「达成」页；先查 diff_tables")
    if before.get("rows") is not None and int(before["rows"]) >= int(total):
        raise SystemExit(f"[delivery] 基线 exchange_before.rows={before['rows']} 不小于当前 {total}，"
                         "说明基线不是改前态（可能被 snapshot 覆盖过），拒绝生成")

    gates_file = META / "work578-checks.json"
    if not gates_file.exists():
        raise SystemExit(f"[delivery] 缺质量闸结果 {gates_file}；先跑 "
                         "`python -m db.checks.industry all --out /srv/share/quantdb/meta/industry/work578-checks.json`")
    gates = json.loads(gates_file.read_text(encoding="utf-8"))
    gate_items = gates.get("gates") or {}
    if not gate_items:
        raise SystemExit("[delivery] work578-checks.json 缺 gates")
    failed = [k for k, v in gate_items.items() if not v.get("pass")]
    gate_line = (f"{len(gate_items) - len(failed)}/{len(gate_items)} PASS"
                 if not failed else f"{len(gate_items) - len(failed)}/{len(gate_items)}（红：{', '.join(failed)}）")
    gate_detail = "；".join(f"{k}={'PASS' if v.get('pass') else 'FAIL'}" for k, v in gate_items.items())
    gate_detail += f"（统计时点 {gates.get('generated_at', '未知')}）"
    if failed:
        raise SystemExit(f"[delivery] 质量闸未全过（{', '.join(failed)}），不生成「达成」页")

    against_file = META / "work578-astock-against.json"
    if against_file.exists():
        ag = json.loads(against_file.read_text(encoding="utf-8"))
        astock_supp = (f"对照库 {ag.get('against')}：{ag['zero_diff']}/{ag['tables']} 一致"
                       f"（{ag.get('checked_at')}）")
    else:
        astock_supp = "未做「对照库」比对（手边没有改前态的库副本）"

    fig1 = figure_coverage(first, last, dates)
    fig2 = figure_series(c)
    c.close()

    ex_rows = "".join(
        f"<tr><td>{r[0]}</td><td class='n'>{r[1]:,}</td><td class='n'>{r[2]}</td>"
        f"<td class='n'>{r[3]}</td></tr>" for r in by_ex)
    sum_ex = sum(r[1] for r in by_ex)
    audit_rows = "".join(
        f"<tr><td><code>{a[0]}</code></td><td class='n'>{a[1]:,}</td><td class='n'>{a[2]:,}</td>"
        f"<td class='n'>{a[3]:,}</td><td class='n'>{a[4]}</td><td>{a[5]}</td></tr>"
        for a in audits)
    per_day_text = (f"每个交易日恒 {per_day[0]} 行（{dates} × {per_day[0]} = {total:,}）"
                    if per_day[0] == per_day[1] else
                    f"每交易日行数在 {per_day[0]}–{per_day[1]} 之间，合计 {total:,}")
    astock_line = (f"对改前基线（{astock.get('baseline_generated_at', '未知时点')}）逐表「{astock.get('method', '行数')}」比对："
                   f"{astock['zero_diff']}/{astock['tables']} 一致")

    html = Template(TEMPLATE.read_text(encoding="utf-8")).substitute(
        total=f"{total:,}",
        dates=str(dates),
        first=str(first),
        last=str(last),
        before_dates=str(before.get("dates")),
        before_rows=str(before.get("rows")),
        ex_rows=ex_rows,
        sum_ex=f"{sum_ex:,}",
        sum_expr=" + ".join(f"{r[1]:,}" for r in by_ex),
        per_day_text=per_day_text,
        tabs=str(tabs),
        catalog=str(catalog),
        astock_line=astock_line,
        astock_supp=astock_supp,
        gate_line=gate_line,
        gate_detail=gate_detail,
        zero_diff=str(astock["zero_diff"]),
        astock_tables=str(astock["tables"]),
        audit_rows=audit_rows,
        lc_min=f"{lc_min:,.0f}",
        lc_max=f"{lc_max:,.0f}",
        backfill_log=BACKFILL_LOG,
        fig1=fig1,
        fig2=fig2,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"[delivery] {OUT} ({len(html):,} 字符，两张图 base64 内嵌；per-day {per_day[0]}–{per_day[1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
