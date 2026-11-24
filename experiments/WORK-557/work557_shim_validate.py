#!/usr/bin/env python3
"""WORK-557：纯 numpy 替身 vs 真 cp39 .pyd 的逐元素对拍。

在 **py3.9 环境**（.venv39）里跑：
  * 真的 cp39 编译模块（.venv39\\Lib\\site-packages\\qlib\\data\\_libs\\*.pyd）
    用 importlib 按**模块名 "rolling"/"expanding"** 加载（CPython 的扩展模块
    初始化符号是 PyInit_rolling，模块名必须对上）。
  * 纯 numpy 替身（.venv/qlib312/qlib/data/_libs/rolling.py）直接把源码
    exec 到一个独立命名空间里，绕开包上下文（替身是自包含的）。
对随机输入逐元素比较。判据：max |diff| < 1e-12。

用法：
    .venv39\\Scripts\\python experiments/WORK-557/work557_shim_validate.py
"""
import importlib.util
import json
import pathlib
import sys
import types

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
SHIM_LIBS = ROOT / ".venv" / "qlib312" / "qlib" / "data" / "_libs"
REAL_LIBS = pathlib.Path(sys.prefix) / "Lib" / "site-packages" / "qlib" / "data" / "_libs"
TOL = 1e-12


def load_real(name):
    """Load a compiled extension under its own name (PyInit_<name>).

    The installed file carries the ABI tag, e.g. `rolling.cp39-win_amd64.pyd`,
    so glob for it; the *module name* must still be plain `rolling` because the
    CPython init symbol is `PyInit_rolling`.
    """
    cands = sorted(REAL_LIBS.glob(name + "*.pyd"))
    if not cands:
        raise FileNotFoundError("no %s*.pyd under %s" % (name, REAL_LIBS))
    print("  real %s -> %s" % (name, cands[0].name))
    spec = importlib.util.spec_from_file_location(name, cands[0])
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def load_shim(name):
    """Exec the pure-python shim source in an isolated namespace."""
    ns = {"__name__": "_shim_" + name}
    code = (SHIM_LIBS / (name + ".py")).read_text(encoding="utf-8")
    exec(compile(code, str(SHIM_LIBS / (name + ".py")), "exec"), ns)
    return types.SimpleNamespace(**ns)


def cmp(a, b):
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape:
        return float("inf"), {"shape_real": list(a.shape), "shape_shim": list(b.shape)}
    both_nan = np.isnan(a) & np.isnan(b)
    # C's IEEE arithmetic yields +-inf in degenerate windows (e.g. a window that
    # contains a NaN).  Identical infinities agree; |inf - inf| would be NaN.
    same_inf = np.isinf(a) & np.isinf(b) & (np.sign(a) == np.sign(b))
    with np.errstate(all="ignore"):
        diff = np.where(both_nan | same_inf, 0.0, np.abs(a - b))
    diff = np.where(np.isnan(diff), np.inf, diff)
    m = float(diff.max()) if diff.size else 0.0
    extra = {}
    if m > TOL:
        bad = np.argsort(-np.nan_to_num(diff, nan=np.inf))[:5]
        extra = {"sample_idx": bad.tolist(),
                 "real": a[bad].tolist(), "shim": b[bad].tolist(),
                 "real_nan": int(np.isnan(a).sum()),
                 "shim_nan": int(np.isnan(b).sum())}
    return m, extra


def main():
    print("real cp39 libs:", REAL_LIBS)
    real_roll = load_real("rolling")
    real_exp = load_real("expanding")
    shim_roll = load_shim("rolling")
    shim_exp = load_shim("expanding")
    print("loaded: real rolling/expanding (.pyd) + pure-python shim")

    rng = np.random.default_rng(20261124)
    n = 500
    walk = np.cumsum(rng.normal(0, 0.02, n)) + 10.0
    cases = [
        ("random_walk", walk),
        ("iid_normal", rng.normal(100, 5, n)),
        ("constant", np.full(n, 7.0)),
        ("with_nan", np.where(rng.random(n) < 0.02, np.nan, walk)),
        ("all_nan", np.full(n, np.nan)),
        ("short9", rng.normal(1, 1, 9)),
    ]

    report = {"tolerance": TOL, "max_abs_diff": 0.0, "cases": [], "pass": True}
    for label, arr in cases:
        for window in (2, 5, 10, 20, 60):
            if window > len(arr):
                continue
            for fname in ("slope", "rsquare", "resi"):
                real_fn = getattr(real_roll, "rolling_" + fname)
                shim_fn = getattr(shim_roll, "rolling_" + fname)
                m, extra = cmp(real_fn(np.asarray(arr, dtype=np.float64), window),
                               shim_fn(np.asarray(arr, dtype=np.float64), window))
                report["max_abs_diff"] = max(report["max_abs_diff"], m)
                if m > TOL:
                    report["pass"] = False
                    report["cases"].append({"case": label, "window": window,
                                            "fn": "rolling_" + fname,
                                            "max_abs_diff": m, **extra})
        for fname in ("slope", "rsquare", "resi"):
            real_fn = getattr(real_exp, "expanding_" + fname)
            shim_fn = getattr(shim_exp, "expanding_" + fname)
            m, extra = cmp(real_fn(np.asarray(arr, dtype=np.float64)),
                           shim_fn(np.asarray(arr, dtype=np.float64)))
            report["max_abs_diff"] = max(report["max_abs_diff"], m)
            if m > TOL:
                report["pass"] = False
                report["cases"].append({"case": label, "fn": "expanding_" + fname,
                                        "max_abs_diff": m, **extra})

    out = pathlib.Path(__file__).resolve().parent / "shim_validation.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nmax_abs_diff = %.3e  (tolerance %.0e)" % (report["max_abs_diff"], TOL))
    print("failing cases:", len(report["cases"]))
    for c in report["cases"][:6]:
        print("  ", json.dumps(c, ensure_ascii=False)[:400])
    print("PASS =", report["pass"])
    print("wrote ->", out)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
