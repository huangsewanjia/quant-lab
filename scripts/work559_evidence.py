#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WORK-559 · 采集夜间备份的实测证据到 docs/WORK-559/evidence.json。

只读：不启动服务、不改 dump、不碰 borg 仓库内容（`borg list/info` 只读）。
每一条都记录「命令 + 原始输出」，交付页与报告直接引用，不手抄数字。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "docs", "WORK-559", "evidence.json")
DUMPS = "/srv/backup/dumps"
ZERO = os.path.join(DUMPS, "_zero-byte-20261120-20261125")
LOG_DIR = "/srv/share/quantdb/logs"
UNIT = "backup-nightly.service"


def run(cmd: list[str], env: dict | None = None, timeout: int = 180) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    out = (p.stdout or "") + (p.stderr or "")
    return out.strip()


def borg_env() -> dict:
    env = dict(os.environ)
    env["BORG_REPO"] = "/srv/backup/borg"
    env["BORG_PASSCOMMAND"] = (
        "systemd-creds --user decrypt %s/.config/multica/secrets/BORG_PASSPHRASE.cred" % os.path.expanduser("~")
    )
    env["BORG_RELOCATED_REPO_ACCESS_IS_OK"] = "no"
    return env


def parse_runs() -> list[dict]:
    """把 backup-YYYYMMDD.log 切成一次运行一块：起点行到下一处 backup start。"""
    runs: list[dict] = []
    for name in sorted(os.listdir(LOG_DIR)):
        m = re.fullmatch(r"backup-(\d{8})\.log", name)
        if not m:
            continue
        path = os.path.join(LOG_DIR, name)
        blocks, cur = [], []
        for line in open(path, encoding="utf-8", errors="replace"):
            if "backup start" in line and cur:
                blocks.append(cur)
                cur = []
            cur.append(line.rstrip("\n"))
        if cur:
            blocks.append(cur)
        for b in blocks:
            text = "\n".join(b)
            start = re.search(r"^\[([^\]]+)\] backup start", text)
            end = re.search(r"^\[([^\]]+)\] backup (ok|失败 exit=(\d+))", text, re.M)
            dumps = re.findall(r"dump 完成：(\w+) (\d+) 字节 → (\S+)", text)
            runs.append(
                {
                    "log": path,
                    "start": start.group(1) if start else None,
                    "end": end.group(1) if end else None,
                    "result": ("ok" if end and end.group(2) == "ok" else
                               ("fail" if end else "no-terminal-line")),
                    "exit": int(end.group(3)) if end and end.group(3) else (0 if end and end.group(2) == "ok" else None),
                    "dumps": [{"label": d[0], "bytes": int(d[1]), "path": d[2]} for d in dumps],
                    "lines": b,
                }
            )
    # 只保留与本次事故相关的一天以前 + 今天
    return runs


def dump_facts() -> dict:
    facts = {}
    for label in ("multica", "weknora", "langfuse"):
        cands = [f for f in os.listdir(DUMPS) if f.startswith(label + "-") and f.endswith(".dump")]
        cands.sort(key=lambda f: os.path.getmtime(os.path.join(DUMPS, f)), reverse=True)
        if not cands:
            facts[label] = {"error": "no dump"}
            continue
        path = os.path.join(DUMPS, cands[0])
        toc = run(["pg_restore", "-l", path])
        st = os.stat(path)
        facts[label] = {
            "name": cands[0],
            "path": path,
            "bytes": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "ls": "%s  1 %s" % (
                datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "%d bytes" % st.st_size,
            ),
            "toc_head": "\n".join(toc.splitlines()[:5]),
            "toc_lines": len([ln for ln in toc.splitlines() if ln and not ln.startswith(";")]),
            "toc_cmd": "pg_restore -l %s | head -5" % path,
        }
    return facts


