"""List site-packages distributions whose RECORD-listed files are missing.

Read-only forensics.  Usage:
  .venv\\Scripts\\python.exe experiments\\WORK-557\\_env_damage_scan.py <site-packages-dir> [...]
"""
import csv
import json
import os
import sys


def scan(sp):
    out = []
    try:
        names = sorted(os.listdir(sp))
    except OSError as e:
        return [{"site_packages": sp, "error": str(e)}]
    for name in names:
        if not name.endswith(".dist-info"):
            continue
        rec = os.path.join(sp, name, "RECORD")
        if not os.path.exists(rec):
            continue
        missing, total = [], 0
        with open(rec, encoding="utf-8", errors="replace") as fh:
            for row in csv.reader(fh):
                if not row:
                    continue
                rel = row[0]
                total += 1
                p = os.path.join(sp, rel.replace("/", os.sep))
                if not os.path.exists(p):
                    missing.append(rel)
        if missing:
            out.append({
                "dist": name,
                "total": total,
                "missing": len(missing),
                "missing_py": sum(1 for m in missing if m.endswith(".py")),
                "examples": missing[:6],
            })
    return out


if __name__ == "__main__":
    res = {}
    for sp in sys.argv[1:]:
        res[sp] = scan(sp)
    print(json.dumps(res, ensure_ascii=False, indent=1))
