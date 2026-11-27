#!/usr/bin/env python3
"""一次性补丁：work557_train_all.py —— notes 容器 + 探针产物过滤 + notes 落盘。"""
import io

P = "scripts/work557_train_all.py"
t = io.open(P, encoding="utf-8", newline="").read()

subs = [
    ("    failures = []\n    stopped = None",
     "    failures = []\n    notes = []\n    stopped = None"),
    ('            if not fn.endswith(".json") or "_s" not in fn:\n                continue',
     '            if not fn.endswith(".json") or "_s" not in fn:\n'
     '                continue\n'
     '            if "_probe" in fn or "_smoke" in fn:   # 探针/冒烟产物不进训练表\n'
     '                continue'),
    ('    out["failures"] = failures',
     '    out["failures"] = failures\n    out["notes"] = notes'),
]
for old, new in subs:
    if new.splitlines()[-1] in t and new.splitlines()[0] in t:
        continue
    if old not in t:
        print("WARN anchor missing:", old.splitlines()[0][:60])
        continue
    t = t.replace(old, new, 1)

io.open(P, "w", encoding="utf-8", newline="").write(t)
print("patched", P)