def borg_facts() -> dict:
    env = borg_env()
    listing = run(["borg", "list", "--short"], env=env)
    names = [n for n in listing.splitlines() if n.strip()]
    newest = names[-1] if names else None
    info = run(["borg", "info", "::" + newest], env=env) if newest else ""
    return {
        "list_tail": "\n".join(names[-6:]),
        "list_count": len(names),
        "newest": newest,
        "info": info,
        "cmd": "borg list --short | tail -6",
    }


def unit_facts() -> dict:
    props = ["Result", "ExecMainStatus", "SubState", "ActiveState",
             "ExecMainStartTimestamp", "ExecMainExitTimestamp", "TimeoutStartUSec"]
    out = run(["systemctl", "--user", "show", UNIT] + sum([["-p", p] for p in props], []))
    parsed = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {
        "props": parsed,
        "cmd": "systemctl --user show %s -p Result -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp" % UNIT,
        "raw": out,
        "journal_failures": run([
            "journalctl", "--user", "-u", UNIT, "--no-pager",
            "--since", "2026-11-19 00:00", "--until", "2026-11-25 00:00",
        ]),
    }


def zero_facts() -> dict:
    if not os.path.isdir(ZERO):
        return {"exists": False}
    files = []
    for n in sorted(os.listdir(ZERO)):
        p = os.path.join(ZERO, n)
        if not os.path.isfile(p):
            continue
        st = os.stat(p)
        files.append({
            "name": n,
            "bytes": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return {
        "exists": True,
        "dir": ZERO,
        "files": files,
        "ls": run(["ls", "-la", ZERO]),
        "list_txt": open(os.path.join(ZERO, "LIST.txt"), encoding="utf-8").read()
        if os.path.exists(os.path.join(ZERO, "LIST.txt")) else "",
    }


def alert_facts() -> dict:
    """告警去重标记 + 「为什么 11-20/21/22 没建单」的代码考古证据。"""
    state = "/srv/share/quantdb/logs/state"
    markers = sorted(f for f in os.listdir(state) if f.startswith("backup-alert-"))
    history = run([
        "git", "-C", REPO, "log", "--format=%h %ad %s", "--date=iso",
        "-S", "alert_failure", "--", "db/timers/backup-nightly.sh",
    ])
    first_alert_commit = history.splitlines()[-1] if history.strip() else ""
    return {
        "markers": markers,
        "state_dir": state,
        "first_alert_commit": first_alert_commit,
        "git_log": history,
        "cmd": "git -C %s log -S alert_failure -- db/timers/backup-nightly.sh" % REPO,
    }


def journal_facts(unit_raw: str) -> list[dict]:
    """从 11-19~11-24 的 journal 里抽出每次失败的日期、unit 描述与首条脚本输出。"""
    rows: list[dict] = []
    cur: dict | None = None
    for line in unit_raw.splitlines():
        m = re.match(r"^(\w{3} \d{2}) (\d{2}:\d{2}:\d{2}) \S+ systemd\[\d+\]: Starting (backup-nightly\.service) - (.+?)\.\.\.$", line)
        if m:
            cur = {"date": m.group(1), "time": m.group(2), "desc": m.group(4), "first_output": ""}
            rows.append(cur)
            continue
        m = re.search(r"backup-nightly\.sh\[\d+\]: (.+)$", line)
        if m and cur is not None and not cur["first_output"]:
            cur["first_output"] = m.group(1)
        m = re.search(r"(Failed to start|Started) backup-nightly", line)
        if m and cur is not None:
            cur["outcome"] = m.group(1)
    return rows


def main() -> None:
    unit = unit_facts()
    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "host": run(["hostname"]),
        "unit": unit,
        "dumps": dump_facts(),
        "borg": borg_facts(),
        "runs": parse_runs(),
        "zero_byte": zero_facts(),
        "alerts": alert_facts(),
        "journal": journal_facts(unit["journal_failures"]),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("wrote", OUT)


if __name__ == "__main__":
    sys.exit(main())
