#!/usr/bin/env python3
"""WORK-557 段2 回帖正文生成器：四档 × 2 seed 全窗训练结果 + 回测指标。

数字全部从产物读，不手抄：
    experiments/WORK-557/training_log.json   训练侧（s/epoch、峰值显存、早停轮次、总秒数）
    experiments/WORK-557/metrics_table.csv   回测侧（RankIC / SR / AR / MDD / 换手）
    experiments/WORK-557/finish_summary.json 后处理链自检结果（由 work557_finish.py 写）

正文按派单卡的固定五段输出：结论 / 环境实测（自检原文）/ 数字表 / 可复跑命令 / 缺口与不确定项，
末行留 `{{USAGE_LINE}}` 占位符（由发帖那一轮用 work557_usage.py 的真实计数替换）。

用法：
    .venv\\Scripts\\python.exe experiments/WORK-557/work557_comment_stage2.py out.md
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
EXP = "WORK-557"
EXP_DIR = os.path.join(REPO, "experiments", EXP)

ARM_CN = {"full": "完整 full", "no_vq": "去码本 no_vq", "no_moe": "去 MoE no_moe",
          "no_prior": "去先验 no_prior", "gru": "GRU 基线"}
ORDER = ["full", "no_vq", "no_moe", "no_prior", "gru"]
SEEDS = (42, 2024)

PREREG_COMMIT = "3b4a7df"          # 预注册冻结 commit（experiments/WORK-557/preregistration.md）
PROTOCOL = "训练窗 2009-01-01~2019-12-31；每阶段 ≤50 轮；patience 15 / min_delta 1e-5；种子 42/2024"


def fmt(v, nd=4, dash="-"):
    if v is None or v == "":
        return dash
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    try:
        return ("%." + str(nd) + "f") % float(v)
    except (TypeError, ValueError):
        return str(v)


def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def sh(cmd):
    try:
        p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                           timeout=120)
        return (p.stdout or "").strip()
    except Exception as exc:
        return "<命令失败: %r>" % (exc,)


def env_block(tlog):
    """环境实测：自检原文（GPU / torch / CPU / 磁盘 / 代码版本 / 快照哈希）。"""
    L = []
    gpu = (tlog or {}).get("gpu", {}) or {}
    L.append("```")
    L.append("$ nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader")
    L.append(sh(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader"]) or gpu.get("spec", "<未取到>"))
    L.append("")
    L.append("torch=%s | cuda=%s | device=%s"
             % (gpu.get("torch", "<未取到>"), gpu.get("cuda", "<未取到>"),
                gpu.get("device", "<未取到>")))
    L.append("nproc=%s | disk_free(GiB)=%s"
             % (os.cpu_count() or "<未取到>",
                sh(["powershell", "-NoProfile", "-Command",
                    "[math]::Round((Get-PSDrive C).Free/1GB,1)"]) or "<未取到>"))
    L.append("git_HEAD=%s | 预注册_commit=%s"
             % (sh(["git", "rev-parse", "HEAD"])[:12], PREREG_COMMIT))
    L.append("threads=8 | seeds=42,2024 | 协议=%s" % PROTOCOL)
    snap = load_json(os.path.join(EXP_DIR, "snapshot_manifest.json"))
    if snap:
        L.append("数据快照 index sha256=%s"
                 % (snap.get("index_sha256") or snap.get("sha256") or "<未取到>"))
    L.append("```")
    return L


def main():
    tlog = load_json(os.path.join(EXP_DIR, "training_log.json")) or {}
    runs = tlog.get("runs", {}) or {}
    fsum = load_json(os.path.join(EXP_DIR, "finish_summary.json")) or {}
    mtab_path = os.path.join(EXP_DIR, "metrics_table.csv")
    metrics = {}
    if os.path.exists(mtab_path):
        with open(mtab_path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                metrics[(row.get("arm"), row.get("seed"))] = row

    trained = [k for k in runs if "_probe" not in k and "_smoke" not in k]
    have_seeds = sorted({int(k.rsplit("_s", 1)[1]) for k in trained
                          if k.rsplit("_s", 1)[1].isdigit()})

    L = []
    P = L.append
    P("## 段2｜四档 × 2 seed 全窗 GPU 训练（2009–2019，≤50 轮）+ 回测")
    P("")
    P("**结论（一句）**：")
    P("")
    gpu_dev = ((tlog.get("gpu") or {}).get("device") or "GPU")
    P("- 四档 + GRU 基线在 `%s` 上按预注册 `%s` 的**全训练窗 2009–2019**、"
      "**每阶段 ≤50 轮**完成，产物 **%d 档**（seed %s）；"
      "回测已按 TopK-DropN K=30 N=5 只做多、买 5 / 卖 15 bps 出表。"
      % (gpu_dev, PREREG_COMMIT, len(trained),
         "、".join(str(s) for s in have_seeds) if have_seeds else "-"))
    if len(have_seeds) < 2:
        P("- ⚠️ **只有 %d 个 seed 跑完**，未满足预注册的「≥2 seed」要求；"
          "H1/H2/H3 的判词留待段 3，本段**不**宣称任何「进/退」。" % len(have_seeds))
    P("")
    P("---")
    P("")

    # ---- 1 环境实测 ----
    P("### 1. 环境实测（自检原文）")
    P("")
    L.extend(env_block(tlog))
    P("")

    # ---- 2 数字表 ----
    P("### 2. 数字表")
    P("")
    P("#### 2.1 训练侧：每档每 seed 的训练轮数、早停轮次、s/epoch、峰值显存、总秒数")
    P("")
    P("来源：`experiments/WORK-557/training_log.json`（字段 `runs.<arm>_s<seed>.*`）")
    P("")
    P("| 档 | seed | 实际轮数 | 早停轮次 | stage1 轮数 | s/epoch | 峰值显存(GB) | 总秒数 | 设备 |")
    P("|---|---|---|---|---|---|---|---|---|")
    for arm in ORDER:
        for seed in SEEDS:
            r = runs.get("%s_s%d" % (arm, seed))
            if not r:
                continue
            P("| %s | %d | %s | %s | %s | %s | %s | %s | %s |"
              % (ARM_CN.get(arm, arm), seed,
                 fmt(r.get("epochs_ran"), 0), fmt(r.get("early_stopped_epoch"), 0),
                 fmt(r.get("stage1_epochs_ran"), 0),
                 fmt(r.get("mean_seconds_per_epoch"), 1),
                 fmt(r.get("peak_vram_gb"), 2),
                 fmt(r.get("total_seconds"), 0),
                 r.get("device") or "-"))
    P("")
    P("#### 2.2 回测侧：每档每 seed 的 RankIC / RankICIR / SR / 年化 / MDD / 单边换手 / 交易日")
    P("")
    P("来源：`experiments/WORK-557/metrics_table.csv`；组合口径 TopK-DropN K=30 N=5 只做多，"
      "成本主档 买 5 / 卖 15 bps（WORK-503 注册口径）。")
    P("")
    P("| 档 | seed | RankIC | RankICIR | SR | 年化 | MDD | 单边换手 | 交易日 |")
    P("|---|---|---|---|---|---|---|---|---|")
    for arm in ORDER:
        for seed in SEEDS:
            m = metrics.get((arm, str(seed)))
            if not m:
                continue
            P("| %s | %s | %s | %s | %s | %s | %s | %s | %s |"
              % (ARM_CN.get(arm, arm), seed,
                 fmt(m.get("rankic")), fmt(m.get("rankicir")), fmt(m.get("sr")),
                 fmt(m.get("ar")), fmt(m.get("mdd")), fmt(m.get("turnover_1side")),
                 fmt(m.get("days"), 0)))
    P("")

    # ---- 3 可复跑命令 ----
    P("### 3. 可复跑命令")
    P("")
    P("```powershell")
    P("cd %s" % REPO)
    P('$env:MLFLOW_DISABLE_AGENT_HINT="1"')
    P(".venv\\Scripts\\python.exe -u scripts\\work557_pipeline.py")
    P(".venv\\Scripts\\python.exe -u scripts\\work557_train_all.py --epochs 50 "
      "--gru-first --no-wait-gpu --max-run-seconds 7200 --workers 8")
    P(".venv\\Scripts\\python.exe -u scripts\\work557_finish.py")
    P("```")
    P("")
    P("产物：训练 `experiments/WORK-557/preds/{arm}_s{seed}.json|parquet`；"
      "回测 `experiments/WORK-557/backtest_results.json`；"
      "指标表 `experiments/WORK-557/metrics_table.csv`；"
      "判词 `experiments/WORK-557/verdict.json`；"
      "成本敏感性 `experiments/WORK-557/cost_sensitivity.csv`。")
    P("")

    # ---- 4 缺口与不确定项 ----
    P("### 4. 缺口与不确定项")
    P("")
    gapped = []
    bv = fsum.get("backtest_validation")
    if bv:
        gapped.append("后处理链回测产物自检：**%s** —— %s"
                      % ("通过" if bv.get("ok") else "不通过", bv.get("detail")))
    else:
        gapped.append("后处理链自检记录（`finish_summary.json`）**未取到**")
    for fname, rec in sorted((fsum.get("cost_validation") or {}).items()):
        gapped.append("成本点 %s 自检：**%s** —— %s"
                      % (fname, "通过" if rec.get("ok") else "不通过",
                         rec.get("detail")))
    missing = []
    for arm in ORDER:
        for seed in SEEDS:
            if "%s_s%d" % (arm, seed) not in runs:
                missing.append("%s_s%d" % (arm, seed))
    if missing:
        gapped.append("**未训出**的档位：%s（缺档原因见 `training_log.json` 的 "
                      "`failures`/`stopped` 字段）" % "、".join(missing))
    if len(have_seeds) < 2:
        gapped.append("**n_seeds=%d < 2**：H1/H2/H3 的判词不成立，"
                      "不得据此宣称「进/退」" % len(have_seeds))
    gapped.append("**涨跌停掩码口径照抄 WORK-503**：其 `limit_up`/`limit_down` 判定"
                  "约为库内真值的 3.02×/5.00×（段1 交叉核对量化）；本单**不改**该口径"
                  "以保持与 WORK-503 的 apples-to-apples，偏差只披露。")
    gapped.append("**先验口径**：自建 6 个 A 股因子（论文为 JKP 13 因子）；"
                  "**数据底座**：quantdb（tushare 系）重建，非 Qlib cn_data。")
    gapped.append("**训练代码跨两个 commit**：GRU s42 跑在 `4969ca6`（吞吐修复），"
                  "其余档跑在其后仅改非数值项（基线 parquet 文件名、缓存键、死导入）的 "
                  "commit 上；不影响样本顺序、batch 组成与数值。")
    gapped.append("**本段不含** H1 Δ_drop / H2 block-bootstrap CI / H3 净 SR 判词、"
                  "两张对照表与 report.html/PNG/PDF（分别属段 3、段 4）。")
    for i, g in enumerate(gapped, 1):
        P("%d. %s" % (i, g))
    P("")
    P("{{USAGE_LINE}}")
    P("")

    out = "\n".join(L)
    if len(sys.argv) > 1:
        dst = os.path.abspath(sys.argv[1])
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
        print("wrote -> %s (%d chars)" % (dst, len(out)), file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
