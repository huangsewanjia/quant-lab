#!/usr/bin/env python3
"""WORK-557 段2a-0：把 quantdb 的 raw parquet **并行**拷到本地盘，再在本地做 SQL。

为什么
------
Samba 共享（Z: → \\\\192.168.9.104\\share）单线程延迟约 250–300 ms/文件；
`raw/daily` 有 5000+ 个日分区文件，duckdb 直接 glob 扫网络盘会卡死
（实测：读计数 60 s 增量为 0）。瓶颈是**延迟**不是带宽，所以用线程池并发拷贝，
拷完在本地盘上跑 duckdb（本地盘 IO 快一个数量级）。

本脚本只读 Z:，只写本地 `data/WORK-557/cache/raw_local/`（已 gitignore）。
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOCAL = os.path.join(REPO, "data", "WORK-557", "cache", "raw_local")
REMOTE = r"Z:\quantdb\raw"

TABLES = ["daily", "adj_factor", "daily_basic", "index_daily", "stk_limit",
          "suspend_d"]
INDEX_TABLES = ["index_weight", "fina_indicator"]
WORKERS = int(os.environ.get("WORK557_COPY_WORKERS", "16"))


def copy_one(src, dst):
    if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
        return 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return os.path.getsize(src)


def copy_table(table, workers):
    src_root = os.path.join(REMOTE, table)
    dst_root = os.path.join(LOCAL, table)
    files = []
    for root, _dirs, names in os.walk(src_root):
        for n in names:
            if n.endswith(".parquet"):
                files.append(os.path.join(root, n))
    if not files:
        print("  [%s] no files" % table, flush=True)
        return 0, 0
    t0 = time.time()
    total = 0
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(copy_one, f, os.path.join(dst_root,
                                                    os.path.relpath(f, src_root))): f
                for f in files}
        for fu in as_completed(futs):
            try:
                total += fu.result()
            except Exception as e:
                print("  [%s] FAILED %s: %s" % (table, futs[fu], e), flush=True)
            done += 1
            if done % 1000 == 0 or done == len(files):
                el = time.time() - t0
                print("  [%s] %d/%d files  %.0f MB  %.0fs (%.1f MB/s)"
                      % (table, done, len(files), total / 1048576, el,
                         total / 1048576 / max(el, 1e-9)), flush=True)
    return len(files), total


def main():
    which = sys.argv[1:] or TABLES
    os.makedirs(LOCAL, exist_ok=True)
    grand_files = grand_bytes = 0
    for table in which:
        print("=== copy %s ===" % table, flush=True)
        n, b = copy_table(table, WORKERS)
        grand_files += n
        grand_bytes += b
    print("\nTOTAL %d files, %.1f MB -> %s"
          % (grand_files, grand_bytes / 1048576, LOCAL), flush=True)


if __name__ == "__main__":
    main()
