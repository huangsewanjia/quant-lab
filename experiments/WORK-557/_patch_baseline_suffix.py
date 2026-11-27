#!/usr/bin/env python3
"""一次性补丁：给 work557_baseline.py 加 --out-suffix（探针跑不污染正式产物）。"""
import io

P = "scripts/work557_baseline.py"
t = io.open(P, encoding="utf-8", newline="").read()

old_arg = '    ap.add_argument("--workers", type=int, default=2)'
new_arg = ('    ap.add_argument("--workers", type=int, default=2)\n'
           '    ap.add_argument("--out-suffix", default="",\n'
           '                    help="产物文件名后缀（探针跑用 _probe，避免覆盖正式结果）")')
if "--out-suffix" not in t:
    assert old_arg in t, "anchor --workers not found"
    t = t.replace(old_arg, new_arg, 1)

old_name = 'f"{args.kind}_s{args.seed}.json"'
new_name = 'f"{args.kind}_s{args.seed}{args.out_suffix}.json"'
if new_name not in t:
    assert old_name in t, "anchor output name not found"
    t = t.replace(old_name, new_name, 1)

io.open(P, "w", encoding="utf-8", newline="").write(t)
print("patched", P)
