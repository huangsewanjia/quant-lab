import json
import pathlib

E = pathlib.Path("experiments/WORK-503")


def keys(d, prefix="", depth=0):
    if depth > 2:
        return
    if isinstance(d, dict):
        for k, v in list(d.items())[:14]:
            kind = type(v).__name__
            extra = ""
            if isinstance(v, (int, float, str)) and len(str(v)) < 60:
                extra = " = %s" % v
            print("%s%s (%s)%s" % (prefix, k, kind, extra))
            if isinstance(v, dict):
                keys(v, prefix + "  ", depth + 1)


v = json.load(open(E / "verdict.json", encoding="utf-8"))
print("=== verdict.json ===")
keys(v)
print()
b = json.load(open(E / "backtest_results.json", encoding="utf-8"))
print("=== backtest_results.json ===")
keys(b)
print()
print("=== preds/ ===")
for p in sorted((E / "preds").glob("*"))[:40]:
    print("  ", p.name)
