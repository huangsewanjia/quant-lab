# -*- coding: utf-8 -*-
"""
WORK-555 · 个股层面板快照构建（金融数据工程师阶段）

从本机 quantdb（只读）抽取 DEE 复现所需面板，落到 data/WORK-555/ 原始层（只追加），
并写 experiments/WORK-555/snapshot_manifest.json（每端点 ok/error 原文、行数、sha256）。

口径全部照抄 experiments/WORK-555/preregistration.md v1.0：
  - 窗口 2006-01-04 ~ 2021-01-29（训练 2008 起，含 60 日因子预热与次日标签）
  - 股票池 = index_weight('000300.SH') 成分并集；PIT 月末快照
  - 复权一律用视图 v_daily_hfq（字段字典 §2：不要自己算）
  - 不取任何基本面字段 → 不涉及 ann_date 时点
  - 单位：vol=手、amount=千元（字段字典 §1）

运行：
  python scripts/work555_build_panel.py
"""
import hashlib
import json
import os
import sys
import time

import duckdb
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
DATA = os.path.join(REPO, "data", EXP)
OUT = os.path.join(REPO, "experiments", EXP)
DB = "/srv/share/quantdb/db/quantdb.duckdb"

WIN_START = "2006-01-01"
WIN_END = "2021-01-29"
INDEX_CODE = "000300.SH"

os.makedirs(DATA, exist_ok=True)
os.makedirs(OUT, exist_ok=True)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    t0 = time.time()
    con = duckdb.connect(DB, read_only=True)
    manifest = {
        "experiment": EXP,
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "window": {"start": WIN_START, "end": WIN_END},
        "paths_are": "repo-relative (relative to repository root)",
        "source_db": {
            "path": DB,
            "access": "duckdb read_only=True",
            "views_used": ["v_daily_hfq"],
        },
        "prereg": "experiments/WORK-555/preregistration.md v1.0 (commit 9a85409)",
        "items": [],
    }

    uni_sql = (
        "SELECT DISTINCT con_code AS ts_code FROM index_weight "
        f"WHERE index_code='{INDEX_CODE}' AND trade_date BETWEEN '{WIN_START}' AND '{WIN_END}'"
    )
    n_uni = con.execute(f"SELECT count(*) FROM ({uni_sql})").fetchone()[0]

    # ---------------------------------------------------------------- 5 个端点
    specs = [
        (
            "panel_daily",
            "个股日线 + hfq 复权价（视图 v_daily_hfq）+ 成交量/成交额",
            f"""
            SELECT d.ts_code, d.trade_date, d.open_hfq, d.high_hfq, d.low_hfq, d.close_hfq,
                   d."close" AS close_raw, d.pre_close, d.vol, d.amount, d.pct_chg
            FROM v_daily_hfq d
            JOIN ({uni_sql}) u ON d.ts_code = u.ts_code
            WHERE d.trade_date BETWEEN '{WIN_START}' AND '{WIN_END}'
            ORDER BY d.ts_code, d.trade_date
            """,
            ["ts_code", "trade_date"],
        ),
        (
            "panel_stk_limit",
            "涨跌停价（up_limit/down_limit），仅用于回测可成交判定，不进模型特征",
            f"""
            SELECT s.ts_code, s.trade_date, s.up_limit, s.down_limit
            FROM stk_limit s JOIN ({uni_sql}) u ON s.ts_code = u.ts_code
            WHERE s.trade_date BETWEEN '{WIN_START}' AND '{WIN_END}'
            ORDER BY s.ts_code, s.trade_date
            """,
            ["ts_code", "trade_date"],
        ),
        (
            "panel_suspend",
            "停复牌事件（suspend_d），仅用于可成交判定与标签有效性",
            f"""
            SELECT s.ts_code, s.trade_date, s.suspend_type, s.suspend_timing
            FROM suspend_d s JOIN ({uni_sql}) u ON s.ts_code = u.ts_code
            WHERE s.trade_date BETWEEN '{WIN_START}' AND '{WIN_END}'
            ORDER BY s.ts_code, s.trade_date
            """,
            ["ts_code", "trade_date"],
        ),
        (
            "panel_index_weight",
            f"沪深300 成分权重月末快照（PIT 成分来源）",
            f"""
            SELECT index_code, con_code, trade_date, weight
            FROM index_weight
            WHERE index_code='{INDEX_CODE}' AND trade_date BETWEEN '{WIN_START}' AND '{WIN_END}'
            ORDER BY trade_date, con_code
            """,
            ["con_code", "trade_date"],
        ),
        (
            "panel_stock_basic",
            "股票主档（含 list_date，用于上市满 60 个交易日过滤）",
            f"""
            SELECT b.ts_code, b.name, b.list_date, b.market
            FROM stock_basic b JOIN ({uni_sql}) u ON b.ts_code = u.ts_code
            ORDER BY b.ts_code
            """,
            ["ts_code"],
        ),
    ]

    for name, desc, sql, pk in specs:
        item = {
            "name": name,
            "description": desc,
            "sql": " ".join(sql.split()),
            "primary_key": pk,
            "path": f"data/{EXP}/{name}.csv",
        }
        try:
            df = con.execute(sql).df()
            path = os.path.join(DATA, f"{name}.csv")
            df.to_csv(path, index=False)
            item.update(
                ok=True,
                rows=int(len(df)),
                sha256=sha256_of(path),
                size_bytes=int(os.path.getsize(path)),
            )
            for col, tag in (("trade_date", "date"),):
                if col in df.columns:
                    item[f"{tag}_min"] = str(pd.to_datetime(df[col]).min().date())
                    item[f"{tag}_max"] = str(pd.to_datetime(df[col]).max().date())
                    break
            if "ts_code" in df.columns:
                item["n_ts_code"] = int(df["ts_code"].nunique())
            if "con_code" in df.columns:
                item["n_ts_code"] = int(df["con_code"].nunique())
            # 主键唯一性质量门（字段字典 §7）
            if len(pk) == 2:
                dup = int(df.duplicated(subset=pk).sum())
            else:
                dup = int(df.duplicated(subset=pk).sum())
            item["pk_duplicate_rows"] = dup
            print(f"[OK ] {name}: rows={len(df):,} sha256={item['sha256'][:16]}… dup_pk={dup}")
        except Exception as exc:  # 保留条目 + 原文错误，不删条目
            item.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            print(f"[ERR] {name}: {item['error']}", file=sys.stderr)
        manifest["items"].append(item)

    manifest["universe_size"] = int(n_uni)
    manifest["elapsed_sec"] = round(time.time() - t0, 1)

    man_path = os.path.join(OUT, "snapshot_manifest.json")
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print(f"\nmanifest → {man_path}  ({manifest['elapsed_sec']}s)")
    n_bad = sum(1 for it in manifest["items"] if not it["ok"])
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
