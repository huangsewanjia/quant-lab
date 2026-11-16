# -*- coding: utf-8 -*-
"""
配图（Python / matplotlib，Nature 风格）：metrics.json → figures/<EXP>/*.png|pdf|svg + 对齐审计 JSON

输入：experiments/<EXP>/metrics.json（由 scripts/build_analysis.py 生成）
      data/<EXP>/A_tencent_*.csv（画净值曲线用）
口径：指标定义见 data_contracts/metric_definitions.md

图件清单：
  图1 原文因果链与本单验证状态（示意）
  图2 2026 年初至今风格指数净值曲线（基准=2025-12-31）
  图3 复现实验：①7月/10月分风格月度收益 ②全市场成交额代理月均值
  图4 ①可验证性判定矩阵（6 主张 × 7 检查） ②立项三档方案雷达对比

版式纪律（对齐 + 碰撞审计）：
  - 不用网格线（会让文字压线，碰撞审计判 FAIL）；靠轴脊与刻度读数。
  - 图例一律放在数据空白区或坐标区外，不压数据。
  - 数值标签旋转 90°，避免相邻柱标签互相压字。

对齐审计依赖 nature-figure 技能的脚本，路径用环境变量 NATURE_FIGURE_SCRIPTS 指定；
找不到时跳过对齐门（图照常生成），不会中断流水线。

运行：
  python scripts/build_figures.py --exp WORK-404
"""
import argparse
import json
import os
import sys
import warnings

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# ---------------------------------------------------------------- 路径（仓库相对，无写死的任务目录）
EXP = os.environ.get("QUANTLAB_EXP", "WORK-404")
DATA = os.path.join(REPO, "data", EXP)
OUT = os.path.join(REPO, "experiments", EXP)
FIG = os.path.join(REPO, "figures", EXP)


def skill_scripts_dir():
    """定位 nature-figure 技能脚本：环境变量优先，其次仓库内，最后工作区级。找不到返回 None。"""
    cands = []
    env = os.environ.get("NATURE_FIGURE_SCRIPTS")
    if env:
        cands.append(env)
    cands.append(os.path.join(REPO, ".minimax", "skills", "nature-figure", "scripts"))
    cands.append(os.path.join(os.path.dirname(REPO), ".minimax", "skills", "nature-figure", "scripts"))
    for c in cands:
        if os.path.isdir(c):
            return c
    return None


mpl.rcParams.update({
    "font.family": "sans-serif",
    # 中文字形必须放在首位：Arial 缺 CJK 字形，排在前面会触发 matplotlib fallback 崩溃
    "font.sans-serif": ["Microsoft YaHei", "Noto Sans SC", "Arial", "DejaVu Sans", "sans-serif"],
    "axes.unicode_minus": False, "svg.fonttype": "none", "pdf.fonttype": 42,
    "font.size": 7, "axes.spines.right": False, "axes.spines.top": False,
    "axes.linewidth": 0.8, "legend.frameon": False, "figure.dpi": 150,
})
warnings.filterwarnings("ignore", category=UserWarning)

C_BLUE, C_ORANGE, C_RED, C_GREY = "#0B5FA5", "#D98200", "#B03030", "#6E6E6E"
PAL = ["#0B5FA5", "#D98200", "#3C8D5A", "#B03030", "#6C4BA6", "#00808A", "#8C6D31", "#5B6770"]


