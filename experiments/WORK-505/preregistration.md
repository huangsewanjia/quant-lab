# WORK-505 预注册 v2（Transformer 版三档掩码对照）

> **本件性质**：**协议冻结件（pre-registration）**。在 WORK-505 的**任何回测产物**之前落盘并
> commit；commit 时间必须早于任何 `metrics_*` / `primary_test` / `report.html` 的 commit 时间
> （可用 `git log` 查）。
>
> **底稿**：WORK-449 预注册 **v1.4**（`experiments/WORK-449/preregistration.md` + `preregistration_v1.1.md`
> … `_v1.4.md`）。**除 §7「模型与训练」外，其余条款逐字沿用，不改一个数。**
>
> **本单要回答的问题**：WORK-449 用 **MLP** 跑三档掩码，判词「未复现（退）」，且 **换 seed(43)
> 后判词翻转**。WORK-449 §11.1 已诊断出模型端硬伤：MLP 预测的截面离散度只有标签的 **0.58%**、
> 测试 IC 0.0077，而同组 12 因子单因子 |RankIC| 已有 0.038–0.058、OLS 基线 0.0146
> ⇒ **模型把输入里的信号压掉了**。本单把模型换成 **Transformer**（横截面注意力，容量与归纳偏置
> 都不同），**其余一切不动**，重跑三档掩码对照，**重新算判词**。
>
> **本单不作的事**：不实现论文的 213 因子（另一单的活）；不重取数；不改 `data_contracts/`；
> 不改 WORK-449 任何产物；不写对外研报；不给投资建议、评级或目标价。
>
> **执行日期**：2026-11-22 · **平台单号**：WORK-505 · **执行方**：算力实验执行（RTX 3060 / DESKTOP-TS1FCDR）

---

## 0. 与 WORK-449 的可比性（**本单新增节**）

### 0.1 唯一变量

| 项 | WORK-449（R2 主交付轮次） | WORK-505 | 是否变化 |
|---|---|---|---|
| 数据快照 | `experiments/WORK-449/snapshot_manifest.json`（680 条逐股 sha256） | **同一份快照、同一份 sha256 清单** | **否** |
| 股票池 | `000300.SH` 生效日名单（123 份 / 680 只，历史并集） | **同** | **否** |
| 样本窗 | 快照 2014-01-02~2024-12-31（2676 交易日） | **同** | **否** |
| 切分 | 训练 2015-2020 / 验证 2021 / 样本外 2022-01-04~2024-12-31（726 交易日） | **同** | **否** |
| 因子集 | 12 个（`scripts/work449_build_factors.py` → `FACTOR_SPEC`） | **同** | **否** |
| 因子口径 | MAD 5× 去极值 + z-score，**在 `M_factor` 内**做 | **同** | **否** |
| 标准化 | 训练段均值/标准差，测试段只应用不重算 | **同** | **否** |
| 掩码三档 | `no_mask` / `halt_only` / `full_mask`（§5.2 定义） | **同** | **否** |
| 损失 | AdjMSE，γ = 0.1 | **同** | **否** |
| 优化器 | AdamW，lr 5e-4，wd 1e-5，clip 1.0，batch 8192，≤60 epoch（v1.3 修正值） | **同** | **否** |
| 选 epoch | 2021 验证段逐日 RankIC 最优 checkpoint | **同** | **否** |
| 组合 | 只做多、单票 ≤3%、α=10、LedoitWolf 120 日、PSD 下限 1e-10、warm start | **同** | **否** |
| 成本 | 8 bps 双边（主）+ 5 bps（敏感性） | **同** | **否** |
| rf / 年化 / MDD | rf 1.5%；收益 365.25 自然日；波动 √252；MDD 收盘 cummax | **同** | **否** |
| bootstrap | 1000 次 / block 20 / seed 42 / 95% 百分位 CI / 配对同步重采样 | **同** | **否** |
| 判定词 | §9.4 两档（进 / 退） | **同** | **否** |
| **模型** | **MLP 12→128→128→1**（GELU, dropout 0.1） | **Transformer（见 §7）** | **是（唯一变量）** |
| **训练种子** | 42（R2）；43（R3，换种子稳健性） | **42 与 43 并列为主轮次** | **是（执行方式，见 §0.3）** |

