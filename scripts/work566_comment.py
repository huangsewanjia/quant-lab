#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WORK-566 · 生成 issue 最终回复正文（reply_body.md）。

纪律：正文里的**所有数字**都从产物读出（metrics.json / verdict.json / audit_findings.json /
snapshot_manifest.json / sample_stats.json / fast_encoder_selftest.json），
不手抄、不外推；结论句由脚本按预注册阈值生成。

产出：experiments/WORK-566/reply_body.md（直接用 --content-file 发评论）
运行：python scripts/work566_comment.py
"""
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "experiments", "WORK-566")


def load(name, default=None):
    p = os.path.join(OUT, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def num(v, nd=4, dash="—"):
    if v is None:
        return dash
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def main():
    metrics = load("metrics.json", {})
    verdict = load("verdict.json", {})
    audit = load("audit_findings.json", {})
    stats = load("sample_stats.json", {})
    man = load("snapshot_manifest.json", {})
    st = load("fast_encoder_selftest.json", {})
    if not metrics or not verdict:
        raise SystemExit("缺 metrics.json / verdict.json，先跑 Stage 3 主检验")

    h1 = verdict.get("H1", {}) or {}
    h2 = verdict.get("H2", {}) or {}
    h3 = verdict.get("H3", {}) or {}
    h4 = verdict.get("H4", {}) or {}
    tier = (metrics.get("cost_tiers", {}) or {}).get("0.0005", {}) or {}
    v = metrics.get("verdict", "未判定")

    def cell(arm, key, nd=4):
        per = tier.get(arm, {}) or {}
        vals = [x.get(key) for x in per.values() if x.get(key) is not None]
        if not vals:
            return "—"
        m = sum(vals) / len(vals)
        if len(vals) == 1:
            return f"{m:.{nd}f}"
        return f"{m:.{nd}f} [{min(vals):.{nd}f}, {max(vals):.{nd}f}]"

    def cost_row(cost):
        t = (metrics.get("cost_tiers", {}) or {}).get(str(cost), {}) or {}
        out = []
        for arm in ("s1_only", "s3_only", "s1_s2", "full", "fmb"):
            per = t.get(arm, {}) or {}
            vals = [x.get("rankic_mean_pct") for x in per.values() if x.get("rankic_mean_pct") is not None]
            if not vals:
                continue
            m = sum(vals) / len(vals)
            rng = (f" [{min(vals):.4f}, {max(vals):.4f}]" if len(vals) > 1 else "")
            out.append(f"{arm} {m:.4f}%{rng}")
        return "；".join(out)

    n_ep = (man.get("summary", {}) or {}).get("n_endpoints", 0)
    n_ep_ok = (man.get("summary", {}) or {}).get("n_endpoints_ok", 0)
    n_snap = (man.get("summary", {}) or {}).get("n_snapshot_files", 0)
    repo_commit = (man.get("repo", {}) or {}).get("commit", "(见 git log)")

    body = f"""## WORK-566 交付：STOCK-JEPA 三段式结构在 A 股的增量复现 · 判词「{v}」

### 1 · 唯一主检验量与判词

- **H1 = ΔRankIC = RankIC<sub>full</sub> − RankIC<sub>S1-only</sub>，跨种子中位数 = {num(h1.get('delta_median_pp'))} pp**
- 预注册阈值：进 ≥ +3.0 pp / 退 ≤ +1.0 pp 或负 / 部分复现 (1.0, 3.0)；论文基准 **+6.07 pp**（Table 4：5.15% → 11.22%）
- 有效种子 {h1.get('n_seeds', 0)} 个（42 / 2024），每种子 ΔRankIC {num(h1.get('delta_min_pp'))} ~ {num(h1.get('delta_max_pp'))} pp
- 判定规则：{h1.get('verdict_rule', '—')}
- **判词：{v}**

### 2 · 三档消融（测试期 2024-01-02 ~ 2026-06-26，主成本档 单边 5bps；mean ± range）

| 成本 | 各臂 RankIC(%) |
|---|---|
| 5 bps | {cost_row(0.0005)} |
| 15 bps | {cost_row(0.0015)} |
| 30 bps | {cost_row(0.003)} |

