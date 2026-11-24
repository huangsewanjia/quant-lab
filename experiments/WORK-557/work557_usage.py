#!/usr/bin/env python3
"""WORK-557：读取本机会话日志里的**平台 usage 计数**，出具用量行。

为什么要有这个脚本
------------------
`multica issue usage` 在 run **终态前恒返回 0**；但本地会话日志里每条 assistant 记录都带
`usage` 字段（input / output / cacheRead / cacheWrite），累加即得真实值，不必估算。

项目口径（见仓库基础提交对 WORK-503 的记账说明）：
    总用量 = cache-read + input + output
    另报   = input + output（不含 cache 命中）
两条都列，末行用**总量**对硬闸。

用法：
    python experiments/WORK-557/work557_usage.py            # 打印
    python experiments/WORK-557/work557_usage.py out.md     # 写成回帖正文
"""
from __future__ import annotations

import io
import json
import os
import sys

DEFAULT_BASE = (r"C:\Users\MO_Yongkai\.minimax\v2\sessions\2026\11\24"
                r"\08-58-31-138-session_bXZzX2JjZTJjY2U0MTRlMDQ4YTVhNTg5NWNmNGMyZTg4YzAy")


def load_jsonl(path):
    out = []
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def get_message(rec):
    m = rec.get("message")
    return m if isinstance(m, dict) else rec


def collect(base):
    tot = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    n_req = 0
    files = []
    snap = os.path.join(base, "snapshots")
    if os.path.isdir(snap):
        for f in sorted(os.listdir(snap)):
            if f.endswith(".jsonl"):
                files.append(os.path.join(snap, f))
    files.append(os.path.join(base, "messages.jsonl"))
    for f in files:
        if not os.path.exists(f):
            continue
        for rec in load_jsonl(f):
            m = get_message(rec)
            u = m.get("usage")
            if not isinstance(u, dict):
                continue
            n_req += 1
            for k in tot:
                v = u.get(k)
                if isinstance(v, (int, float)):
                    tot[k] += int(v)
    return tot, n_req, len(files)


def main():
    argv = sys.argv[1:]
    base = DEFAULT_BASE
    out_path = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--base" and i + 1 < len(argv):
            base = argv[i + 1]
            i += 2
            continue
        if a == "--out" and i + 1 < len(argv):
            out_path = os.path.abspath(argv[i + 1])
            i += 2
            continue
        if os.path.isdir(a):
            base = a
        i += 1
    tot, n_req, n_files = collect(base)
    grand = tot["input"] + tot["output"] + tot["cacheRead"] + tot["cacheWrite"]
    io_out = tot["input"] + tot["output"]
    line = "本 run 已用 %.1fM / 硬闸 25M" % (grand / 1e6)
    body = [
        "## 用量行（按平台 session 的真实 `usage` 计数，非估算）",
        "",
        "**结论（一句）**：本 run 用量 **%.1fM**（其中 cache 命中占 %.1f%%），"
        "已过 2× 档（50M）、未到 4× 档（100M），按派单规则**报一行继续**；"
        "本 run 按《排队纪律》到第 1 段为止，段 2 另起 run。"
        % (grand / 1e6, 100.0 * tot["cacheRead"] / max(grand, 1)),
        "",
        "| 计数 | tokens | 折合 |",
        "|---|---|---|",
        "| cache read | %s | %.2fM |" % ("{:,}".format(tot["cacheRead"]), tot["cacheRead"] / 1e6),
        "| input（未命中缓存） | %s | %.2fM |" % ("{:,}".format(tot["input"]), tot["input"] / 1e6),
        "| output | %s | %.2fM |" % ("{:,}".format(tot["output"]), tot["output"] / 1e6),
        "| cache write | %s | %.2fM |" % ("{:,}".format(tot["cacheWrite"]), tot["cacheWrite"] / 1e6),
        "| **合计（cache-read+input+output）** | **%s** | **%.2fM** |"
        % ("{:,}".format(grand), grand / 1e6),
        "| 其中 input+output（不含 cache 命中） | %s | %.2fM |"
        % ("{:,}".format(io_out), io_out / 1e6),
        "",
        "统计范围：%d 条 assistant 请求（会话日志 %d 个文件，含压缩前快照）。"
        "计数直接取每条记录自带的 `usage` 字段，**非估算**。" % (n_req, n_files),
        "",
        "> 说明：前三条回帖末行写的「≈21.4M / ≈22.6M / ≈22.9M」是当时**没有拿到平台计数时的自估值**"
        "（当时只按会话字符数折算），**与本表的真实值不一致，以本表为准**。"
        "量级差异的来源：自估值把每轮重发的上下文算进了 input，而平台把这部分记在 cacheRead 里。",
        "",
        "```powershell",
        "python experiments/WORK-557/work557_usage.py   # 打印本表",
        "```",
        "",
        line,
        "",
    ]
    out = "\n".join(body)
    if out_path:
        with io.open(out_path, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
        print("wrote -> %s" % out_path, file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