> **口径声明**：本单与 WORK-449 的 ΔSharpe / ΔIC **只有在同一份快照上才可比**。
> 若快照 sha256 未达成 680/680，**不得**把本单数字与 WORK-449 的数字放进同一张对照表下判词。

### 0.2 训练种子的执行方式（**写死，不看结果再定**）

- **必须跑 ≥2 个训练种子：42 与 43**，三档掩码 × 2 seed = **6 次训练 + 6 次回测**，全部在 GPU 上。
- 报告口径：**每个指标报 mean ± range**（range = 两 seed 的极差），**不以单 seed 下判词**。
  理由（WORK-449 §11.3 / 教训）：WORK-449 换 seed 后 ΔSharpe 符号翻转（+0.551644 → −0.551457），
  两轮 CI 都不含 0 ⇒ 可实现 Sharpe 轴**功效不足，符号不由数据决定而由训练随机种子决定**。
- 单 seed 结果**只作附加证据**，不作判词依据；若两 seed 的 ΔSharpe 异号，**必须写进报告首屏**，
  且判词按 §9.4 在**两 seed 合并口径**下给出（合并口径见 §9.5）。

### 0.3 产物命名（写死）

| 产物 | 路径 | 说明 |
|---|---|---|
| 预注册 | `experiments/WORK-505/preregistration.md`（本文件） | 协议冻结件 |
| 快照校验 | `experiments/WORK-505/snapshot_verification.json` / `.csv` | 680 行逐股 sha256 判定 |
| 面板与掩码 | `experiments/WORK-505/panel.npz` + `panel_stats.json` | 与 WORK-449 同口径重建 |
| 因子 | `experiments/WORK-505/factors_<arm>.npz` | 三臂各一份 |
| 训练日志 | `experiments/WORK-505/train_log_<arm>_<seed>.json` | 6 份（每份带设备/线程/秒/显存） |
| 预测 | `experiments/WORK-505/pred_<arm>_<seed>.npz` | 格式与 WORK-449 **完全一致** |
| 三臂指标 | `experiments/WORK-505/metrics_<arm>_<seed>.json` | 6 份 |
| 主检验量 | `experiments/WORK-505/primary_test.json` | ΔSharpe、CI、判定 |
| 独立复算 | `experiments/WORK-505/audit_findings.json`（脚本 `audit/recompute_work505.py`） | 退出码 0 = 无 blocking |
| 报告 | `experiments/WORK-505/report.html` | 单文件，图片 base64 内嵌，0 外链 |
| 失败日志 | `experiments/WORK-505/failure_log.md` | 跑挂的尝试、OOM、搬运失败原文 |
| 登记 | `experiments/registry.md` 加一行 | 单号 / 论文 / 模型 / 参数 / 结论 / 产物路径 + sha256 |

---

## 1. §7「模型与训练」替换条款（**本单唯一改动的协议条款**）

> 其余章节（§0–§6、§8–§12）**逐字沿用 WORK-449 v1.4**，包括 §9.4 判定词、§10 七条退出门槛、
> §4 数据源与字段口径、§5 掩码三档、§6 因子集、§8 组合与成本。本节只替换「模型」与「训练循环」。

### 1.1 模型配置（**6 项写死，无「视情况」「可调」「待定」**）

| # | 项 | 规定值 | 依据 |
|---|---|---|---|
| **M1** | **架构** | **横截面 Transformer（`CrossSectionTransformer`）**：股票序列 = 一个交易日的横截面，每只股票一个 token | 论文 arXiv:2507.07107v2 Table 11 用 Transformer + GBM 增广；本单用其中 **Transformer** 部分 |
| **M2** | **序列长度 `seq`** | **680**（= 快照股票池股票数，=`snapshot_manifest.json` → `pool.unique_codes`）；横截面上不足 680 的日子按 mask 屏蔽 | 与 WORK-500 基准一致（680×213 面板） |
| **M3** | **模型宽度 `d`** | **128**（`nn.Linear(K, 128)` 投影 + `LayerNorm`） | 与 MLP 的 hidden 128 对齐，便于同口径比较 |
| **M4** | **层数 / 头数** | **2 层 encoder，每层 4 头**（`nn.MultiheadAttention(d=128, nhead=4)`，FFN = 512，GELU，LayerNorm 前置，残差连接） | 与 WORK-500 已交付 `work500_bench.py` 的 `CrossSectionTransformer` **逐行一致** |
| **M5** | **位置编码** | **无位置编码**（对股票置换等变） | 横截面模型的正确归纳偏置；与 WORK-500 基准一致 |
| **M6** | **dropout** | **0.1**（投影后 + 每层两处），GELU 激活 | 与 MLP（dropout 0.1 / GELU）对齐 |

