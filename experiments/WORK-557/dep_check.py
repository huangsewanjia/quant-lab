"""WORK-557 段1：依赖自检 —— 上游 trainer 需要什么、本机有什么、能否装。"""
import importlib
import subprocess
import sys

NEED = [
    "pytorch_lightning", "hydra", "omegaconf", "wandb", "qlib",
    "torch", "numpy", "pandas", "duckdb", "scipy", "sklearn", "matplotlib",
]
print("=== installed (current interpreter) ===")
for m in NEED:
    try:
        mod = importlib.import_module(m)
        print("%-18s OK   %s" % (m, getattr(mod, "__version__", "?")))
    except Exception as e:
        print("%-18s MISSING (%s)" % (m, type(e).__name__))

print("\n=== pip index availability ===")
for pkg in ["pyqlib", "pytorch-lightning", "hydra-core", "omegaconf", "wandb"]:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "index", "versions", pkg],
            capture_output=True, text=True, timeout=180,
        )
        line = (r.stdout or r.stderr).strip().splitlines()
        print("%-18s -> %s" % (pkg, line[0] if line else "(no output)"))
    except Exception as e:
        print("%-18s -> ERR %r" % (pkg, e))

print("\n=== upstream trainer imports (read from vendor sources) ===")
import re
import pathlib
root = pathlib.Path("vendor/PRISM-VQ")
for f in sorted(root.glob("trainer/train_*.py")) + [root / "utils" / "test.py",
                                                    root / "utils" / "metric.py",
                                                    root / "utils" / "__init__.py"]:
    src = f.read_text(encoding="utf-8")
    mods = sorted(set(re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", src, re.M)))
    print("%-40s %s" % (f.as_posix(), ", ".join(mods)))
