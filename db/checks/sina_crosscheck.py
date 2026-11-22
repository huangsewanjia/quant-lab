"""Cross-check helper: close prices from the Sina source (akshare).

Runs under the akshare virtualenv, not the quantdb one:

    /home/moyongkai/mcp/akshare-one-mcp/bin/python db/checks/sina_crosscheck.py

Reads a JSON request on stdin and writes a JSON result on stdout:

    in : {"pairs": [["sh600000", "20200102"], ...]}
    out: {"sh600000": {"20200102": 12.47}, ...}

One Sina request per symbol covers the symbol's whole history, so 20 sample
trading days cost 5 upstream calls instead of 100.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    pairs = request.get("pairs") or []
    wanted: dict[str, set[str]] = {}
    for symbol, day in pairs:
        wanted.setdefault(symbol, set()).add(str(day))

    try:
        import akshare as ak
    except Exception as exc:  # noqa: BLE001
        json.dump({"error": f"akshare unavailable: {exc}"}, sys.stdout)
        return 2

    result: dict[str, dict[str, float]] = {}
    errors: dict[str, str] = {}
    for symbol, days in wanted.items():
        try:
            frame = ak.stock_zh_a_hist_sina(symbol=symbol)
        except Exception as exc:  # noqa: BLE001
            errors[symbol] = f"{type(exc).__name__}: {exc}"
            continue
        if frame is None or frame.empty:
            errors[symbol] = "empty response"
            continue
        date_column = "date" if "date" in frame.columns else "日期"
        close_column = "close" if "close" in frame.columns else "收盘"
        frame = frame.copy()
        frame["_day"] = (
            frame[date_column].astype(str).str.replace("-", "", regex=False).str[:8]
        )
        subset = frame[frame["_day"].isin(days)]
        result[symbol] = {
            str(row["_day"]): float(row[close_column])
            for _, row in subset.iterrows()
            if row[close_column] is not None
        }

    json.dump({"closes": result, "errors": errors}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