补充写死项：

| # | 项 | 规定值 |
|---|---|---|
| **M7** | 参数量 | 由 `d=128 / 2 层 / 4 头 / FFN 512 / K=12` 决定，**实现后把实测值写进训练日志**（不作为判据） |
| **M8** | 输出 | 每只股票一个标量（`nn.Linear(128, 1)`），即**次日收益预测** `r_{t+1}` |
| **M9** | **无效股票的处理** | 该臂在日 t 无效的股票（因子无效 / `sample_ok` 为假）**不参与损失**；实现上用 `key_padding_mask` 把它们从 **key** 侧屏蔽（有效 query 不去注意它们），其 query 输出直接丢弃。**每个训练日至少要有 1 只有效股票**，否则该日整日跳过（断言并计数） |

### 1.2 训练循环（写死）

| 项 | 规定值 | 与 WORK-449（MLP）的关系 |
|---|---|---|
| **样本单位** | **一个交易日**（横截面），而不是一只股票 | 变化（Transformer 的输入单位） |
| **样本集合** | 日 t 的有效股票 = `all_valid[t] & m_arm[t] & m_arm[t+1]`（**双向掩码检查**，与 MLP 的 `sample_ok` 逐格相同） | **完全相同**（同一份 `sample_ok`） |
| **batch** | **12 个交易日**（= 8160 stock-day ≈ MLP 的 8192 stock-day） | 与 WORK-500 基准一致 |
| **epoch** | 训练段（2015-2020）全部交易日过一遍（1462 日 → 122 step） | MLP 是 51 step；**两组 epoch 不是同一单位，禁止互相替代或相加** |
| **优化器** | AdamW，lr **5e-4**，weight decay **1e-5**，grad clip **1.0** | **同**（v1.3 修正值） |
| **epoch 数** | 最多 **60**，按 2021 验证段逐日 RankIC 选最优 checkpoint | **同** |
| **损失** | **AdjMSE，γ = 0.1**，在 batch 内所有有效 (日, 股) 上按权重求均值 | **同**（MLP 在 stock-day 上，Transformer 在 (日,股) 上，权重定义一致） |
| **标准化** | 因子用**训练段**的均值/标准差标准化（mu/sd 与 MLP 用**同一段代码、同一份 `sample_ok`** 算出，逐位相同） | **完全相同** |
| **种子** | **42 与 43**（numpy / torch / python 全设） | 42 同 WORK-449 R2 |
| **三臂一致性** | 三臂用**同一套超参数、同一份标准化参数、同一批种子**，唯一差异是 `M_factor` | **同** |

### 1.3 预测产物格式（**写死：与 WORK-449 逐字段一致**）

`pred_<arm>_<seed>.npz` 必须包含且只包含下列字段，**dtype 与轴序照抄** WORK-449：

| 字段 | 形状 | dtype | 语义 |
|---|---|---|---|
| `pred` | `[T, N]` | float32 | `pred[t, i]` = 用 t 日因子对 `r_{t+1}` 的预测（**存放在 t，不是 t+1**）；无效格为 NaN |
| `sample_ok` | `[T, N]` | bool | 该臂的有效样本格（最后一列维度用 `sample_full` 补齐） |
| `dates` | `[T]` | datetime64[D] | 面板日历 |
| `codes` | `[N]` | unicode | 股票代码 |

