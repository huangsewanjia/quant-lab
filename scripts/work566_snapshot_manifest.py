#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WORK-566 · 快照 manifest 生成器。

产出 experiments/WORK-566/snapshot_manifest.json：
  每个端点一条记录，含 ok / error 原文 / 行数 / sha256；
  缺项保留 ok:false，不删条目（任务书硬要求）。

运行：python scripts/work566_snapshot_manifest.py
"""
import hashlib
import json
import os
import subprocess
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "WORK-566")
OUT = os.path.join(REPO, "experiments", "WORK-566")


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                b = fh.read(chunk)
                if not b:
                    break
                h.update(b)
    except OSError as e:
        return None, str(e)
    return h.hexdigest(), None


def git(*args):
    try:
        return subprocess.check_output(["git", "-C", REPO, *args], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception as e:                                    # noqa: BLE001
        return f"<git error: {e}>"


def main():
    manifest = {
        "experiment": "WORK-566",
        "stage": "快照 manifest",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "data_cutoff": "2026-06-26",
        "repo": {
            "path": REPO,
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "commit": git("rev-parse", "HEAD"),
            "commit_time": git("log", "-1", "--format=%cI"),
            "commit_subject": git("log", "-1", "--format=%s"),
        },
        "endpoints": [],
        "snapshot_files": [],
    }

    # ---------------- 端点（来自 Stage 0 探针） ----------------
    probe_path = os.path.join(OUT, "data_probe.json")
    if os.path.exists(probe_path):
        probe = json.load(open(probe_path, encoding="utf-8"))
        for ep in probe.get("tushare_endpoints", []):
            manifest["endpoints"].append({
                "endpoint": ep.get("endpoint"),
                "source": "tushare MCP",
                "params": ep.get("params"),
                "ok": bool(ep.get("ok")),
                "error": ep.get("error"),
                "rows": ep.get("rows"),
                "coverage": ep.get("coverage"),
                "unit": ep.get("unit"),
                "note": ep.get("note"),
            })
        for ep in probe.get("local_endpoints", []):
            rows = ep.get("rows")
            manifest["endpoints"].append({
                "endpoint": ep.get("endpoint"),
                "source": "quantdb parquet 原始层",
                "sql": ep.get("sql"),
                "ok": ep.get("ok", True) if isinstance(ep, dict) else True,
                "error": ep.get("error") if isinstance(ep, dict) else None,
                "rows": len(rows) if isinstance(rows, list) else None,
                "result": rows,
                "unit": ep.get("unit"),
                "note": ep.get("note"),
                "elapsed_s": ep.get("elapsed_s"),
            })
        manifest["window_coverage"] = probe.get("window_coverage")
        manifest["delisting"] = probe.get("delisting")
        manifest["test_window_bar_coverage"] = probe.get("test_window_bar_coverage")
    else:
        manifest["endpoints"].append({
            "endpoint": "quantdb.* / tushare.*",
            "source": "Stage 0 探针",
            "ok": False,
            "error": "data_probe.json 缺失：Stage 0 探针未产出，端点复测不可复核",
            "rows": None,
        })

    # ---------------- 快照文件（面板 + 中间产物） ----------------
    meta_path = os.path.join(DATA, "meta.json")
    if os.path.exists(meta_path):
        meta = json.load(open(meta_path, encoding="utf-8"))
        for rel, info in meta.get("files", {}).items():
            manifest["snapshot_files"].append({
                "path": f"data/WORK-566/{rel}",
                "sha256": info.get("sha256"),
                "bytes": info.get("bytes"),
                "ok": True,
                "source": "面板构建时记录",
            })
    for rel in ("prior.json", "norm.json", "samples.npz"):
        p = os.path.join(DATA, rel)
        if os.path.exists(p):
            sha, err = sha256_file(p)
            manifest["snapshot_files"].append({
                "path": f"data/WORK-566/{rel}",
                "sha256": sha,
                "bytes": os.path.getsize(p),
                "ok": err is None,
                "error": err,
                "source": "本次运行复算",
            })
        else:
            manifest["snapshot_files"].append({
                "path": f"data/WORK-566/{rel}",
                "sha256": None,
                "bytes": None,
                "ok": False,
                "error": "文件不存在（对应阶段未完成）",
                "source": "本次运行复算",
            })

    for rel in ("panel_audit.json", "data_probe.json", "sample_stats.json"):
        p = os.path.join(OUT, rel)
        if os.path.exists(p):
            sha, err = sha256_file(p)
            manifest["snapshot_files"].append({
                "path": f"experiments/WORK-566/{rel}",
                "sha256": sha,
                "bytes": os.path.getsize(p),
                "ok": err is None,
                "error": err,
                "source": "本次运行复算",
            })

    ok_eps = sum(1 for e in manifest["endpoints"] if e.get("ok"))
    manifest["summary"] = {
        "n_endpoints": len(manifest["endpoints"]),
        "n_endpoints_ok": ok_eps,
        "n_endpoints_failed": len(manifest["endpoints"]) - ok_eps,
        "n_snapshot_files": len(manifest["snapshot_files"]),
    }

    outp = os.path.join(OUT, "snapshot_manifest.json")
    with open(outp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print(f"written: {outp}")
    print(f"  endpoints: {ok_eps}/{len(manifest['endpoints'])} ok")
    print(f"  snapshot files: {len(manifest['snapshot_files'])}")
    print(f"  repo commit: {manifest['repo']['commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
