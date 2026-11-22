"""Sina via the local akshare installation.

The akshare venv is intentionally separate from the quantdb venv: it carries
the exact akshare build measured in WORK-475/512, while quantdb only needs
DuckDB/Parquet. Calls are made in a subprocess and return JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pandas as pd

AKSHARE_PYTHON = os.environ.get(
    "AKSHARE_PYTHON", "/home/moyongkai/mcp/akshare-one-mcp/bin/python"
)

_WORKER = r"""
import json, sys
import akshare as ak
symbol = sys.argv[1]
df = ak.stock_zh_a_hist_sina(symbol=symbol)
df["date"] = df["date"].astype(str)
print(df.to_json(orient="records", force_ascii=False))
"""


def fetch_sina_history(symbol: str) -> pd.DataFrame:
    path = Path(AKSHARE_PYTHON)
    if not path.exists():
        raise RuntimeError(f"akshare python 不存在: {path}")
    proc = subprocess.run(
        [str(path), "-c", _WORKER, symbol],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"akshare 新浪接口失败 {symbol}: {proc.stderr.strip()[-800:]}"
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"新浪返回非 JSON: {proc.stdout[:300]}") from exc
    df = pd.DataFrame(payload)
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["date"]).dt.date
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def to_canonical_daily(df: pd.DataFrame, ts_code: str) -> pd.DataFrame:
    """Map Sina units to Tushare-compatible daily units.

    Sina volume is shares -> /100 = hands (vol).
    Sina amount is yuan   -> /1000 = thousand yuan (amount).
    """
    if df.empty:
        return pd.DataFrame(
            columns=[
                "ts_code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "change",
                "pct_chg",
                "vol",
                "amount",
            ]
        )
    work = df.copy().sort_values("trade_date").reset_index(drop=True)
    work["ts_code"] = ts_code
    work["pre_close"] = work["close"].shift(1)
    work["change"] = work["close"] - work["pre_close"]
    work["pct_chg"] = work["change"] / work["pre_close"] * 100.0
    work["vol"] = work["volume"] / 100.0
    work["amount"] = work["amount"] / 1000.0
    return work[
        [
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "change",
            "pct_chg",
            "vol",
            "amount",
        ]
    ]