> **为什么写死**：回测脚本 `scripts/work449_backtest.py` 与主检验 `scripts/work449_primary_test.py`
> **一行不改**即可吃本单的预测。⇒ **回测口径与 WORK-449 逐项一致**这条不是承诺，是结构保证。

### 1.4 与 MLP 的对照口径（写死）

- 本单**必须**同时报出「预测截面离散度 ÷ 标签截面离散度」与「测试段表观 IC」，
  与 WORK-449 §11.1 的 **0.58% / 0.0077** 直接对照，**用来回答「模型是不是把信号压掉了」**。
- 「预测截面离散度比」定义（写死）：逐日算 `std(pred_t[valid]) / std(label_t[valid])`，
  再对测试段取**中位数**；标签为 `r_{t+1}`。

---

## 2. §9.4 判定词（**逐字沿用 WORK-449**）

> - **复现成立**：`ΔSharpe < 0` 且 95% CI 不含 0 → 判词「**进**」，并写明 `ΔIC` 是否 > 0
>   （背离是否完整复现）。
> - **未复现**：`ΔSharpe ≥ 0` 或 CI 含 0 → 判词「**退**」，写清卡在哪一条（差多少、CI 覆盖范围），
>   以及**翻案需要什么**（换池 / 换窗口 / 换因子集 / 换成本口径中的哪一个）。

主检验量：`ΔSharpe = Sharpe(no_mask) − Sharpe(full_mask)`；`ΔIC` 同步报。
成本 8 bps 为主口径，5 bps 为敏感性。bootstrap 1000 次 / block 20 / seed 42 / 配对同步重采样。

### 2.1 §9.5 两 seed 合并口径（**本单新增，写死**）

| 情形（两 seed 的 ΔSharpe） | 判词 |
|---|---|
| 两 seed **同号**且合并口径满足 §9.4 | 按 §9.4 给「进 / 退」，并注明两 seed 一致 |
| 两 seed **同号**但合并口径不满足 §9.4 | 按 §9.4 给「进 / 退」，并注明两 seed 一致 |
| 两 seed **异号** | **判词必须写成「未复现（退）· 结论不稳定」**：主检验量的符号不由数据决定 ⇒ **功效不足**；同时**必须**回答「换模型后判词是否翻面」——若 WORK-449 的两 seed 也异号，则**模型容量不是符号不确定性的来源**；若 WORK-449 两 seed 同号而本单异号，则**换模型引入/放大了不确定性**，这是一条**新的发现** |

「合并口径」定义（写死）：两 seed 的逐日净收益序列先各自按 §8 口径算 Sharpe，再取
**两 seed 的算术平均**；bootstrap 对两 seed 的配对逐日净收益**同步重采样**（同一组块起点同时作用
于两 seed 与两臂），1000 次 / block 20 / seed 42，报告 95% 百分位 CI。

---

## 3. §10 退出门槛（**逐字沿用 WORK-449 七条**，并加两条本单专有）

| # | 门槛 | 判据 | 本单实测 |
|---|---|---|---|
| 1 | 预注册先行 | 预注册 commit 早于任何回测产物 commit；提交后未改判定规则/阈值/样本窗/成本/组合约束 | 见 `git log` |
| 2 | **快照完整** | manifest 逐股 `ok/error/rows/sha256`；**本单追加**：落地文件与冻结清单**逐条 sha256 比对，680/680 通过** | `snapshot_verification.json` |
| 3 | 窗口断言真的在跑 | `no_mask > 0`、`halt_only > 0`、`full_mask == 0` | `panel_stats.json` |
| 4 | 三档指标齐全 | 每档表观 IC / Sharpe / 年化 / MDD / 换手；主检验量 = ΔSharpe；按 §9.4 判词 | `metrics_<arm>_<seed>.json` |
| 5 | 独立复算一致 | 独立复算脚本**不 import 建模阶段任何函数**，从快照 CSV 重算 IC / Sharpe / 年化 / MDD / 换手，逐项相对差 ≤ 1e-6；不一致项列出并**以复算值为准** | `audit_findings.json` |
| 6 | 求解器与训练可复现 | QP 与 SLSQP 在 5 个抽样日相对差 ≤1e-6；**同 seed 重跑两次指标一致（相对差 ≤1e-6）** | `rerun_same_seed.json` |
| 7 | 结论可判 | 明确判词（进 / 退）+ 三条依据 + 「本结论不构成投资建议」 | 报告首屏 |
| **8（本单）** | **两 seed 齐跑** | 三档 × 2 seed = 6 次训练 + 6 次回测全部落盘，各带设备/线程/s-per-epoch/峰值显存/seed/快照校验结果 | `train_log_*` |
| **9（本单）** | **可比性自检** | 快照 680/680 sha256 通过；本单 `panel_stats.json` 的 `counts` 与 WORK-449 `panel_stats.json` 逐项相等 | 见下 |

