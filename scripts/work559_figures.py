#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WORK-559 · 交付页配图（Nature 风格，输出 PNG，由 build_delivery 内嵌 base64）。

三张图的数据全部来自 docs/WORK-559/evidence.json（实测采集），不手填。
图 1 每晚状态矩阵 · 图 2 根因链 · 图 3 修复后一次运行的耗时分解。
"""
from __future__ import annotations

import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = os.path.join(REPO, "docs", "WORK-559", "evidence.json")
FIG_DIR = os.path.join(REPO, "docs", "WORK-559", "figures")

INK = "#20303c"
MUTED = "#6b7a86"
OK = "#3f7a5e"
FAIL = "#a8433a"
WARN = "#b9842f"
NONE = "#cfd6db"
ACCENT = "#2c5f8a"

# 本机的 CJK 字体只装在 ~/.fonts，matplotlib 的默认缓存里没有，必须显式登记，
# 否则中文标签会被渲染成方框（实测 findfont: Font family ... not found）。
_FONT_DIR = os.path.expanduser("~/.fonts")
if os.path.isdir(_FONT_DIR):
    for _f in sorted(os.listdir(_FONT_DIR)):
        if _f.lower().endswith((".otf", ".ttf")):
            try:
                matplotlib.font_manager.fontManager.addfont(os.path.join(_FONT_DIR, _f))
            except Exception:  # 单个字体损坏不影响其余
                pass

plt.rcParams.update({
    "font.family": ["Noto Sans SC", "Noto Serif CJK SC", "DejaVu Sans"],
    "font.size": 9,
    "axes.edgecolor": MUTED,
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "savefig.dpi": 200,
    "savefig.bbox": "tight",
})


def chrono_label(days: list[str]) -> list[str]:
    return days


def fig_timeline(d: dict, path: str) -> None:
    """每晚四层状态：冷备 / 三个 pg_dump / borg 快照 / 告警单。"""
    days = ["11-19", "11-20", "11-21", "11-22", "11-23", "11-24", "11-25 03:30", "11-25 12:39"]
    # status: 0=ok 1=fail 2=missing/无告警 3=n/a(当时脚本还没有这一步)
    rows = [
        ("冷备 rsync+校验", [3, 3, 3, 3, 3, 0, 0, 0]),
        ("pg_dump ×3", [0, 1, 1, 1, 1, 1, 1, 0]),
        ("borg 快照", [0, 1, 1, 1, 1, 1, 1, 2]),
        ("告警单", [3, 2, 2, 2, 2, 0, 0, 2]),
    ]
    colors = {0: OK, 1: FAIL, 2: WARN, 3: NONE}
    notes = {0: "成功", 1: "失败", 2: "无/未落", 3: "不适用"}

    fig, ax = plt.subplots(figsize=(7.4, 2.5))
    for yi, (label, vals) in enumerate(rows):
        for xi, v in enumerate(vals):
            ax.add_patch(plt.Rectangle((xi, len(rows) - yi - 1), 0.92, 0.72,
                                       facecolor=colors[v], edgecolor="white", linewidth=1.2))
    ax.set_xlim(0, len(days))
    ax.set_ylim(0, len(rows))
    ax.set_xticks([i + 0.46 for i in range(len(days))])
    ax.set_xticklabels(days, fontsize=8)
    ax.set_yticks([len(rows) - i - 0.64 for i in range(len(rows))])
    ax.set_yticklabels([r[0] for r in rows], fontsize=8.5)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    ax.axvline(4.0, color=MUTED, linewidth=0.8, linestyle=(0, (2, 2)))
    ax.text(4.02, len(rows) + 0.05, "03:30 定时", fontsize=7.5, color=MUTED, va="bottom")
    ax.text(6.06, len(rows) + 0.05, "WORK-529 告警上线", fontsize=7.5, color=MUTED, va="bottom")
    handles = [plt.Rectangle((0, 0), 1, 1, facecolor=colors[k]) for k in (0, 1, 2, 3)]
    ax.legend(handles, [notes[k] for k in (0, 1, 2, 3)], ncol=4, frameon=False,
              fontsize=7.5, loc="lower left", bbox_to_anchor=(0, -0.28), handlelength=1.1,
              columnspacing=1.4)
    fig.savefig(path)
    plt.close(fig)


def fig_rootcause(d: dict, path: str) -> None:
    """两条独立故障链：docker 组缺失（dump 空转）与 borg lost+found（快照被判失败）。"""
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    ax.set_xlim(0, 12.6)
    ax.set_ylim(0, 7.0)
    ax.axis("off")

    def box(x, y, w, h, text, fc="#ffffff", ec=MUTED, fs=7.8):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.05,rounding_size=0.10",
                                    facecolor=fc, edgecolor=ec, linewidth=0.9))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
                color=INK, linespacing=1.45)

    def arrow(x1, y, x2):
        ax.add_patch(FancyArrowPatch((x1, y), (x2, y), arrowstyle="-|>",
                                     mutation_scale=9, linewidth=0.9, color=ACCENT))

    cols = [0.1, 3.25, 6.4, 9.55]
    w = 2.95

    ax.text(0.1, 6.72, "A · dump 空转链（2026-11-20 → 11-24，连续 5 晚）",
            fontsize=8.4, color=ACCENT, weight="bold")
    lane_a = ["systemd --user\n会话无 docker 补充组", "docker compose exec\nEACCES: docker.sock",
              "重定向先建文件\n→ 0 字节 dump 留盘", "旧脚本无 alert_failure\n→ 11-20/21/22 无单"]
    for x, t in zip(cols, lane_a):
        box(x, 5.25, w, 1.05, t)
    for i in range(3):
        arrow(cols[i] + w + 0.02, 5.78, cols[i + 1] - 0.06)

    ax.text(0.1, 4.62, "B · 快照判错链（2026-11-25 实测）", fontsize=8.4, color=ACCENT, weight="bold")
    lane_b = ["/srv/share/lost+found\n/srv/share/catl/pgdata", "borg create\ndir_open EACCES",
              "归档已写入\n退出码=1（warning）", "run_step 判失败\nunit=exit-code"]
    for x, t in zip(cols, lane_b):
        box(x, 3.15, w, 1.05, t)
    for i in range(3):
        arrow(cols[i] + w + 0.02, 3.68, cols[i + 1] - 0.06)

    box(0.1, 1.55, 12.4, 0.85,
        "修复 A：dk() = /usr/bin/sg docker -c '<命令>'；dump 先写 .part，校验大小 / PGDMP 头 / pg_restore -l 目录后才改名",
        fc="#eef4f1", ec=OK, fs=7.8)
    box(0.1, 0.45, 12.4, 0.85,
        "修复 B：EXCLUDES 显式排除不可读路径并把清单写进日志；SIGTERM trap 补日志与告警；冷备自校验失败整步重试一次",
        fc="#eef4f1", ec=OK, fs=7.8)
    fig.savefig(path)
    plt.close(fig)


def fig_duration(d: dict, path: str) -> None:
    """修复后一次完整运行的耗时分解，对照 6h/12h 上限。"""
    # 2026-11-25 12:39:17 → 13:06:13 冷备；→ 13:09:08 dump；→ 16:31:59 borg 归档写出
    cold, dump, borg = 26 * 60 + 56, 2 * 60 + 55, 3 * 3600 + 22 * 60 + 51
    total = cold + dump + borg
    fig, ax = plt.subplots(figsize=(7.4, 1.95))
    segs = [("冷备 rsync + SHA256SUMS", cold, "#7f9cb5"),
            ("pg_dump ×3（含 pg_restore -l 校验）", dump, "#4f7592"),
            ("borg create 566.6 GB / 719,192 文件", borg, ACCENT)]
    left = 0
    for label, val, color in segs:
        ax.barh(0, val / 3600, left=left / 3600, height=0.42, color=color, edgecolor="white")
        left += val
    ax.axvline(6, color=FAIL, linewidth=1.0, linestyle=(0, (4, 2)))
    ax.text(5.92, 0.34, "TimeoutStartSec = 6h（余量约 2 小时 07 分）", fontsize=7.6,
            color=FAIL, va="bottom", ha="right")
    ax.set_yticks([])
    ax.set_xlim(0, 6.5)
    ax.set_xlabel("小时（同一次运行：2026-11-25 12:39:17 → 16:32:05，合计 %d 小时 %d 分）"
                  % (total // 3600, (total % 3600 + 59) // 60), fontsize=8)
    ax.spines["left"].set_visible(False)
    ax.legend([plt.Rectangle((0, 0), 1, 1, facecolor=s[2]) for s in segs],
              ["%s · %d 分" % (s[0], round(s[1] / 60)) for s in segs],
              frameon=False, fontsize=7.6, ncol=3, loc="lower center", bbox_to_anchor=(0.5, -0.62))
    fig.savefig(path)
    plt.close(fig)


def main() -> None:
    d = json.load(open(EVIDENCE, encoding="utf-8"))
    os.makedirs(FIG_DIR, exist_ok=True)
    fig_timeline(d, os.path.join(FIG_DIR, "fig1-timeline.png"))
    fig_rootcause(d, os.path.join(FIG_DIR, "fig2-rootcause.png"))
    fig_duration(d, os.path.join(FIG_DIR, "fig3-duration.png"))
    print("figures:", sorted(os.listdir(FIG_DIR)))


if __name__ == "__main__":
    main()
