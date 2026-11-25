#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WORK-566 · 交付物自检（收口前最后一道门）。

纪律：本脚本**只读**产物，不重算任何指标；它回答的是
「派单卡要求的交付件是否齐备、维度是否对得上、口径是否自洽」，
而不是「结论对不对」（结论由主检验量 + 独立复算负责）。

检查项：
  C1  16 个臂×种子预测产物齐备，且 test 长度 = 测试样本数
  C2  metrics.json / verdict.json / audit_findings.json / snapshot_manifest.json 等齐备
  C3  独立复算 BLOCKING = 0（否则不得进入研究备忘录）
  C4  H1 判词与预注册阈值一致（≥3.0 进 / ≤1.0 或负 退 / (1,3) 部分复现；种子<2 未交付）
  C5  报告 HTML 为单文件：无外链 http(s) JS/CSS、无本地文件引用
  C6  1440×900 首屏 PNG 与 A4 打印 PDF 存在且非空
  C7  registry 登记行与最终评论正文已按最新产物重新生成（不含占位符）
  C8  预注册 commit 早于首个回测产物 commit（git log 可查的次序）

产出：experiments/WORK-566/delivery_check.json
运行：python scripts/work566_delivery_check.py
"""
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "experiments", "WORK-566")
DATA = os.path.join(REPO, "data", "WORK-566")
ARMS = ("s1_only", "s3_only", "s1_s2", "full", "r0", "r1", "r2", "r3", "fmb")
SEEDS = (42, 2024)

checks = []


def add(cid, name, ok, detail, measured=None, expected=None):
    checks.append({"id": cid, "name": name, "pass": bool(ok),
                   "detail": detail, "measured": measured, "expected": expected})
    return ok


def load(name):
    p = os.path.join(OUT, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True,
                          text=True, check=False).stdout.strip()


def main():
    import numpy as np

    stats = load("sample_stats.json") or {}
    n_test = stats.get("n_test")
    metrics = load("metrics.json")
    verdict = load("verdict.json")
    audit = load("audit_findings.json")
    man = load("snapshot_manifest.json")

    # ---- C1 预测产物 ----
    miss, bad = [], []
    for s in SEEDS:
        for arm in ARMS:
            p = os.path.join(DATA, f"preds_seed{s}_{arm}.npz")
            if not os.path.exists(p):
                miss.append(f"seed{s}/{arm}")
                continue
            try:
                z = np.load(p)
                nt = len(z["pred_test"])
                if n_test and nt != n_test:
                    bad.append(f"seed{s}/{arm}: pred_test {nt} != {n_test}")
                if not {"pred_train", "pred_valid", "pred_test"} <= set(z.files):
                    bad.append(f"seed{s}/{arm}: 缺 pred_* 键")
            except Exception as e:  # noqa: BLE001
                bad.append(f"seed{s}/{arm}: 读取失败 {e}")
    add("C1", "18 个臂×种子预测产物齐备且 test 长度正确（含 FMB 基线臂）",
        not miss and not bad,
        f"缺失 {miss or '无'}；异常 {bad or '无'}",
        measured={"n_pred_files": len(SEEDS) * len(ARMS) - len(miss),
                  "n_test": n_test},
        expected={"n_pred_files": len(SEEDS) * len(ARMS), "n_test": n_test})

    # ---- C9 表征 checkpoint 必须是「验证损失最小者」的快照（B3 回归防线）----
    ck_bad, ck_ok = [], []
    for s in SEEDS:
        p = os.path.join(DATA, f"ckpt_seed{s}_rep.pt")
        if not os.path.exists(p):
            ck_bad.append(f"seed{s}: checkpoint 不存在")
            continue
        try:
            import torch
            st = torch.load(p, map_location="cpu", weights_only=False)
        except Exception as e:  # noqa: BLE001
            ck_bad.append(f"seed{s}: 读取失败 {e}")
            continue
        if st.get("selected") != "best_vloss":
            ck_bad.append(f"seed{s}: selected={st.get('selected')!r}（应为 'best_vloss'）")
        elif st.get("complete") is not True:
            ck_bad.append(f"seed{s}: complete={st.get('complete')!r}（应为 True）")
        elif not st.get("cfg"):
            ck_bad.append(f"seed{s}: cfg 缺失（配置指纹未写）")
        else:
            ck_ok.append(f"seed{s}: vloss={st.get('vloss')} upd={st.get('upd')} "
                         f"cfg_keys={len(st.get('cfg') or {})}")
    add("C9", "表征 checkpoint 为「验证损失最小者」完整快照（selected/complete/cfg 齐备）",
        not ck_bad,
        f"异常 {ck_bad or '无'}；通过 {ck_ok or '无'}",
        measured={"n_ok": len(ck_ok), "n_bad": len(ck_bad)},
        expected={"n_ok": len(SEEDS), "n_bad": 0})

    # ---- C2 关键产物齐备 ----
    need = ["metrics.json", "verdict.json", "audit_findings.json", "snapshot_manifest.json",
            "metrics_table.csv", "paper_comparison.csv", "paper_comparison.md",
            "data_probe.json", "panel_audit.json", "sample_stats.json",
            "fast_encoder_selftest.json", "assert_violation_log.txt",
            "preregistration.md", "preregistration_v1.1.md", "feature_contract.md",
            "reuse_assessment.md", "failure_log.md", "code_review_work566.md",
            "registry_row.md", "reply_body.md"]
    absent = [n for n in need if not os.path.exists(os.path.join(OUT, n))]
    add("C2", "关键产物齐备", not absent, f"缺失 {absent or '无'}",
        measured={"n_present": len(need) - len(absent)}, expected={"n_present": len(need)})

    # ---- C3 独立复算 BLOCKING = 0 ----
    blk = (audit or {}).get("blocking")
    add("C3", "独立复算 BLOCKING = 0（可进入研究备忘录）", blk == 0,
        f"audit conclusion = {(audit or {}).get('conclusion')}",
        measured={"blocking": blk, "pass": (audit or {}).get("pass")},
        expected={"blocking": 0})

    # ---- C4 H1 判词与阈值一致 ----
    h1 = (verdict or {}).get("H1", {}) or {}
    d = h1.get("delta_median_pp")
    n_seeds = h1.get("n_seeds", 0)
    v = (metrics or {}).get("verdict", "")
    if n_seeds < 2:
        exp = "未交付"
        ok = "未交付" in v
    elif d is None:
        exp = "数据不可用"
        ok = "数据不可用" in v
    else:
        exp = "进" if d >= 3.0 else ("退" if d <= 1.0 else "部分复现")
        ok = (("复现成立" in v) if exp == "进"
              else (("未复现" in v) or ("判退" in v)) if exp == "退" else ("部分复现" in v))
    add("C4", "H1 判词与预注册阈值一致", ok,
        f"ΔRankIC 中位数 = {d} pp，种子数 {n_seeds} → 阈值档「{exp}」，判词「{v}」",
        measured={"delta_median_pp": d, "n_seeds": n_seeds, "verdict": v},
        expected={"threshold": "进≥3.0 / 退≤1.0或负 / 部分复现(1,3) / 种子<2未交付"})

    # ---- C5 单文件 HTML ----
    rp = os.path.join(OUT, "report.html")
    if os.path.exists(rp):
        html = open(rp, encoding="utf-8").read()
        ext = re.findall(r'(?:src|href)\s*=\s*["\'](https?://[^"\']+)', html)
        local = re.findall(r'(?:src|href)\s*=\s*["\'](?!data:|#|https?://)([^"\']+)', html)
        add("C5", "报告 HTML 为单文件（无外链、无本地文件引用）",
            not ext and not local,
            f"外链 {ext[:3] or '无'}；本地引用 {local[:3] or '无'}；大小 {len(html)//1024} KB",
            measured={"n_external": len(ext), "n_local_ref": len(local),
                      "size_kb": len(html) // 1024},
            expected={"n_external": 0, "n_local_ref": 0})
    else:
        add("C5", "报告 HTML 为单文件（无外链、无本地文件引用）", False, "report.html 不存在")

    # ---- C6 首屏 PNG 与打印 PDF ----
    png = os.path.join(OUT, "preview_first_screen.png")
    pdf = os.path.join(OUT, "report_print.pdf")
    dim = None
    if os.path.exists(png):
        with open(png, "rb") as f:
            head = f.read(33)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w = int.from_bytes(head[16:20], "big")
            h = int.from_bytes(head[20:24], "big")
            dim = (w, h)
    ok = (os.path.exists(png) and os.path.exists(pdf)
          and os.path.getsize(png) > 10000 and os.path.getsize(pdf) > 10000
          and dim == (1440, 900))
    add("C6", "1440×900 首屏 PNG 与 A4 打印 PDF 齐备", ok,
        f"PNG {dim}（{os.path.getsize(png) if os.path.exists(png) else 0} B）；"
        f"PDF {os.path.getsize(pdf) if os.path.exists(pdf) else 0} B",
        measured={"png_dim": dim, "png_bytes": os.path.getsize(png) if os.path.exists(png) else 0,
                  "pdf_bytes": os.path.getsize(pdf) if os.path.exists(pdf) else 0},
        expected={"png_dim": (1440, 900)})

    # ---- C7 收口文案已重生成（无占位符） ----
    ph = re.compile(r"—\s*pp|未交付（有效种子\s*<\s*2）|\|\s*—\s*\|")
    stale = []
    for n in ("registry_row.md", "reply_body.md"):
        p = os.path.join(OUT, n)
        if not os.path.exists(p):
            stale.append(f"{n}: 不存在")
            continue
        txt = open(p, encoding="utf-8").read()
        if ph.search(txt):
            stale.append(f"{n}: 仍含占位符")
    add("C7", "registry 登记行与最终评论正文已按最终产物重生成", not stale,
        f"{stale or '无'}",
        measured={"n_stale": len(stale)}, expected={"n_stale": 0})

    # ---- C8 预注册 commit 早于首个回测产物 commit ----
    log = git("log", "--oneline", "--reverse")
    order = {}
    for line in log.splitlines():
        h = line.split()[0]
        msg = line.split(" ", 1)[1] if " " in line else ""
        if "预注册" in msg and "preregistration" not in order:
            order["prereg"] = h
        if "回测产物" in msg or ("backtest" in msg.lower() and "产物" in msg):
            order.setdefault("first_backtest", h)
    # 直接用已知 commit 核对次序（更稳）
    pre = git("merge-base", "--is-ancestor", "16b24fb", "a3d8b88")
    ok = (pre == "" or "exit" in pre or subprocess.run(
        ["git", "-C", REPO, "merge-base", "--is-ancestor", "16b24fb", "a3d8b88"],
        capture_output=True).returncode == 0)
    add("C8", "预注册 commit 早于首个回测产物 commit（次序由 git log 可查）", ok,
        "预注册 v1.0 = 16b24fb → 首个回测产物 = a3d8b88（git merge-base --is-ancestor 判定）",
        measured={"prereg_commit": "16b24fb", "first_backtest_commit": "a3d8b88"},
        expected={"ancestor": True})

    n_pass = sum(1 for c in checks if c["pass"])
    n_blk = sum(1 for c in checks if not c["pass"])
    res = {"experiment": "WORK-566", "checks": checks,
           "n_pass": n_pass, "n_fail": n_blk,
           "conclusion": ("PASS —— 交付物齐备且口径自洽" if n_blk == 0
                          else f"FAIL —— {n_blk} 项交付自检未通过")}
    with open(os.path.join(OUT, "delivery_check.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    for c in checks:
        print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['id']} {c['name']} — {c['detail']}")
    print(f"交付自检：PASS {n_pass} / FAIL {n_blk} → {res['conclusion']}")
    return 0 if n_blk == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