**直接判「退」并停手的条件**（沿用 WORK-449）：数据源端点 403/40203 或快照无法覆盖测试期；
预注册落盘后想改口径（停下回报，不私下改）；连续 2 轮无新增交付物或同一命令反复失败；
需要付费终端 / Level-2 逐笔 / 论文之外的数据。

**本单追加停手条件**（派单卡给死）：峰值显存 > 8 GB 或 OOM；环境复测（C 组 s/epoch）
超 0.13 s ±20% 区间；同一命令连续失败 2 次。

---

## 4. §11 复现命令（**固定顺序**，写死）

```powershell
# 0) 快照落地与逐股 sha256 校验（680/680）
& 'F:\001_install\020_Anaconda\envs\ds\python.exe' scripts\work505_verify_snapshot.py `
    --exp WORK-505 --data-dir 'F:\quant_work505\data\WORK-449'

# 1) 面板与三档掩码（口径同 WORK-449）
& 'F:\001_install\020_Anaconda\envs\ds\python.exe' scripts\work449_build_panel.py --exp WORK-505
& 'F:\001_install\020_Anaconda\envs\ds\python.exe' scripts\work449_build_factors.py --exp WORK-505

# 2) 三档掩码 × 2 seed = 6 次训练（GPU）
& 'F:\001_install\020_Anaconda\envs\ds\python.exe' scripts\work505_train_transformer.py `
    --exp WORK-505 --arm {no_mask|halt_only|full_mask} --seed {42|43}

# 3) 6 次回测（脚本与 WORK-449 同一份，不改）
& 'F:\001_install\020_Anaconda\envs\ds\python.exe' scripts\work449_backtest.py `
    --exp WORK-505 --arm {no_mask|halt_only|full_mask} --seed {42|43}

# 4) 主检验（两 seed 合并口径）
& 'F:\001_install\020_Anaconda\envs\ds\python.exe' scripts\work505_primary_test.py --exp WORK-505

# 5) 独立复算（不 import 建模阶段任何函数）
& 'F:\001_install\020_Anaconda\envs\ds\python.exe' audit\recompute_work505.py --exp WORK-505
```

> 脚本名后缀 `_transformer` / `_505` 表示**本单新增**；`work449_build_panel.py` /
> `work449_build_factors.py` / `work449_backtest.py` 为**复用不改**（与 WORK-449 同一份）。

---

## 5. §12 边界（**逐字沿用 WORK-449**）

仍不得出现交易指令、仓位建议或收益承诺；不接真实资金；不接入付费 / Level-2 数据；
不改 `data_contracts/` 既有规则；股票池维持 `000300.SH` 生效日名单。

**本报告为内部研究模拟，撰写方非持牌机构，不构成投资建议与收益承诺。**

---

## 6. 本件与 WORK-449 预注册链的关系（可核验）

```
WORK-449 v1.0 (522ecf9) → v2.0 澄清 (dfa7372) → v1.1+v1.2 (e434508)
    → v1.3 训练协议 (ca86db9) → v1.4 求解器口径 (04e9abf) → 产物 (7f6b51e)
                                              │
                                              ▼
                     WORK-505 预注册 v2（本文件）：仅替换 §7 模型一节
                     + 新增 §0 可比性 / §2.1 两 seed 合并口径 / §3 门槛 8-9
```

**本件不改**：假设 H1、股票池、样本窗与切分、掩码三档、因子集与其口径、损失、标准化、
组合约束、成本、bootstrap 次数/块长/种子、判定词（进/退）的定义与阈值。
