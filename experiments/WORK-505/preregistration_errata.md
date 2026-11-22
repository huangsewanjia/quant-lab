# WORK-505 预注册 v2 · 勘误表（v2.1）

> **性质**：**勘误件，不是协议修订**。本文件只记录**操作性错误**（命令写错、脚本不存在、解释器选错、
> 措辞不准确）的更正，**不改动任何判定规则、阈值、样本窗、成本口径、组合约束、掩码三档、因子集、
> bootstrap 设定或判词定义**。
>
> **预注册原件字节未动**：`experiments/WORK-505/preregistration.md`
> 的 sha256 = `e542ccfe06677580ec36a55e739adff18d8c9fed2acbb66abd49f8d3fb49e523`
> （commit `38fe864f3f81ddc8653faf62c8affccb6686c03f`，2026-11-22T14:50:03+08:00）。
> 门槛 1「预注册 commit 早于任何回测产物 commit」不受本勘误影响。
>
> **触发来源**：独立代码审查（子代理，`code-review-and-quality` 流程）对本单变更层出的 7 项 Major /
> 4 项 Minor 发现。其中 4 项会直接让「数据补齐即可直接开跑」这条主张落空，故逐条修，
> 修法记在下表；其余为交付文本的事实性更正，落在报告与失败日志里。

---

## 一、勘误清单

| # | 位置 | 原文 / 问题 | 勘误 | 依据 | 回滚 |
|---|---|---|---|---|---|
| **E1** | §4 步骤 2 | 命令 `--arm {…} --seed {42\|43}` 不带 `--tag`；而脚本按 `--tag` 命名产物 ⇒ 照抄原命令时，seed 43 会**静默覆盖** seed 42 的 `pred_*.npz` / `train_log_*.json` | 训练脚本的 `--tag` **默认值改为 `_<seed>`**（`None` → `f"_{args.seed}"`），因此 §4 步骤 2 的命令**逐字可用**，且产物名与 §0.3 的 `pred_<arm>_<seed>.npz` 一致 | 独立审查实测：原命令下 `pred_no_mask.npz` 被两次运行覆盖，`train_log_no_mask.json` 的 seed 字段变成 43，seed 42 产物不可恢复 | 删掉 `work505_train_transformer.py` 里 `tag = args.tag if args.tag is not None else f"_{args.seed}"` 一行，回到显式传 tag |
| **E2** | §4 步骤 3 | 命令带 `--seed {42\|43}`，但 `scripts/work449_backtest.py`（**复用不改**的 WORK-449 脚本）**只接受 `--tag`，不认 `--seed`** ⇒ 照抄必然 `unrecognized arguments` 报错 | 回测命令改为 `--tag _42` / `--tag _43`（产物名 `metrics_<arm>_<seed>.json` / `weights_<arm>_<seed>.npz`，与 §0.3 一致）。**不改** `work449_backtest.py` | `work449_backtest.py:187-198` 的 argparse 只有 `--exp/--arm/--solver-check-days/--tag` | 无需回滚（WORK-449 脚本未被改动） |
| **E3** | §4 步骤 4 / 5 | 原件已写明 `scripts\work505_primary_test.py` 与 `audit\recompute_work505.py`，但**两个脚本当时都不存在** ⇒ 「完整链条」不成立 | 两个脚本**已实现并入库**：<br>· `scripts/work505_primary_test.py`：逐 seed 的 ΔSharpe + 配对 block bootstrap（1000/block 20/seed 42）+ §2.1 两 seed 合并口径（同一组块起点同步作用于两 seed 与两臂）+ §2.1 判词表（含「两 seed 异号 ⇒ 未复现（退）· 结论不稳定」）+ 与 WORK-449 的符号对照<br>· `audit/recompute_work505.py`：由 WORK-449 已过审的 `audit/recompute_work449.py` **派生**（独立重建代码逐字保留，只加 `--data-dir` / `--seeds` 参数与 (arm, seed) 循环），文件头列明全部改动 | 独立审查实测：预注册 §2.1 的合并口径此前**没有任何代码实现** | 删除两个新脚本即可，预注册原文不变 |
| **E4** | §4 各命令的解释器 | 原文用 `F:\001_install\020_Anaconda\envs\ds\python.exe`；该环境的 numpy(MKL) 与 pip torch 自带另一份 OpenMP 运行时，**WORK-449 全部脚本都会崩**（`OMP: Error #15`） | 正式跑数改用 `F:\quant_work505\venv\Scripts\python.exe`（`--system-site-packages` venv + pip OpenBLAS numpy）。**不改用户已有环境** | 失败日志 §3.2 的实测报错原文 | 删除该 venv 目录即可回到原环境；本单未改动用户环境 |
| **E5** | §1.1 M4 | 「与 WORK-500 已交付 `work500_bench.py` 的 `CrossSectionTransformer` **逐行一致**」——实测**不是逐行一致**（WORK-500 用自定义 `do(p)` + `ctx` 记账，本单用 `nn.Dropout`） | 改为「**结构等价**」：投影 / LayerNorm / 多头注意力 / FFN / 残差逐层对应，**参数量逐位相符**（K=213 → 424321；K=12 → 398593 = 424321 − (213−12)×128） | 独立审查逐层 diff `attachments/work500_bench.py` 与本单实现 | 改回「逐行一致」字样（但该说法不成立，不建议） |
| **E6** | §1.3 预测产物格式 | 原件只规定字段/形状/dtype；未规定**有限性集合**必须与 `sample_ok` 逐格相同 | 补强：训练脚本写盘前**断言** `isfinite(pred) == sample_ok`（逐格），不成立即报错退出；预测网格覆盖**所有**含有效样本的交易日（含训练/验证/测试窗口之外的 2014 段），与 WORK-449 的 MLP 语义一致 | 独立审查实测：原实现只在 tr/va/te 三个窗口内预测 ⇒ 2014 段 1,558 格 `sample_ok=True` 而 `pred=NaN`，`isfinite(pred)==sample_ok` 对 MLP 成立、对 Transformer 不成立 | 删除 `pred_grid_matches_sample_ok` 断言与 `pred_days` 全网格改动即回到旧行为 |
| **E7** | §1.1 M9 | 「该臂在日 t 无效的股票不参与损失；… 每个训练日至少要有 1 只有效股票，否则该日整日跳过（断言并计数）」——实现里跳过的天数**只跳过、不计数** | 补强：① 训练日志新增 `n_train_days_skipped` / `n_val_days_skipped` / `n_test_days_skipped`；② **标签为 NaN 的样本格不再被当作 0.0 参与损失**，而是剔除并计入 `n_label_nan_cells` | 独立审查注入实验：MLP 遇 NaN 标签整批 loss=nan（跑挂、17400 格预测全为非有限数），本单原实现却把 NaN 当 0.0 静默训练（loss 曲线正常、预测全有限）——同一份数据下 MLP 暴露、Transformer 伪装，是「结论可信度」层面的差异 | 把损失里的 `sel_mask = vb & label_finite` 改回 `sel_mask = vb` 即回到旧行为 |

