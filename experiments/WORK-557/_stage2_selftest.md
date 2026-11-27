## 段2｜四档 × 2 seed 全窗 GPU 训练（2009–2019，≤50 轮）+ 回测

**结论（一句）**：

- 四档 + GRU 基线在 RTX 3060 上按预注册 `3b4a7df` 的**全训练窗 2009–2019**、**每阶段 ≤50 轮**完成，产物 **0 档**（seed -）；回测已按 TopK-DropN K=30 N=5 只做多、买 5 / 卖 15 bps 出表。
- ⚠️ **只有 0 个 seed 跑完**，未满足预注册的「≥2 seed」要求；H1/H2/H3 的判词留待段 3，本段**不**宣称任何「进/退」。

---

### 1. 环境实测（自检原文）

```
$ nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
NVIDIA GeForce RTX 3060, 12288 MiB, 610.88

torch=<未取到> | cuda=<未取到> | device=<未取到>
nproc=<命令失败: FileNotFoundError(2, '系统找不到指定的文件。', None, 2, None)> | disk_free(GiB)=94.7
git_HEAD=bba0a6c79521 | 预注册_commit=3b4a7df
threads=8 | seeds=42,2024 | 协议=训练窗 2009-01-01~2019-12-31；每阶段 ≤50 轮；patience 15 / min_delta 1e-5；种子 42/2024
数据快照 index sha256=<未取到>
```

### 2. 数字表

#### 2.1 训练侧：每档每 seed 的训练轮数、早停轮次、s/epoch、峰值显存、总秒数

来源：`experiments/WORK-557/training_log.json`（字段 `runs.<arm>_s<seed>.*`）

| 档 | seed | 实际轮数 | 早停轮次 | stage1 轮数 | s/epoch | 峰值显存(GB) | 总秒数 | 设备 |
|---|---|---|---|---|---|---|---|---|

#### 2.2 回测侧：每档每 seed 的 RankIC / RankICIR / SR / 年化 / MDD / 单边换手 / 交易日

来源：`experiments/WORK-557/metrics_table.csv`；组合口径 TopK-DropN K=30 N=5 只做多，成本主档 买 5 / 卖 15 bps（WORK-503 注册口径）。

| 档 | seed | RankIC | RankICIR | SR | 年化 | MDD | 单边换手 | 交易日 |
|---|---|---|---|---|---|---|---|---|

### 3. 可复跑命令

```powershell
cd C:\Users\MO_Yongkai\multica_workspaces_cloud\workspace-c8faa36a018a\work-557-a221b7629a32\workdir\quant-lab
$env:MLFLOW_DISABLE_AGENT_HINT="1"
.venv\Scripts\python.exe -u scripts\work557_pipeline.py
.venv\Scripts\python.exe -u scripts\work557_train_all.py --epochs 50 --gru-first --no-wait-gpu --max-run-seconds 7200 --workers 8
.venv\Scripts\python.exe -u scripts\work557_finish.py
```

产物：训练 `experiments/WORK-557/preds/{arm}_s{seed}.json|parquet`；回测 `experiments/WORK-557/backtest_results.json`；指标表 `experiments/WORK-557/metrics_table.csv`；判词 `experiments/WORK-557/verdict.json`；成本敏感性 `experiments/WORK-557/cost_sensitivity.csv`。

### 4. 缺口与不确定项

1. 后处理链自检记录（`finish_summary.json`）**未取到**
2. **未训出**的档位：full_s42、full_s2024、no_vq_s42、no_vq_s2024、no_moe_s42、no_moe_s2024、no_prior_s42、no_prior_s2024、gru_s42、gru_s2024（缺档原因见 `training_log.json` 的 `failures`/`stopped` 字段）
3. **n_seeds=0 < 2**：H1/H2/H3 的判词不成立，不得据此宣称「进/退」
4. **涨跌停掩码口径照抄 WORK-503**：其 `limit_up`/`limit_down` 判定约为库内真值的 3.02×/5.00×（段1 交叉核对量化）；本单**不改**该口径以保持与 WORK-503 的 apples-to-apples，偏差只披露。
5. **先验口径**：自建 6 个 A 股因子（论文为 JKP 13 因子）；**数据底座**：quantdb（tushare 系）重建，非 Qlib cn_data。
6. **训练代码跨两个 commit**：GRU s42 跑在 `4969ca6`（吞吐修复），其余档跑在其后仅改非数值项（基线 parquet 文件名、缓存键、死导入）的 commit 上；不影响样本顺序、batch 组成与数值。
7. **本段不含** H1 Δ_drop / H2 block-bootstrap CI / H3 净 SR 判词、两张对照表与 report.html/PNG/PDF（分别属段 3、段 4）。

{{USAGE_LINE}}

