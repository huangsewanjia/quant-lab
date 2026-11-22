#!/usr/bin/env python3
"""Enumerate the 213 registered legacy factors and their market-data field needs.

Purpose
-------
ML-Quant (arXiv:2507.07107v2) claims a 213-factor engine. The official
implementation registers them through the ``@register_legacy_factor("<name>")``
decorator in ``src/mlquant/features/_factors_*.py``. This script statically
resolves, for every registered factor, **which ``Panel`` market fields the
factor actually reads** — so a data-export spec can be written against a
concrete list instead of a guess.

Method (all auditable, standard library only)
--------------------------------------------
1. Parse each ``_factors_*.py``; collect functions decorated with
   ``@register_legacy_factor("name")`` -> (factor name, definition line).
2. Walk the function AST for ``panel.<attr>`` attribute accesses whose attr is
   a market field; ``panel.mask`` and other non-field attributes are ignored.
3. Recurse into **same-module** helper functions, then into **cross-module**
   helpers imported from ``legacy_factors.py`` (``_close_loc`` -> high/low/
   close, ``_amount`` -> amount/vwap/volume, ``_ret`` -> close).
4. ``panel.returns`` is attributed to ``close`` + ``last_close``.

Usage
-----
    python scripts/enumerate_mlquant_factors.py \
        --repo /path/to/ml-quant-trading \
        --out  docs/mlquant_factor_fields.csv

The repo path must be a checkout of ``initial-d/ml-quant-trading``. The script
never imports torch; it only parses source text.
"""
from __future__ import annotations

import argparse
import ast
import csv
import os
import sys
from collections import defaultdict

# Panel fields that carry market data (see src/mlquant/data/panel.py:35-36).
DATA_FIELDS = {
    "open", "high", "low", "close", "volume", "vwap",
    "amount", "limit_up", "limit_down", "last_close",
}
# Attributes that are derived, not raw fields.
DERIVED = {
    "mask", "returns", "has_real_limits", "field", "to", "slice_dates",
    "assert_consistent", "n_dates", "n_stocks", "device",
}

# Paper Table 1 family -> (module, theme).
FAMILIES = {
    "_factors_alpha101.py": ("alpha_*", "Alpha101 子集（论文 §3.4 只取 9 条）"),
    "_factors_better.py": ("better_*", "VWAP 偏离 / 量价动量"),
    "_factors_best.py": ("best_*", "收盘位置（close-location）动量"),
    "_factors_old.py": ("old_*", "秩相关复合（legacy alpha 信号）"),
    "_factors_stock.py": ("stock_*", "个股派生（波动 / CCI / 区间）"),
    "_factors_extra.py": ("extra_*", "换手与成交额"),
    "_factors_add.py": ("add_*", "复合补充因子"),
    "_factors_change.py": ("change_*", "短窗加速度"),
    "_factors_original.py": ("original_*", "收盘/成交量直接统计"),
    "_factors_market.py": ("cs_rank_*", "截面市场宽度"),
}

# Field -> new-library source column (quantdb raw).
SOURCE = {
    "open": "daily.open",
    "high": "daily.high",
    "low": "daily.low",
    "close": "daily.close",
    "volume": "daily.vol(手→股)",
    "vwap": "daily.amount÷daily.vol(派生)",
    "amount": "daily.amount(千元→元)",
}

ORDER = ["open", "high", "low", "close", "volume", "vwap"]


def parse_module(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    return src, ast.parse(src, filename=path)


def module_functions(path):
    """-> (source, {func_name: FunctionDef}, [(factor, func, line), ...])."""
    src, tree = parse_module(path)
    funcs, registry = {}, []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        funcs[node.name] = node
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
                    and dec.func.id == "register_legacy_factor"
                    and dec.args and isinstance(dec.args[0], ast.Constant)
                    and isinstance(dec.args[0].value, str)):
                registry.append((dec.args[0].value, node.name, node.lineno))
    return src, funcs, registry


def resolve_fields(node, local_funcs, global_funcs, seen):
    """Panel market fields read by ``node``, following helper calls."""
    out = set()
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                and sub.value.id == "panel"):
            if sub.attr in DATA_FIELDS:
                out.add(sub.attr)
            elif sub.attr == "returns":
                out.update({"close", "last_close"})
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            fn = sub.func.id
            if fn in seen:
                continue
            seen.add(fn)
            if fn in local_funcs:
                out |= resolve_fields(local_funcs[fn], local_funcs, global_funcs, seen)
            elif fn in global_funcs:
                gpath, gnode = global_funcs[fn]
                _, gfuncs, _ = module_functions(gpath)
                out |= resolve_fields(gnode, gfuncs, global_funcs, seen)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="checkout of initial-d/ml-quant-trading")
    ap.add_argument("--out", default="docs/mlquant_factor_fields.csv")
    args = ap.parse_args()

    feat_dir = os.path.join(args.repo, "src", "mlquant", "features")
    legacy = os.path.join(feat_dir, "legacy_factors.py")
    if not os.path.isdir(feat_dir):
        print(f"not a ml-quant-trading checkout: {feat_dir}", file=sys.stderr)
        return 2

    _, legacy_funcs, _ = module_functions(legacy)
    global_funcs = {name: (legacy, node) for name, node in legacy_funcs.items()}

    rows = []
    for fname in sorted(os.listdir(feat_dir)):
        if not fname.startswith("_factors_"):
            continue
        path = os.path.join(feat_dir, fname)
        _, funcs, registry = module_functions(path)
        family, theme = FAMILIES.get(fname, (fname.replace("_factors_", ""), ""))
        for factor, func_name, line in registry:
            fields = resolve_fields(funcs[func_name], funcs, global_funcs, set())
            req = [f for f in ORDER if f in fields]
            reads_amount = "amount" in fields
            if not fields & {"vwap", "amount"}:
                bucket = "OHLCV"
            elif reads_amount:
                bucket = "VWAP+成交额"
            else:
                bucket = "VWAP"
            rows.append({
                "factor": factor,
                "family": family,
                "theme": theme,
                "module": f"src/mlquant/features/{fname}",
                "def_line": line,
                "requires_fields": "+".join(req),
                "n_fields": len(req),
                "reads_panel_amount": "Y" if reads_amount else "N",
                "bucket": bucket,
                "newlib_sources": "; ".join(
                    [SOURCE[f] for f in req] +
                    (["daily.amount(千元→元)"] if reads_amount else [])),
            })

    rows.sort(key=lambda r: (r["family"], r["factor"]))
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    counts = defaultdict(int)
    for r in rows:
        counts[r["bucket"]] += 1
    print(f"registered factors: {len(rows)}")
    for k in sorted(counts):
        print(f"  {k}: {counts[k]}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