def save(fig, stem, multi=False):
    """按 nature-figure 契约导出：PNG(内嵌用) + PDF + SVG，并跑面板对齐门与碰撞审计门。"""
    sk = skill_scripts_dir()
    if sk:
        if sk not in sys.path:
            sys.path.insert(0, sk)
        try:
            from audit_panel_alignment import require_matplotlib_panel_alignment
            require_matplotlib_panel_alignment(
                fig, json_out=os.path.join(FIG, f"{stem}.alignment.json"),
                overlay_svg=os.path.join(FIG, f"{stem}.alignment.svg"),
                tolerance_pt=1.5, gutter_tolerance_pt=1.5, strict=True)
            print(f"[align] {stem}: ok")
        except Exception as e:  # noqa: BLE001
            print(f"[align] {stem}: {type(e).__name__}: {e}"[:220])
    else:
        print(f"[align] {stem}: 跳过（未找到 nature-figure 脚本；设 NATURE_FIGURE_SCRIPTS 启用对齐门）")
    fig.savefig(os.path.join(FIG, f"{stem}.png"), dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(os.path.join(FIG, f"{stem}.pdf"), bbox_inches="tight", facecolor="white")
    fig.savefig(os.path.join(FIG, f"{stem}.svg"), bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"[save] {stem}.png/.pdf/.svg")
    # 碰撞审计门：audit/audit_recompute.py 会读它，缺了就少一道独立复核
    if sk:
        try:
            import subprocess
            script = os.path.join(sk, "audit_figure_collisions.py")
            out = os.path.join(FIG, f"{stem}.collision-audit.json")
            r = subprocess.run([sys.executable, script, os.path.join(FIG, f"{stem}.pdf"),
                                "--json-out", out], capture_output=True, text=True)
            v = ""
            if os.path.exists(out):
                v = json.load(open(out, encoding="utf-8")).get("verdict", "")
            print(f"[collide] {stem}: rc={r.returncode} verdict={v}")
        except Exception as e:  # noqa: BLE001
            print(f"[collide] {stem}: {type(e).__name__}: {e}"[:220])
    else:
        print(f"[collide] {stem}: 跳过（未找到 nature-figure 脚本）")


def load(data_dir, code):
    df = pd.read_csv(os.path.join(data_dir, f"A_tencent_{code}.csv"))
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


# ----------------------------------------------------------------- 图1 示意
def fig1():
    fig, ax = plt.subplots(figsize=(7.2, 3.5))
    ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")

    def box(x, y, w, h, text, fc, ec, fs=6.4, tc="white", lw=0.8):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6,rounding_size=1.2",
                                    facecolor=fc, edgecolor=ec, linewidth=lw, zorder=2))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=tc, zorder=3)

    def arrow(x0, y0, x1, y1, color=C_GREY, lw=0.9):
        ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=8,
                                     color=color, lw=lw, zorder=1))

    box(2, 50, 20, 22, "上半年\n科技行情推动\n规模与收益双扩张", "#EAF1F8", C_BLUE, tc="#0B5FA5")
    box(2, 26, 20, 22, "资金扎堆\n相似因子与调仓节奏\n（隐性拥挤）", "#FDF2E3", C_ORANGE, tc="#8A5200")
    box(2, 4, 20, 18, "7月 / 10月\n两轮调整行情", "#FBECEC", C_RED, tc="#8C2020")

    box(30, 50, 22, 22, "风格极致反转\n动量、贝塔因子同步反转", "#FDF2E3", C_ORANGE, tc="#8A5200")
    box(30, 26, 22, 22, "成交缩量\n买卖价差扩大、订单簿变薄\n冲击成本上升", "#FDF2E3", C_ORANGE, tc="#8A5200")
    box(30, 4, 22, 18, "选股超额 + 风格收益\n双重承压", "#FBECEC", C_RED, tc="#8C2020")

    box(60, 50, 22, 22, "超额收益被抹平\n指增产品连续跑输指数", "#FBECEC", C_RED, tc="#8C2020")
    box(60, 26, 22, 22, "行业反思\n风控从事后反应\n转向事前约束", "#EAF1F8", C_BLUE, tc="#0B5FA5")
    box(60, 4, 22, 18, "规模纪律\n封盘、与策略容量匹配", "#EAF1F8", C_BLUE, tc="#0B5FA5")

    for x0, y0, x1, y1 in [(22, 61, 30, 61), (22, 37, 30, 37), (22, 13, 30, 13),
                           (52, 61, 60, 61), (52, 37, 60, 37), (52, 13, 60, 13),
                           (82, 50, 82, 48), (82, 26, 82, 22)]:
        arrow(x0, y0, x1, y1)

    ax.text(50, 97, "图1｜原文因果链，以及本单对每一环的验证状态", ha="center", va="top",
            fontsize=8.5, fontweight="bold")
    for i, (txt, col) in enumerate([
            ("价格/成交层：本单已实测复现（7月、10月风格与成交）", C_BLUE),
            ("拥挤归因：无法用公开数据识别（无外生冲击、无反事实）", C_ORANGE),
            ("产品层（超额、规模、盈亏比例）：无公开可核数据", C_RED)]):
        y = 86 - i * 3.4
        ax.add_patch(Rectangle((1.5, y - 0.9), 2.0, 1.8, facecolor=col, edgecolor="none", zorder=3))
        ax.text(5.0, y, txt, fontsize=6.2, color=col, va="center", ha="left")
    ax.text(1.5, 74.5, "注：蓝色=本单已实测；橙色=机构观点、不可独立识别；红色=原文主张但无公开可核数据。",
            fontsize=5.8, color=C_GREY, va="center", ha="left")
    save(fig, "fig1_schematic")


