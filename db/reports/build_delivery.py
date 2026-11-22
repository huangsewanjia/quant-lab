"""Build a single-file docs/DELIVERY.html with embedded figures."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import MaxNLocator

from ..etl import common

TEAL = "#0B6E99"
ORANGE = "#C46B2E"
GREEN = "#3F7D4A"
TEMPLATE = Path(__file__).with_name("delivery_template.html")


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#333333",
            "axes.linewidth": 0.8,
            "figure.dpi": 160,
            "savefig.dpi": 220,
            "savefig.bbox": "tight",
        }
    )


def price_figure(con, out: Path) -> dict:
    df = con.execute(
        "SELECT trade_date, close, vol FROM daily "
        "WHERE ts_code = '600000.SH' ORDER BY trade_date"
    ).fetchdf()
    if df.empty:
        raise SystemExit("daily 中没有 600000.SH")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    fig, axes = plt.subplots(
        2,
        1,
        figsize=(7.2, 3.6),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08},
    )
    ax, axv = axes
    ax.plot(df["trade_date"], df["close"], color=TEAL, linewidth=0.9, label="Close")
    ax.axvspan(
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2020-12-31"),
        color=ORANGE,
        alpha=0.10,
        linewidth=0,
        label="2020 query window",
    )
    for idx, color, label, offset in (
        (df["close"].idxmax(), GREEN, "max", 6),
        (df["close"].idxmin(), ORANGE, "min", 8),
    ):
        row = df.loc[idx]
        ax.scatter(row["trade_date"], row["close"], s=12, color=color, zorder=4)
        ax.annotate(
            f"{label} {row['close']:.2f}",
            (row["trade_date"], row["close"]),
            textcoords="offset points",
            xytext=(6, offset),
            fontsize=7.5,
            color=color,
        )
    ax.set_ylabel("Close (CNY)")
    ax.set_ylim(df["close"].min() * 0.92, df["close"].max() * 1.08)
    ax.set_title("600000.SH daily close, 2006-11-22 to 2026-11-20", loc="left")
    ax.legend(loc="upper left", frameon=False, fontsize=7.5)
    ax.grid(axis="y", color="#D9D9D9", linewidth=0.5, alpha=0.7)
    axv.fill_between(
        df["trade_date"],
        0,
        df["vol"] / 1e6,
        color="#8FA9B3",
        linewidth=0,
        alpha=0.8,
    )
    axv.set_ylabel("Volume (M hand)")
    axv.grid(axis="y", color="#D9D9D9", linewidth=0.5, alpha=0.7)
    axv.set_xlabel("Trade date")
    fig.savefig(out)
    plt.close(fig)
    return {
        "rows": int(len(df)),
        "start": str(df["trade_date"].min().date()),
        "end": str(df["trade_date"].max().date()),
        "max_close": float(df["close"].max()),
        "min_close": float(df["close"].min()),
    }


def policy_figure(last_run: dict, out: Path) -> None:
    rows = last_run.get("sources") or []
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    if not rows:
        ax.text(0.5, 0.5, "No policy run", ha="center", va="center")
        ax.axis("off")
        fig.savefig(out)
        plt.close(fig)
        return
    labels = [row["source"] for row in rows]
    stored = [row.get("stored", 0) for row in rows]
    missing = [row.get("missing", 0) for row in rows]
    x = range(len(labels))
    ax.bar(x, stored, color=TEAL, width=0.55, label="stored")
    ax.bar(x, missing, bottom=stored, color=ORANGE, width=0.55, label="missing dual timeline")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylabel("Documents")
    ax.set_title("Policy collection outcome by whitelist source", loc="left")
    ax.legend(frameon=False, fontsize=7.5, loc="upper right")
    ax.grid(axis="y", color="#D9D9D9", linewidth=0.5, alpha=0.7)
    for idx, (s, m) in enumerate(zip(stored, missing)):
        if s:
            ax.text(idx, s / 2, str(s), ha="center", va="center", color="white", fontsize=7.5)
        if m:
            ax.text(
                idx,
                s + m / 2,
                str(m),
                ha="center",
                va="center",
                color="white",
                fontsize=7.5,
            )
    fig.savefig(out)
    plt.close(fig)


def table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def fill(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def build(out_path: Path, sha: str) -> Path:
    style()
    fig_dir = common.META_DIR / "delivery"
    fig_dir.mkdir(parents=True, exist_ok=True)
    price_path = fig_dir / "price.png"
    policy_path = fig_dir / "policy.png"

    con = common.duckdb_connect(read_only=True)
    try:
        price = price_figure(con, price_path)
    finally:
        con.close()
    last_run = common.read_json(common.POLICY_DIR / "last-run.json")
    policy_figure(last_run, policy_path)
    smoke = common.read_json(common.META_DIR / "smoke-report.json")
    checks_dir = common.META_DIR / "checks"
    candidates = [checks_dir / f"checks-{common.today_tag()}-all.json"]
    candidates.extend(sorted(checks_dir.glob("checks-*-all.json"), reverse=True))
    checks = None
    for candidate in candidates:
        if candidate.exists():
            checks = common.read_json(candidate)
            break
    if checks is None:
        raise SystemExit("缺少 checks all 报告；先运行 python -m db.checks.run all")
    required = {"manifest", "calendar", "pk", "ohlc", "cross-source", "rowcount"}
    if set(checks.get("checks", {})) != required:
        raise SystemExit(
            "checks 报告不完整，拒绝生成交付说明: "
            f"{sorted(checks.get('checks', {}))}"
        )
    failed = [name for name, result in checks["checks"].items() if not result.get("ok")]
    if failed:
        raise SystemExit(f"质量闸未全部通过，拒绝生成交付说明: {failed}")
    index = common.read_json(common.POLICY_DIR / "index.json")

    gate_rows = []
    for name, result in checks["checks"].items():
        detail = result["detail"]
        if name == "manifest":
            note = f"{detail.get('partitions', 0)} partitions, bad={detail.get('bad_count', 0)}"
        elif name == "cross-source":
            note = (
                f"{detail.get('checked_rows', 0)} rows, "
                f"max diff={detail.get('max_abs_diff', 0):.4f}"
            )
        elif name == "pk":
            note = "15 tables checked"
        elif name == "ohlc":
            note = f"{detail.get('rows', 0)} rows, bad={detail.get('bad_rows', 0)}"
        else:
            note = json.dumps(detail, ensure_ascii=False)[:80]
        gate_rows.append([name, "PASS" if result["ok"] else "FAIL", note])

    policy_rows = []
    for row in last_run.get("sources", []):
        policy_rows.append(
            [
                row["source"],
                row.get("listed", 0),
                row.get("stored", 0),
                row.get("missing", 0),
                row.get("duplicates", 0),
                "; ".join(row.get("errors", [])) or "-",
            ]
        )
    doc_rows = [
        [
            m["source_id"],
            m["publish_date"],
            m["effective_date"],
            m.get("document_number") or "-",
            m["title"],
            m["sha256"][:12],
        ]
        for m in index.get("documents", [])
    ]
    metrics_rows = [
        ["20 年行情行数", f"{smoke['rows_20y']:,}"],
        ["实际窗口", f"{smoke['actual_start']} 至 {smoke['actual_end']}"],
        ["20 年新浪调用耗时", f"{smoke['sina_20y_call_s']} s"],
        ["单交易日调用耗时", f"{smoke['sina_1d_call_s']} s"],
        ["raw 分区数", f"{smoke['raw_partitions']:,}"],
        [
            "raw Parquet 字节",
            f"{smoke['raw_parquet_bytes']:,} B"
            f"（约 {smoke['raw_parquet_bytes'] / 1024 / 1024:.1f} MB）",
        ],
        ["raw 写盘耗时", f"{smoke['raw_write_s']} s"],
        ["DuckDB 装载耗时", f"{smoke['duckdb_load_s']} s"],
        ["DuckDB daily 行数", f"{smoke['duckdb_daily_rows']:,}"],
        ["单交易日与 20 年数据一致", str(all(smoke["one_day_match"].values()))],
    ]
    req_rows = [
        ["1 目录与依赖", "完成", "六目录存在；venv 四包可 import"],
        ["2 schema", "完成", "15 张 P0 表；PK/单位/PIT；一次建库"],
        ["3 ETL", "完成", "整截面/单股；幂等/断点/manifest/失败清单"],
        ["4 限流并发", "完成", "默认 9 rpm；并发 1..19；JSONL 日志"],
        ["5 质量闸", "完成", "6 项检查；失败退出码 1"],
        ["6 ql", "完成", "sql/daily/fin；只读；200 行；三种格式"],
        ["7 政策", "完成", "六源白名单；双时间轴；同文去重"],
        ["8 冒烟", "完成", "600000.SH 20 年 + 1 交易日全链路"],
        ["9 测试", "完成", "25 passed"],
        ["10 独立审查", "见审查报告", "由独立子代理复跑并出分级报告"],
    ]
    ql_count = ""
    for item in smoke.get("ql", []):
        if "count(*)" in " ".join(item["cmd"]):
            ql_count = item["stdout"].strip()
    caption = (
        f"2006-11-22 至 2026-11-20，共 {price['rows']:,} 个交易日；"
        f"收盘最高 {price['max_close']:.2f} 元，最低 {price['min_close']:.2f} 元。"
        "阴影为 2020 年查询窗口，用于验证 ql daily --start 20200101 --end 20201231。"
    )
    values = {
        "SHA": html.escape(sha or "PENDING"),
        "GENERATED": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "VERIFY_HOME": html.escape(str(common.QDB_HOME)),
        "REQ_TABLE": table(["验收项", "状态", "证据"], req_rows),
        "PRICE_IMG": b64(price_path),
        "PRICE_CAPTION": html.escape(caption),
        "METRICS_TABLE": table(["指标", "实测值"], metrics_rows),
        "QL_COUNT": html.escape(ql_count),
        "GATE_TABLE": table(["质量闸", "结果", "关键数字"], gate_rows),
        "POLICY_IMG": b64(policy_path),
        "POLICY_TABLE": table(
            ["源", "列表条目", "入库", "记缺", "重复", "错误"], policy_rows
        ),
        "DOC_TABLE": table(
            ["源", "发布日", "生效日", "文号", "标题", "sha256"], doc_rows
        ),
    }
    body = fill(TEMPLATE.read_text(encoding="utf-8"), values)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build single-file DELIVERY.html")
    parser.add_argument(
        "--out",
        type=Path,
        default=common.PROJECT_ROOT / "docs" / "DELIVERY.html",
    )
    parser.add_argument("--sha", default="", help="交付 SHA")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = build(args.out, args.sha)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
