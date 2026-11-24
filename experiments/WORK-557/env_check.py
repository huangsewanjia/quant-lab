"""WORK-557 段1：环境自检（包版本 / 线程 / 路径可读）。只做只读探测，不改任何东西。"""
import importlib
import os
import platform
import sys

print("=== python ===")
print(sys.version)
print(platform.platform())
print("executable:", sys.executable)

print("\n=== packages ===")
for m in [
    "duckdb", "pandas", "numpy", "qlib", "torch", "matplotlib", "scipy",
    "statsmodels", "sklearn", "tqdm", "yaml", "plotly", "playwright", "pdfkit",
    "weasyprint", "pyarrow", "requests", "bs4", "lxml",
]:
    try:
        mod = importlib.import_module(m)
        print("%-14s OK   %s" % (m, getattr(mod, "__version__", "?")))
    except Exception as e:
        print("%-14s MISSING (%s)" % (m, type(e).__name__))

print("\n=== cpu / env ===")
print("logical cpus:", os.cpu_count())
for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    print("%s = %s" % (k, os.environ.get(k)))

print("\n=== torch / cuda ===")
try:
    import torch
    print("torch:", torch.__version__)
    print("cuda available:", torch.cuda.is_available())
    print("cuda version:", torch.version.cuda)
    if torch.cuda.is_available():
        print("device 0:", torch.cuda.get_device_name(0))
        cap = torch.cuda.get_device_capability(0)
        print("capability: sm_%d%d" % cap)
        total = torch.cuda.get_device_properties(0).total_memory
        print("total mem: %.2f GB" % (total / 1024 ** 3))
        free, total2 = torch.cuda.mem_get_info()
        print("free mem: %.2f GB / total %.2f GB" % (free / 1024 ** 3, total2 / 1024 ** 3))
except Exception as e:
    print("torch probe failed:", repr(e))

print("\n=== Z: paths ===")
for p in [
    r"Z:\quantdb\README.md",
    r"Z:\quantdb\db\quantdb.duckdb",
    r"Z:\quantdb\raw\daily",
    r"Z:\quantdb\raw\index_weight",
    r"Z:\quantdb\raw\adj_factor",
    r"Z:\quantdb\raw\stk_limit",
    r"Z:\quantdb\raw\suspend_d",
    r"Z:\quantdb\raw\daily_basic",
    r"Z:\quantdb\raw\stock_basic",
    r"Z:\quantdb\raw\trade_cal",
    r"Z:\quantdb\raw\index_daily",
]:
    try:
        ok = os.path.exists(p)
        extra = ""
        if os.path.isdir(p):
            names = sorted(os.listdir(p))
            extra = " entries=%d e.g. %s" % (len(names), names[:3])
        print("%-45s exists=%s%s" % (p, ok, extra))
    except Exception as e:
        print("%-45s ERROR %r" % (p, e))