# ----------------------------------------------------------------- 图2 净值曲线
def fig2(data_dir):
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    picks = [("sh000852", "中证1000", C_BLUE), ("sz399303", "国证2000", "#3C8D5A"),
             ("sh000300", "沪深300（基准）", C_GREY), ("sz399006", "创业板指", C_ORANGE),
             ("sh000688", "科创50", C_RED), ("sh000922", "中证红利", "#6C4BA6")]
    for code, label, col in picks:
        df = load(data_dir, code)
        base_idx = df.loc[df.index <= pd.Timestamp("2025-12-31")].index[-1]
        s = df.loc[(df.index >= base_idx) & (df.index <= pd.Timestamp("2026-11-13")), "close"]
        ax.plot(s.index, s / df.loc[base_idx, "close"], lw=1.2, color=col, label=label,
                ls="--" if "基准" in label else "-")
    ax.axvspan(pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-31"), color=C_ORANGE, alpha=0.10)
    ax.axvspan(pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-31"), color=C_RED, alpha=0.10)
    # 月份标签贴到坐标区顶部（该区域无数据曲线经过）
    ax.text(pd.Timestamp("2026-07-16"), 0.985, "7月调整", transform=ax.get_xaxis_transform(),
            ha="center", va="top", fontsize=6.6, color="#8A5200")
    ax.text(pd.Timestamp("2026-10-16"), 0.985, "10月调整", transform=ax.get_xaxis_transform(),
            ha="center", va="top", fontsize=6.6, color="#8C2020")
    ax.axhline(1.0, color=C_GREY, lw=0.6, ls=":")
    ax.set_ylabel("净值（2025-12-31 = 1.00）")
    ax.set_title("图3｜2026 年初至今风格指数净值：科技与小盘显著跑输大盘与红利", fontsize=8.5,
                 fontweight="bold", loc="left")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.10), ncol=3, fontsize=6.2)
    save(fig, "fig2_nav")


