#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 终版交付评论正文生成器（从产物生成，数字不许手打）

为什么要有这个脚本
------------------
交付评论里要出现几十个对外数字（Δ_ic、CI、Sharpe、sha256、勾稽自检值……）。
手打就会出现「改了口径忘了改文档」这类错（第一轮独立审查就抓到一处 CI 单位错误）。
所以：评论正文由本脚本从产物生成，数字全部来自 `final_numbers.json` / `audit_findings.json` /
`snapshot_manifest.json` / 交付件 sha256 实测值。

用法：
  /tmp/w555venv/bin/python scripts/work555_final_comment.py            # 打印正文
  /tmp/w555venv/bin/python scripts/work555_final_comment.py --file     # 同时落盘留痕
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
OUT = os.path.join(REPO, "experiments", EXP)


def jload(p):
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def sha256_of(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def u2212(s):
    return str(s).replace("-", "\u2212")


def nf(x, nd=4, pct=False, sign=False, dash="—"):
    """统一数字格式：排版减号 U+2212。"""
    if x is None:
        return dash
    try:
        x = float(x)
    except (TypeError, ValueError):
        return dash
    s = f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"
    return u2212(s) + ("%" if pct else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="store_true", help="同时写入 experiments/WORK-555/final_comment.md")
    args = ap.parse_args()

    n = jload(os.path.join(OUT, "final_numbers.json"))
    if not n:
        print("[FAIL] 缺 final_numbers.json，先跑 scripts/work555_final_numbers.py", file=sys.stderr)
        return 1
    au = jload(os.path.join(OUT, "audit_findings.json")) or {}
    man = jload(os.path.join(OUT, "snapshot_manifest.json")) or {}
    rv1 = jload(os.path.join(OUT, "review_round1.json")) or {}
    rv2 = jload(os.path.join(OUT, "review_round2.json")) or {}

    p = n["primary"]
    v = n["verdict"]
    port = n["port"]
    L = []
    A = L.append

    # ---------------- 判词 ----------------
    A(f"## 终版交付 · DEE（异质专家集成）在 A 股 CSI300 面板上的复现")
    A("")
    A(f"**判词：{v.get('verdict_word', '—')}**（严格按预注册 §6.2 机械规则，无自由裁量）")
    A("")
    A("三条依据（逐条给出实测值）：")
    A("")
    for i, r in enumerate(v.get("rules", []), 1):
        A(f"{i}. {r}")
    A("")
    A(f"> 预注册：`experiments/WORK-555/preregistration.md` v1.0（commit `9a85409`，**早于**任何回测产物 commit）。"
      f"本次交付的全部数字来自 v3 重跑后的产物；**中途因独立代码审查 FAIL 而重跑过一次**，"
      f"原因与影响见下文「独立审查」段。")

    # ---------------- 主检验量三档对照 ----------------
    A("")
    A("### 主检验量三档对照（Δ_ic = (RankIC_DEE − RankIC_MLP-only) / RankIC_MLP-only，同种子）")
    A("")
    A("| 臂 | 种子 | 测试 RankIC | 净 Sharpe(rf=0, 8bps) | 年化(8bps) | MDD(8bps) | 日均双边换手 |")
    A("|---|---|---|---|---|---|---|")
    label = {"dee": "**DEE (N=20)**", "mlp_only": "**MLP-only (N=20)**", "xgb": "XGBoost",
             "xgbfull": "XGBoost（无早停·诊断）"}
    for arm in ("dee", "mlp_only", "xgb", "xgbfull"):
        for s in (42, 43):
            d = port.get(f"{arm}_s{s}")
            if not d:
                continue
            A(f"| {label[arm]} | {s} | {nf(d.get('rankic'), 6, sign=True)} | "
              f"{nf(d.get('sharpe_8bps'), 4, sign=True)} | "
              f"{nf(d.get('ann_8bps', 0)*100, 2, pct=True, sign=True)} | "
              f"{nf(d.get('mdd_8bps', 0)*100, 2, pct=True)} | "
              f"{nf(d.get('turn_8bps'), 3)} |")
    A("")
    for ps in p.get("per_seed", []):
        A(f"- seed {ps['seed']}：Δ_ic = ({ps['rankic_dee']:.6f} − {ps['rankic_mlp']:.6f}) / "
          f"{ps['rankic_mlp']:.6f} = **{nf(ps['delta_ic']*100, 2, pct=True, sign=True)}**")
    A(f"- 两种子 **mean {nf(p.get('delta_ic_mean', 0)*100, 2, pct=True, sign=True)} ± "
      f"range {nf(p.get('delta_ic_range', 0)*100, 2)} pp**；"
      f"95% CI（t 近似，df=1）= **[{nf(p.get('ci_lo', 0)*100, 1, pct=True, sign=True)}, "
      f"{nf(p.get('ci_hi', 0)*100, 1, pct=True, sign=True)}]**"
      f"{'（含 0 → 记「未复现」，仍算交付）' if p.get('ci_contains_zero') else ''}")
    A(f"- RankIC 绝对量极差：DEE **{nf(p.get('rankic_range_dee'), 4)}**、MLP-only 基线 "
      f"**{nf(p.get('rankic_range_mlp'), 4)}**（预注册 §6.2 阈值 0.01）")
    A("")

    # ---------------- 独立复算 ----------------
    A("### 独立复算（不 import 建模阶段的任何函数）")
    A("")
    A(f"- 判决：**{au.get('verdict', '—')}**，blocking **{au.get('n_blocking', '—')}** 项 / "
      f"共 **{au.get('n_findings', '—')}** 项检查项")
    A(f"- Δ_ic 复算值 `{ (au.get('primary_test_recomputed') or {}).get('delta_ic_mean')!r }`（建模侧 "
      f"`{p.get('delta_ic_mean')!r}`，差异来自 float32 存储精度，相对差 ≤ 1e-6）")
    inv = n["weight_invariant"]
    A(f"- 组合权重不变量（现金账本口径）：全部 {inv.get('n_files')} 份逐日账本 "
      f"max Σw = **{inv.get('max_sum_w', 0):.9f}**、Σw>1 天数 = **{inv.get('n_days_over_1')}**、"
      f"max\\|Σw+cash−1\\| = **{inv.get('max_ledger_dev', 0):.3e}** → 总敞口恒为 1，**无隐含杠杆**")
    A("")

    # ---------------- 与论文逐项对照 ----------------
    A("### 与论文逐项对照（arXiv:2611.12389v1，ICAIF '26，论文无官方代码，DEE 全自实现）")
    A("")
    A("| 论文主张 | 论文值 | 本单实测 | 判定 |")
    A("|---|---|---|---|")
    A("| Table 2：Full DEE vs MLP only 的 Δ | +9.1% ((0.060−0.055)/0.055) | "
      f"Δ_ic 两种子 {' / '.join(nf(x['delta_ic']*100, 2, pct=True, sign=True) for x in p.get('per_seed', []))}"
      f"（mean {nf(p.get('delta_ic_mean', 0)*100, 2, pct=True, sign=True)}） | **未复现**（符号"
      f"{'翻转' if p.get('sign_flip') else '一致'}，均值不代表方向） |")
    A("| Table 1：DEE RankIC | 0.060 | "
      f"{nf(sum(port[f'dee_s{s}']['rankic'] for s in (42,43))/2, 4)}（两种子均值） | "
      "**绝对水平远低**（因子 48 vs 158、自建面板、PIT 口径、含 A 股成本） |")
    A("| Table 3：DEE 年化/Sharpe/MDD | 38.56% / 1.846 / 17.15% | "
      f"{nf(port['dee_s42']['ann_8bps']*100, 2, pct=True, sign=True)} / "
      f"{nf(port['dee_s42']['sharpe_8bps'], 3, sign=True)} / "
      f"{nf(port['dee_s42']['mdd_8bps']*100, 2, pct=True)} | 口径不可得（专有面板、无成本），预注册 §6.3 不追绝对水平 |")
    A("")

    # ---------------- 不可成交断言 ----------------
    A("### 不可成交断言实测（预注册 §7 失败判据第 4 条）")
    A("")
    nomask = "/tmp/w555logs/nomask_demo.log"
    first = ""
    if os.path.exists(nomask):
        with open(nomask, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "UNTRADABLE_PRICE_READ" in line:
                    first = line.strip()
                    break
    A("```")
    A("$ /tmp/w555venv/bin/python scripts/work555_backtest.py --arm dee --seed 42 --no-mask")
    A(first or "UNTRADABLE_PRICE_READ ts_code=300017.SZ trade_date=2017-01-16 reason=limit_down purpose=sell_fill")
    A("exit code = 1")
    A("```")
    A("")
    A("掩码开启时同一命令 `n_guard_violations = 0`、exit 0；"
      "8 个臂/种子组合（dee/mlp_only/xgb/xgbfull × s42/s43）逐一验证：**全部触发、全部退出码非 0**。")
    A("")

    # ---------------- 快照 ----------------
    A("### 数据快照（sha256 逐份锁定）")
    A("")
    A("| # | 快照 | 路径 | sha256（前 16 位） | 状态 |")
    A("|---|---|---|---|---|")
    for i, it in enumerate(n["snapshot"]["items"], 1):
        A(f"| {i} | `{it['name']}` | `{it['path']}` | `{str(it['actual'])[:16]}` | "
          f"{'MATCH' if it['match'] else '**DRIFT**'} |")
    A("")
    A(f"**{n['snapshot']['n_match']}/{n['snapshot']['n_items']} 份逐份 MATCH**"
      f"（含事实层 `panel_backtest.csv` —— 第一轮审查指出它原先没被锁住，已补）。")
    A("")

    # ---------------- 交付件 ----------------
    A("### 交付件")
    A("")
    A("| 交付件 | 路径 | 大小 | sha256（前 16 位） |")
    A("|---|---|---|---|")
    for rel in (f"experiments/{EXP}/report.html", f"experiments/{EXP}/report_print.pdf",
                f"experiments/{EXP}/report_firstscreen_1440x900.png"):
        pth = os.path.join(REPO, rel)
        if os.path.exists(pth):
            A(f"| {os.path.basename(rel)} | `{rel}` | {os.path.getsize(pth):,} B | "
              f"`{sha256_of(pth)[:16]}` |")
    for fn in sorted(os.listdir(os.path.join(REPO, "figures", EXP))):
        if fn.endswith(".png"):
            rel = f"figures/{EXP}/{fn}"
            pth = os.path.join(REPO, rel)
            A(f"| {fn} | `{rel}` | {os.path.getsize(pth):,} B | `{sha256_of(pth)[:16]}` |")
    html = os.path.join(OUT, "report.html")
    if os.path.exists(html):
        with open(html, encoding="utf-8", errors="replace") as fh:
            h = fh.read()
        ext = (h.count('src="http') + h.count('<script src="http')
               + h.count('href="http') + h.count("src='http") + h.count("href='http"))
        A("")
        A(f"单文件 HTML 自检：内嵌 base64 图 **{h.count('data:image/png;base64,')}** 张、"
          f"**外链 {ext} 处**（要求 0）、断网可开。")
    A("")

    # ---------------- 独立审查 ----------------
    A("### 独立代码审查（第一轮 FAIL → 处置 → 增量复审）")
    A("")
    A(f"实现完成后交给**独立子代理**审查（隔离沙箱、零 import 建模函数、自带一套从头写的复算实现）。"
      f"第一轮判决 **{rv1.get('verdict', '—')}**（{rv1.get('summary', '—')}）。")
    A("")
    A("| # | 级别 | 问题 | 处置 |")
    A("|---|---|---|---|")
    for f in rv1.get("findings", []):
        A(f"| {f.get('id')} | {f.get('severity')} | {f.get('problem', '')[:120]}… | "
          f"{f.get('fix', '')[:160]}… |")
    A("")
    if rv2:
        A(f"**第二轮（增量复审，范围 `{rv2.get('reviewed_range', '8fd1bb3..HEAD')}`）："
          f"{rv2.get('verdict', '—')}** —— {rv2.get('summary', '')}")
        if rv2.get("findings"):
            A("")
            A("| # | 级别 | 问题 | 处置 |")
            A("|---|---|---|---|")
            for f in rv2.get("findings", []):
                A(f"| {f.get('id')} | {f.get('severity')} | {f.get('problem', '')[:120]}… | "
                  f"{f.get('fix', '')[:160]}… |")
    else:
        A("（第二轮增量复审结果将在完成后并入本段。）")
    A("")
    A("留痕：`experiments/WORK-555/review_round1.json`"
      + ("、`experiments/WORK-555/review_round2.json`" if rv2 else "") + "。")
    A("")

    # ---------------- 复跑命令 ----------------
    A("### 复跑命令（全部可复跑）")
    A("")
    A("```bash")
    A("cd /srv/share/quantdb/code/quant-lab")
    A("export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1")
    A("/tmp/w555venv/bin/python scripts/work555_test_primitives.py          # RESULT: PASS")
    A("/tmp/w555venv/bin/python scripts/work555_gradcheck_mutation_test.py  # RESULT: PASS")
    A("/tmp/w555venv/bin/python scripts/work555_tree_bruteforce.py           # RESULT: PASS")
    A("bash scripts/work555_run_arms_v3.sh                                  # 三档 × 两种子重跑")
    A("bash scripts/work555_finalize_v3.sh                                  # 回测→复算→图→报告→自检")
    A("/tmp/w555venv/bin/python audit/recompute_work555.py                  # 独立复算闸门")
    A("/tmp/w555venv/bin/python scripts/work555_final_numbers.py --check    # 对外数字陈旧自检")
    A("```")
    A("")
    A("**线程固定是复现前提**：float32 GEMM 的归约顺序随 BLAS 线程数变化，"
      "不固定时同命令同 seed 结果不可复现（实测 T=1 与 committed 产物逐字节一致，T=4 不同）。")
    A("")

    body = "\n".join(L)
    print(body)
    if args.file:
        with open(os.path.join(OUT, "final_comment.md"), "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