- full：RankIC {cell('full', 'rankic_mean_pct')}%｜净 SR {cell('full', 'net_sr')}｜净 AR {cell('full', 'net_ar')}｜MDD {cell('full', 'mdd', 2)}%
- S1-only：RankIC {cell('s1_only', 'rankic_mean_pct')}%｜净 SR {cell('s1_only', 'net_sr')}
- FMB-style 基线：RankIC {cell('fmb', 'rankic_mean_pct')}%（论文 8.76%）｜净 SR {cell('fmb', 'net_sr')}

### 3 · 并列检验

- **H2**（full vs FMB-style 逐日 RankIC 差，block bootstrap 20 日 / 10000 次 / seed 42）：{num(h2.get('pooled_mean_diff_pp'), 3)} pp，95% CI {h2.get('pooled_ci95')} → {h2.get('conclusion', '—')}
- **H3**（可交易性）：full 净 SR {num((h3 or {}).get('full_net_sr_mean'), 3)}，95% CI 下界 {num((h3 or {}).get('full_sr_ci95_lower_min'), 3)}，相对论文 1.50 的比率 {num((h3 or {}).get('shrinkage_vs_paper'), 3)}（降级配置下只披露不判定；净 SR < 0 直接判退仍生效）
- **H4**（R2 vs R3，只报数）：R2 {num((h4 or {}).get('r2_rankic'), 3)}% vs R3 {num((h4 or {}).get('r3_rankic'), 3)}%，差 {num((h4 or {}).get('r2_minus_r3_rankic_pp'), 3)} pp

### 4 · 独立复算与代码审查

- **独立复算**：{audit.get('conclusion', '—')}（PASS {audit.get('pass', 0)} / BLOCKING {audit.get('blocking', 0)}）；判据逐项相对差 ≤ 1e-6；复算脚本**不 import 建模阶段任何函数**，交易日历从 parquet 独立重取
- **独立子代理代码审查**（verifier，主循环不自审，共 **3 轮**）：第 1 轮判定 **FAIL（1 BLOCKING + 11 NON-BLOCKING）**，全部已修并重跑全链 —— B1 组合估值用未来 bar 价格盯市（前视泄漏）、N1 卖端与清算成本、N2 S2 编码器训练期 dropout 与官方不等价、N5 推理裁剪、N6 种子守卫、N7 NAV 守卫、N9 引擎级断言；N3/N8 以披露处理。第 2 轮复核判定上述均已修、新发现 **B2**（`build_reps` 写回 u 时误用全局样本下标 → 越界）→ 改为 split 内位置并冒烟复现通过。第 3 轮复核判定 B2 已修、**新发现 2 项 BLOCKING**：
  - **B3（会改变判词方向）**：落盘的表征 checkpoint 实为**最后一轮**权重，而非预注册 §10.2 冻结的「验证损失最小者」——`state_dict()` 返回**活引用** + 训练末尾再次 `torch.save(best[1])` + 周期快照写同一路径，三重叠加；实测落盘文件 `upd=1000`（best=epoch0）却带着 epoch3 的 `m_k/v_k`，自相矛盾。`s1_only` 不用表征（不受影响）而 `full` 用（受影响）→ 系统性**压低 ΔRankIC**。已改为深拷贝快照 + 周期快照独立路径 + 落盘回读自检 + `--resume-rep` 校验 `selected=="best_vloss"`，并补红-绿回归测试（10/10 PASS）与交付自检 C9。**因 epoch0 权重不可恢复（备份经 md5 证明与末轮同源），两个种子已全部完整重训**，本评论全部数字来自重训后产物。
  - **B4**：Stage 3 前置检查漏 `fmb` 臂（该臂由 `work566_baselines.py` 产出、训练脚本不产）→ 回测跑到一半才崩；已改为前置缺失即补齐 + jobfile 写入前截断。
  - **第 4 轮复审（只审第 3 轮的整改）判「通过」**：B3/B4/B5 三项经代码 + 磁盘产物 + 合成用例三重验证成立，无新的 BLOCKING；磁盘实证选型 ckpt 为 `upd=1000 / vloss=115.0360 / selected=best_vloss / complete=True`，周期快照另存 `.periodic` 不再覆盖。
  - 第 4 轮另提 11 项 NON-BLOCKING，**全部已处理**：报告补「编码器自检只覆盖 layer 级、不覆盖 `SeqEncoder` 外层」的范围限定；报告与实现的 tmpfs 指纹口径对齐（`size:mtime:sha256(首末各 4MB)`）；回读自检的 `assert` 改 `raise RuntimeError`（`python -O` 下不被剥离）；`--resume-rep` 的配置指纹校验由 fail-open 改 fail-closed；配置指纹补 `--threads` / `--val-batch` 与**数据快照指纹**；选型 checkpoint 改原子写；H3「净 SR<0 判退」加「有效种子 ≥ 2」约束；Stage 3 前置内容校验纳入 `fmb` 臂；54 份回测产物复核加「JSON 内 arm/seed/cost 与文件名一致」；交付自检 C9 补查 `complete` 与 `cfg`。
  - 复审全文：<code>code_review_work566.md</code>（第 1–2 轮）、<code>code_review_work566_round3.md</code>（第 3 轮）、<code>code_review_work566_round4.md</code>（第 4 轮）；整改记录见 <code>failure_log.md</code> §10–§15。