# ----------------------------------------------------------------- 图3 复现双面板
def fig3(M):
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.2))
    w = M["windows"]
    order = [("sh000016", "上证50"), ("sh000300", "沪深300"), ("sh000905", "中证500"),
             ("sh000852", "中证1000"), ("sz399303", "国证2000"), ("sz399006", "创业板指"),
             ("sh000688", "科创50"), ("sh000922", "中证红利")]
    labels = [n for _, n in order]
    jul = [w["2026年7月"][c]["ret"] * 100 for c, _ in order]
    oct_ = [w["2026年10月"][c]["ret"] * 100 for c, _ in order]
    x = np.arange(len(labels))
    b1 = axes[0].bar(x - 0.2, jul, 0.38, color=C_ORANGE, label="2026年7月")
    b2 = axes[0].bar(x + 0.2, oct_, 0.38, color=C_RED, label="2026年10月")
    for bars in (b1, b2):
        for r in bars:
            h = r.get_height()
            # 正值标在柱顶之上、负值标在柱底之下，避免数值标签压到 y=0 轴线
            if h >= 0:
                axes[0].text(r.get_x() + r.get_width() / 2, h + 0.4, f"{h:.1f}",
                             ha="center", va="bottom", rotation=90, fontsize=5.6)
            else:
                axes[0].text(r.get_x() + r.get_width() / 2, h - 0.4, f"{h:.1f}",
                             ha="center", va="top", rotation=90, fontsize=5.6)
    axes[0].set_xticks(x); axes[0].set_xticklabels(labels, rotation=40, ha="right", fontsize=6.2)
    axes[0].axhline(0, color="#333333", lw=0.7)
    axes[0].set_ylabel("区间涨跌幅（%）")
    axes[0].set_ylim(min(jul + oct_) - 6, 14)
    axes[0].set_title("a  7 月与 10 月：科技/小盘重挫，红利独涨", fontsize=7.6, fontweight="bold", loc="left")
    axes[0].legend(fontsize=6.2, loc="upper left")

    mm = M["indices"]["sh000985"]["turnover_proxy_month_mean_2026"]
    ks = sorted(mm.keys())
    vs = [mm[k] / 1e12 for k in ks]
    ax2 = axes[1]
    cols = [C_GREY] * len(ks)
    for i, k in enumerate(ks):
        if k == "2026-07":
            cols[i] = C_ORANGE
        if k == "2026-10":
            cols[i] = C_RED
    ax2.bar(ks, vs, color=cols)
    h1 = M["turnover_summary"]["2026H1月均代理"] / 1e12
    ax2.axhline(h1, color=C_BLUE, lw=0.9, ls="--")
    ax2.text(-0.45, h1 + 0.25, f"H1 月均 {h1:.1f} 万亿元", fontsize=6.2, color=C_BLUE,
             va="bottom", ha="left")
    # 只标注两个关键月，避免柱标压到 H1 参考线
    for i, k in enumerate(ks):
        if k in ("2026-07", "2026-10"):
            ax2.text(i, vs[i] + 0.15, f"{vs[i]:.1f}", ha="center", fontsize=6.2)
    ax2.set_ylabel("成交额代理月均值（万亿元）")
    ax2.set_ylim(0, max(vs) * 1.25)
    ax2.set_title("b  全市场成交额代理：10 月较上半年均值 −42.6%", fontsize=7.6, fontweight="bold", loc="left")
    ax2.tick_params(axis="x", rotation=90, labelsize=5.8)
    fig.text(0.005, -0.04, "口径：a=区间收盘涨跌幅（腾讯行情源，Tushare 独立校验 9/9 一致——两侧都有值的同日观测）；"
                           "b=中证全指 volume×close 代理，仅用于趋势比较，绝对水平高于 Tushare 真实成交额 3.3–3.6 倍。",
             fontsize=5.8, color=C_GREY)
    fig.tight_layout()
    save(fig, "fig3_reproduce", multi=True)