---

## 二、本勘误**没有**触碰的东西（逐项确认）

- 判定规则（进 / 退）与阈值：§2、§9.4 —— **一字未改**。
- 样本窗与切分（2015-2020 训练 / 2021 验证 / 2022-01-04~2024-12-31 样本外）：**一字未改**。
- 成本口径（8 bps 双边 + 5 bps 敏感性）、rf 1.5%、年化 365.25 自然日、波动 √252、MDD 收盘 cummax：
  **一字未改**。
- 组合约束（只做多、单票 ≤3%、α=10、Ledoit-Wolf 120 日、PSD 下限 1e-10、warm start）：**一字未改**。
- 掩码三档定义、12 因子集与其口径、损失 AdjMSE γ=0.1、优化器与轮数、选 epoch 方式：**一字未改**。
- bootstrap 设定（1000 次 / block 20 / seed 42 / 配对同步重采样 / 95% 百分位 CI）：**一字未改**。
- §2.1 两 seed 合并口径的定义与三行判词表：**一字未改**（本次只是把它**实现**出来）。
- §10 十条退出门槛：**一字未改**。

---

## 三、勘误件的效力

| 项 | 说明 |
|---|---|
| 冻结性 | 预注册原件 `experiments/WORK-505/preregistration.md` **字节未动**，sha256 见文首；本勘误件是**新增文件**，不改原件 |
| 时间顺序 | 预注册 commit `38fe864`（2026-11-22T14:50:03+08:00）仍**早于**本单任何产物 commit（产物 commit 的 sha 见 registry 行与本单 issue 评论） |
| 门槛 1 复核 | 门槛 1 的判据是「提交后未改判定规则/阈值/样本窗/成本/组合约束」——本勘误件**没有**改这六类中的任何一项 |
| 复核方法 | `git show 38fe864:experiments/WORK-505/preregistration.md \| sha256sum` 应等于文首的 `e542ccfe…` |

---

**本报告为内部研究模拟，撰写方非持牌机构，不构成投资建议与收益承诺。**