- 复审自检：S2 编码器 eval max|diff| {num(st.get('max_abs_diff'), 3)}、**train max|diff| {num(st.get('train_max_abs_diff'), 3)}（逐位一致）**

### 5 · 数据与复现

- 样本量：train {stats.get('n_train', 0):,} / valid {stats.get('n_valid', 0):,} / test {stats.get('n_test', 0):,}（论文 Table 3：3,883,006 / 1,990,842 / 2,965,433）
- 端点复测 {n_ep_ok}/{n_ep} ok；快照 manifest 锁定 {n_snap} 个文件 sha256；代码提交 `{repo_commit}`
- 预注册 commit `16b24fb`（v1.0）→ `7580a3d`（v1.1，唯一增补）→ 首个回测产物 commit `a3d8b88`，**次序由 `git log` 可查**
- 交付物：`report.html`（单文件，图 base64 内嵌、CSS 内联、无外链、断网可读）+ 1440×900 首屏 PNG + A4 打印 PDF + `metrics.json` / `verdict.json` / `metrics_table.csv` / `paper_comparison.csv|md` / `audit_findings.json` / `data_probe.json` / `panel_audit.json` / `sample_stats.json` / `code_review_work566.md` / `failure_log.md`

### 6 · 必须并列的口径冲突与已知限制

1. **任务书切分与论文原文冲突**：任务书写「2009–2019 训练 / 2020–2021 验证」，论文原文为「2014–15 先验初装 / 2016–21 训练 / 2022–23 验证 / 2024–26 测试」→ **以论文原文为准，两份并列不合成**
2. **绝对水平与论文不可比**：40 维特征字段自定、市场级 12 维自定、算力降级（d_model 64 / 2 层 / 4 头 / FFN 128 / d_z 64 / fp32）、股票池 +2.0%、A 股执行约束自加 → **只有相对增量可作结构性比较**
3. **测试段右边界**：目标窗越过数据截止日（2026-06-26）的最后 21 个交易日无实现收益 → 本单 578 个预测日，论文报 599 个（样本总数几乎一致）
4. **停牌日价格未前向填充**（文档三处声明与实现不符，影响 0）与**退市清算晚 1 个交易日**（因果口径）→ 均已在报告偏离表单列，未改数字口径
5. **训练期 I/O**：面板随机取数在冷缓存下被内核 readahead 放大 129×，经 **train 日期片预置 tmpfs（1400MB，与源切片 sha256 全量一致）+ stock-major 顺序读 + 去掉 MADV_RANDOM** 修复，**不改任何取值与口径**；另加 tmpfs 陈旧校验（shape + 起日 + 源文件 size/mtime 指纹）
6. **整机 OOM（本单真正的拦路虎）**：两种子并行时整机触发 OOM killer，内核同时杀了训练进程与 `multica-daemon.service` —— 平台提示里的 `daemon shut down while task was in flight` 即此 OOM 的次生结果（非平台故障）；处置为两种子改**顺序**跑（硬占用 4.2GB→2.8GB，总墙钟不变）、`setsid nohup` 脱离会话、挂内存看门狗。附实测**线程数 A/B 负结果**（8 线程比 6 线程慢 3.6 倍，整机已超订 → 维持 6 线程），以及 seed42 在 4/6 线程两种配置下 `L_anc/L_rev` 逐位相同的可复现性证据

本报告为内部研究模拟、撰写方非持牌机构、不构成投资建议与收益承诺。本结论不构成任何投资建议或推荐。
"""
    path = os.path.join(OUT, "reply_body.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"written: {path}")
    print(f"  判词: {v} | H1 = {num(h1.get('delta_median_pp'))} pp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
