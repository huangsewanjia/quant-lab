#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WORK-566 · 渲染交付件：1440×900 首屏 PNG + 打印首页 PDF（单文件 HTML 的渲染结果）。

用法：python scripts/work566_render.py
输出：figures/WORK-566/first_screen_1440x900.png
      figures/WORK-566/report_first_page.pdf
      figures/WORK-566/report_full.pdf
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "experiments", "WORK-566")
FIG = os.path.join(REPO, "figures", "WORK-566")
HTML = os.path.join(OUT, "report.html")

CHROME_CANDIDATES = [
    "/home/moyongkai/.local/bin/google-chrome",
    "/home/moyongkai/.cache/ms-playwright/chromium-1255/chrome-linux64/chrome",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def find_chrome():
    for c in CHROME_CANDIDATES:
        if c and os.path.exists(c):
            return c
    raise SystemExit("未找到 Chrome/Chromium 可执行文件")


def run_chrome(chrome, args, timeout=180):
    with tempfile.TemporaryDirectory() as prof:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--disable-dev-shm-usage", "--hide-scrollbars",
               "--user-data-dir=" + prof, "--virtual-time-budget=8000",
               "--allow-file-access-from-files"] + args
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            log(f"chrome stderr: {r.stderr[-600:]}")
        return r


def main():
    if not os.path.exists(HTML):
        raise SystemExit(f"未找到报告 HTML：{HTML}")
    os.makedirs(FIG, exist_ok=True)
    chrome = find_chrome()
    log(f"Chrome: {chrome}")
    url = "file://" + os.path.abspath(HTML)

    # ---------------- 1) 首屏 PNG（1440×900） ----------------
    png = os.path.join(FIG, "first_screen_1440x900.png")
    log("渲染首屏 PNG 1440×900 …")
    run_chrome(chrome, ["--window-size=1440,900", f"--screenshot={png}", url])
    if os.path.exists(png):
        log(f"  → {png} ({os.path.getsize(png):,} bytes)")
    else:
        raise SystemExit("首屏 PNG 渲染失败")

    # ---------------- 2) 打印 PDF（整份） ----------------
    full_pdf = os.path.join(FIG, "report_full.pdf")
    log("渲染打印 PDF（整份）…")
    run_chrome(chrome, ["--no-pdf-header-footer", f"--print-to-pdf={full_pdf}", url])
    if not os.path.exists(full_pdf):
        raise SystemExit("PDF 渲染失败")
    log(f"  → {full_pdf} ({os.path.getsize(full_pdf):,} bytes)")

    # ---------------- 3) 抽取首页 PDF ----------------
    first_pdf = os.path.join(FIG, "report_first_page.pdf")
    try:
        from pypdf import PdfReader, PdfWriter
        rd = PdfReader(full_pdf)
        wr = PdfWriter()
        wr.add_page(rd.pages[0])
        with open(first_pdf, "wb") as fh:
            wr.write(fh)
        log(f"  → {first_pdf}（首页，共 {len(rd.pages)} 页 → 取第 1 页）")
    except Exception as e:                                        # noqa: BLE001
        log(f"首页抽取失败（{type(e).__name__}: {e}），保留整份 PDF")
        shutil.copy(full_pdf, first_pdf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
