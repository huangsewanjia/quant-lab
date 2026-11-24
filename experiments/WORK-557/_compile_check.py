"""Compile-check every WORK-557 script and print the key protocol settings."""
import pathlib
import py_compile
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
files = sorted(ROOT.glob("scripts/work557_*.py")) + [ROOT / "audit" / "recompute_work557.py"]
bad = 0
for f in files:
    try:
        py_compile.compile(str(f), doraise=True)
        print("OK   ", f.relative_to(ROOT))
    except Exception as e:
        bad += 1
        print("FAIL ", f.relative_to(ROOT), "->", e)
print("\n%d files, %d failures" % (len(files), bad))
sys.exit(1 if bad else 0)