# ----------------------------------------------------------------- 图4 判定矩阵 + 立项雷达
def fig4():
    fig = plt.figure(figsize=(7.6, 3.4))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.55, 1.0])
    axes = (fig.add_subplot(gs[0]), fig.add_subplot(gs[1], projection="polar"))
    claims = ["C1 私募指数10月跌超17%", "C2 量化跑不赢指数", "C3 新发产品七成亏损",
              "C4 部分产品回撤超30%", "C5 规模1.83万亿/3万亿", "C6 三座大山归因"]
    checks = ["前视偏差", "幸存者偏差", "退市/停牌", "复权与对齐", "样本内/外", "多重检验", "容量与成本"]
    score = np.array([
        [0, -1, 1, 1, -1, 0, -1],
        [1, -1, 0, 0, -1, -1, -1],
        [-1, -1, -1, 0, -1, 0, -1],
        [-1, -1, -1, 0, -1, 0, -1],
        [1, 0, 1, 1, -1, 0, 0],
        [1, 0, 0, 1, -1, 0, 0],
    ], dtype=float)
    from matplotlib.colors import ListedColormap, BoundaryNorm
    cmap = ListedColormap([C_RED, "#F0D9B5", C_BLUE])
    norm = BoundaryNorm([-1.5, -0.5, 0.5, 1.5], cmap.N)
    axes[0].imshow(score, cmap=cmap, norm=norm, aspect="auto")
    axes[0].set_xticks(range(len(checks)))
    axes[0].set_xticklabels(checks, rotation=38, ha="right", fontsize=6.0)
    axes[0].set_yticks(range(len(claims)))
    axes[0].set_yticklabels(claims, fontsize=6.2)
    for i in range(score.shape[0]):
        for j in range(score.shape[1]):
            txt = {1: "可复现", 0: "需假设", -1: "不可"}[int(score[i, j])]
            axes[0].text(j, i, txt, ha="center", va="center", fontsize=5.8,
                         color="white" if score[i, j] != 0 else "#333333")
    axes[0].set_title("a  可验证性判定矩阵：6 条主张 × 7 项检查", fontsize=7.6, fontweight="bold", loc="left")
    from matplotlib.patches import Patch
    axes[0].legend(handles=[Patch(facecolor=C_BLUE, label="可复现"), Patch(facecolor="#F0D9B5", label="需假设补全"),
                            Patch(facecolor=C_RED, label="不可复现")], fontsize=5.8,
                   loc="upper center", bbox_to_anchor=(0.5, -0.30), ncol=3)

    dims = ["复用频次", "数据复用率", "边际成本", "沉淀可复用", "不立项代价可控"]
    opts = {"不立项（继续挂在现有 issue）": ([1, 1, 3, 1, 2], C_GREY),
            "挂靠现有项目（中国金融市场研究）": ([3, 4, 3, 3, 3], C_ORANGE),
            "立专项（独立 project + 回测仓库）": ([5, 4, 2, 5, 4], C_BLUE)}
    ang = np.linspace(0, 2 * np.pi, len(dims), endpoint=False).tolist()
    ang += ang[:1]
    ax = axes[1]
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_xticks(ang[:-1])
    ax.set_xticklabels(dims, fontsize=6.0)
    ax.tick_params(axis="x", pad=18)   # 维度标签推到圆外，避免压雷达外圈
    ax.set_yticks([1, 3, 5])
    ax.set_yticklabels([])          # 去掉径向刻度数字，避免压多边形线
    ax.set_ylim(0, 5)
    ax.grid(lw=0.5, color="#CCCCCC")
    for label, (vals, col) in opts.items():
        v = vals + vals[:1]
        ax.plot(ang, v, lw=1.2, color=col, label=label)
        ax.fill(ang, v, color=col, alpha=0.10)
    ax.legend(fontsize=5.8, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    ax.set_title("b  立项三档方案对比（主观评分 1–5，依据见正文）", fontsize=7.6, fontweight="bold",
                 loc="left", pad=12)
    fig.tight_layout()
    save(fig, "fig4_matrix_radar", multi=True)


def _force_utf8_stdio():
    """把 stdout/stderr 显式切到 UTF-8（Windows 默认控制台编码是 GBK/cp936）。

    脚本输出里只要出现非 GBK 字符（如 U+2212 数学减号），在「输出被重定向到文件
    或管道」时就会抛 UnicodeEncodeError、进程 exit 1——结果文件根本产不出来。
    强制重设编码后，README 里的命令不需要任何环境变量即可跑完并返回真实退出码。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


def main():
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="metrics.json → 配图")
    ap.add_argument("--exp", default=EXP, help="实验 ID")
    args = ap.parse_args()
    exp = args.exp
    data_dir = os.path.join(REPO, "data", exp)
    out_dir = os.path.join(REPO, "experiments", exp)
    fig_dir = os.path.join(REPO, "figures", exp)
    os.makedirs(fig_dir, exist_ok=True)

    mp = os.path.join(out_dir, "metrics.json")
    if not os.path.exists(mp):
        raise SystemExit(f"缺少 {mp}；先跑 scripts/build_analysis.py --exp {exp}")
    M = json.load(open(mp, encoding="utf-8"))

    fig1(); fig2(data_dir); fig3(M); fig4()
    print(f"done → figures/{exp}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
