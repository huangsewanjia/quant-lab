# WORK-449 预注册修订 v1.3（模型训练协议：对齐论文 §3.5.2）

> **本文件是一次协议修订**，写于**任何使用修订后超参的模型产物提交之前**。
> v1.0 / v1.1 / v1.2 / v1.3 的 commit 时间均早于任何回测产物 commit。
> **未变更**：核心假设、研究对象、股票池（`000300.SH` 生效日名单）、样本窗（2015-01-01 ~ 2024-12-31）与三段切分、因子集（12 个及其窗口）、三档掩码定义、成本模型（8 bps 双边）、组合参数（α=10、w_max=3%、LedoitWolf 120 日）、主检验量（ΔSharpe）、判定规则、退出门槛、复权序列（hfq）、数据源（AKShare/东财）。
> **变更**：仅限**优化器与训练超参**，并说明为什么必须改。

---

## 1. 变更原因（实测）

### 1.1 v1.0 的超参是从「通用默认」写的，不是从论文 §3.5.2 抄的

预注册 v1.0 §7 的「优化器」行原文写的是：

> | **优化器** | Adam，`lr = 1e-3`，`weight_decay = 1e-4`，batch 4096 | 常规默认，写死 |

理由栏写「常规默认，写死」——**即当时并没有从论文的 "Training protocol" 段落取值，而是填了一组通用默认值**。这是转录错误，不是研究设计。

### 1.2 论文原文（arXiv:2507.07107v2 §3.5.2 "Training protocol"）

> "AdamW, lr 5×10⁻⁴, weight decay 10⁻⁵, gradient clipping 1.0, batch size 8192, up to 60 epochs, early stopping on 10% validation holdout. These are standard choices; we found the system not very sensitive to them."

取数方式：2026-11-20 抓取 `https://arxiv.org/html/2507.07107v2` 全文（约 6.0 万字符纯文本），定位 §3.5.2 "Training protocol" 段落。

### 1.3 官方实现侧交叉验证

官方仓库 `initial-d/ml-quant-trading`（MIT）的 `configs/paper.yaml`（文件头注释 "Full paper-reproduction config"）：

```yaml
train:
  epochs:       60
  batch_size:   8192
  lr:           5.0e-4
  weight_decay: 1.0e-5
  grad_clip:    1.0
```

官方 `src/mlquant/training/trainer.py` 的 `TrainConfig` 默认值 `grad_clip=1.0`、`weight_decay=1e-5`，优化器为 **`torch.optim.AdamW`**（非 `Adam`），并在每个 step 执行 `nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)`。

**论文 §3.5.2 与官方实现逐项一致**，v1.0 与两者都不一致。

### 1.4 架构没有错（一个需要澄清的冲突）

论文 §3.5.3：

> "MLP. Two hidden layers (128 units, GELU, dropout 0.1), linear output to scalar. **Input: 213-dimensional factor vector. Total parameters: ~44K.**"

参数核对：213×128+128 + 128×128+128 + 128+1 = 27,392 + 16,512 + 129 = **44,033 ≈ 44K** ✓。与论文自述完全吻合，**hidden = 128 正确**。

但官方 `configs/paper.yaml` 写 `hidden: 256`——**官方配置与论文正文矛盾**（256 会得到约 121K 参数，与论文 "~44K" 不符）。

**裁定：以论文正文为准，hidden = 128 不变**，并在失败日志中记录该矛盾（官方配置与论文正文不一致，单一来源无法自洽，采用论文正文 + 参数数目自洽性双重校验）。

### 1.5 代价证据

用 v1.0 超参（Adam / lr 1e-3 / wd 1e-4 / batch 4096 / 无梯度裁剪）实跑 `no_mask` 臂：单臂墙钟 **29 分钟**（11:18:07 → 11:47:04）。
用本修订超参实跑 `no_mask` 臂：**2.5 s/训练 epoch + 0.17 s/验证 epoch**，60 epoch 约 **2.7 分钟**。

（两者在同一台主机、同一 `torch.set_num_threads(2)` + `OMP_NUM_THREADS=1` 设置下测量；慢的确切机理未单独隔离，失败日志按「实测两个口径」记录，不断言因果。）

---

## 2. 变更内容

| 项 | v1.0 | **v1.3** | 依据 |
|---|---|---|---|
| 优化器 | Adam | **AdamW**（`torch.optim.AdamW`，解耦权重衰减） | 论文 §3.5.2；官方 `trainer.py` |
| 学习率 | 1e-3 | **5e-4** | 论文 §3.5.2；官方 `paper.yaml` |
| 权重衰减 | 1e-4 | **1e-5** | 论文 §3.5.2；官方 `paper.yaml` |
| 梯度裁剪 | 无 | **1.0**（`clip_grad_norm_`） | 论文 §3.5.2；官方 `TrainConfig.grad_clip` |
| batch | 4096 | **8192** | 论文 §3.5.2；官方 `paper.yaml` |
| 隐藏层单元 | 128 | **128（不变）** | 论文 §3.5.3 + 44K 参数自洽校验 |
| epoch 上限 | 60 | **60（不变）** | 论文 §3.5.2 |
| seed | 42 | **42（不变）** | 论文 `paper.yaml` `seed: 42` |

## 3. 明确**未**改动的两项（有意保留差异，并披露）

1. **验证段与选 epoch 规则**：论文是「10% validation holdout，按 val loss 早停」；本单仍用 **2015-2020 训练 / 2021 验证（逐日 RankIC 选 epoch）/ 2022-2024 样本外测试**。
   - **保留理由**：本单的比较对象是**同一模型在三档掩码下的相对差异**，不是绝对 Sharpe；本单的切分更严格（独立验证年 + 完整 3 年样本外），且 v1.0 已冻结。若改用论文的 10% 时间留出，等于把 2021 段并入训练，样本外区间只剩 2022-2024 且训练/验证不再独立。
   - **披露**：本单的绝对 IC / Sharpe 因此不与论文 Table 11 逐位可比；论文对照表只做**方向性**对照，并逐项标注口径差异。
2. **因子维度 12 vs 213**：论文用 213 维因子，本单用论文因子子集 12 个（v1.0 冻结，见 §「因子集」）。hidden=128 在 12 维输入下参数更少（18,177），架构本身仍与论文一致。

## 4. 连带影响（必须披露）

1. **v1.0 超参下产出的模型与预测全部作废**：`pred_no_mask.npz` / `model_no_mask.pt`（11:47:04 落盘那一版）及作废轮次目录下的 `train_log_*.json` 一律不作为证据，已隔离到 `experiments/WORK-449/void_run_2026-11-20/`（`.gitignore` 排除，不入库）。
2. **三臂必须在本修订下同设置重训**才可比，且必须同 `torch.set_num_threads` 设置。
3. **本修订不改变任何判定规则**：ΔSharpe 的符号与 95% CI 判定、ΔIC 辅证、退出门槛 7 条全部原样。

## 5. 作废轮次登记

| 轮次 | 超参口径 | 产物 | 状态 |
|---|---|---|---|
| R0 | v1.0（Adam / 1e-3 / 1e-4 / 4096 / 无裁剪） | `train_log_no_mask.json`（11:47:04）、`train_log_full_mask.json`（10:54:25） | **作废**，移入 `void_run_2026-11-20/` |
| R1 | v1.0 + 旧协方差（LW2003 收缩公式） | `metrics_full_mask.json`、`metrics_no_mask.json` | **作废**（协方差退化成等比阵），同上隔离 |
| R2 | **v1.3（本修订）** | 三臂 `train_log_*.json` / `pred_*.npz` | **本单有效轮次** |
