# WORK-557 预注册（Pre-registration）

> 单号：WORK-557 · 论文：PRISM-VQ, arXiv:2605.13407v1（IJCAI-ECAI 2026）
> 官方实现：`finxlab/PRISM-VQ`，上游 commit `7d02635d0cdeec2f4e7278a9e73acd0eb67a8fa6`（MIT）
> 基线协议：`experiments/WORK-503/preregistration.md`（v1.0）+ `preregistration_v1.1.md`（算力参数）
> \+ `preregistration_v1.2.md`（数据构造层缺陷 A–E）+ `preregistration_v1.3/v1.4.md`（H1 符号口径）
> 冻结日期：2026-11-24 · 版本：**v1.0**（WORK-557 的第一版，也是唯一一版）
> 状态：**本文件先于任何本单训练/回测产物落盘并 commit**；跑完不改本协议。

---

## 0. 一句话

把 WORK-503 的「无 GPU → 训练段压到 2015–2019、每阶段 ≤6 轮」这个**算力妥协撤掉**，
在带 RTX 3060 的机器上按论文口径**全窗重跑**：训练 2009–2019、每阶段 ≤50 轮、四档 × 2 seed。

**判定规则、股票池、特征、标签、成本、组合规则、掩码、种子、指标定义一律照抄 WORK-503，
一字不改。** 本单只动「算力参数」与「数据底座」两组东西，逐条列在 §1。

---

## 1. 本单相对 WORK-503（v1.4 生效版）的全部改动

| # | 项 | WORK-503（生效值） | WORK-557（本单） | 依据 |
|---|---|---|---|---|
| **C1** | 训练段 | 2015-01-01 ~ 2019-12-31（~1220 交易日） | **2009-01-01 ~ 2019-12-31** | 派单卡明令；上游 `configs/config.yaml:39` 的 `data.train_period` 本身就是这个区间（论文同） |
| **C2** | 训练轮数上限 | stage1 ≤ 6、stage2 ≤ 6 | **stage1 ≤ 50、stage2 ≤ 50** | 派单卡「每阶段最多 50 轮」 |
| **C3** | 早停 patience | 3 | **15** | WORK-503 v1.1 §1 把 patience 从**上游 15 收到 3**，理由写得很清楚：「轮数缩短后 patience 相应收紧，避免早停形同虚设」。本单撤销这项压缩 → 回到上游 `configs/config.yaml:88` 的原值 15。`min_delta` 保持 1e-5、`monitor=val_loss`、`mode=min` 不变 |
| **C4** | 设备 | CPU 8 核，无 GPU | **GPU：RTX 3060 12 GB**（实测 CUDA 可用显存 ~11.0 GB） | 派单卡「在带 3060 的机器上…GPU」 |
| **C5** | 数据底座 | 新浪财经日频 + baostock（起点 2012-01-04） | **`Z:\quantdb`**（quantdb 底座，起点 2006-01-04） | 派单卡「数据：`Z:\quantdb`（只读）」 |
| **C6** | PIT 成分来源 | `baostock query_hs300_stocks` 月末采样（145 个生效名单） | **`index_weight` 月度快照**（index_code=000300.SH），按生效日向前沿用 | 派单卡「CSI300 point-in-time 成分（`index_weight` 按生效日）」 |
| **C7** | 每股净资产生效日 | 报告期 + 120 天（新浪快照没有 ann_date，只能近似） | **`ann_date`（公告日即公开日）** | quantdb README §6 规矩 2「财务按 ann_date，否则回测吃未来信息」。这是**口径升级**：只用已公开信息，且比 WORK-503 更早可用；仍在报告 §偏离表单列 |
| **C8** | 流通股本（先验 P2） | 新浪 `outstanding_share` | **`daily_basic.float_share`**（万股 → 股） | C5 的连带项 |
| **C9** | qlib 运行时 | 上游轮子原生 | 同上 + **纯 python 替身**（见 §5） | pyqlib 0.9.7 无 cp313 轮子 |

**未改动（照抄，逐条核对过）**：

| 项 | 来源 | 本单处理 |
|---|---|---|
| 验证段 2020-01-01 ~ 2021-12-31、测试段 2022-01-01 ~ 2024-12-31 | WORK-503 v1.0 §1 | 照抄，一字不改 |
| 切分方式（按时间顺序）、超参只在验证段选、测试段只跑一次 | 同上 | 照抄 |
| 特征回看 T = 20（允许跨切分边界取历史） | 同上 §1 | 照抄 |
| 股票池 = PIT 的 CSI300 成分；禁止用当前名单回填历史、禁止用退市名单反推历史 | 同上 §2 | 照抄（成分来源换了，规则不变） |
| 单票等权、不做市值加权 | 同上 | 照抄 |
| 特征 = Alpha158（158 个）+ RobustZScoreNorm(clip_outlier) → Fillna | 同上 §3 | 照抄 |
| 预处理拟合窗口 = **训练段**（本单即 2009-01-01 ~ 2019-12-31） | 同上 §3 + WORK-503 v1.2 §E | 随 C1 变（拟合窗口跟着训练段走） |
| 输入数据 = 后复权价 + volume + `vwap = amount/volume` | 同上 §3 | 照抄（后复权定义见 §2） |
| 6 个自建 A 股先验（P1~P6），**全部滞后一个交易日** | 同上 §4.1 | 照抄 |
| `no_prior` 档 = 先验整档置零，维度保持 6 | 同上 §4.2 | 照抄 |
| 标签 = `open(t+1) → close(t+k)`，k=1..10；训练标签 `CSRankNorm`；`target_day=5` | 同上 §5.1 | 照抄 |
| 成本档：主 买 5 bps / 卖 15 bps；敏感 1 单边 8 bps；敏感 2 双边 30 bps；另跑 0 bps 毛值诊断 | 同上 §5.2 | 照抄（三档全跑全报；派单卡点名的 8 bps 与 30 bps 单列成表） |
| 组合 = TopK-DropN，K=30、N=5，只做多，等权，每日调仓；t 日收盘卖 / t+1 日开盘买 | 同上 §6 | 照抄 |
| 掩码契约（停牌 / 涨跌停 / 退市 / 新股满 20 日 / 前收盘口径） | `mask_contract.md` | 照抄，一字不改 |
| 种子 {42, 2024}，四档 × 2 seed | 同上 §8 | 照抄 |
| H1 = `Δ_drop = (RankIC_no_vq − RankIC_full) / RankIC_full`，中位数；进 [−40%,−15%]、退 [−10%,+10%]、其余部分复现 | WORK-503 v1.4 §3 | 照抄（**符号口径用 v1.4 的 Δ_drop**，不是 v1.0 的字面式） |
| H2 = vs 固定基线 GRU 的 RankIC 差，10000 次 block bootstrap（block=5 日）95% CI | WORK-503 v1.0 §9 | 照抄 |
| H3 = 净 SR / AR / MDD / 日均换手；净 SR < 0 直接判退 | 同上 | 照抄 |
| 失败判据 1–5 | 同上 | 照抄 |
| 指标定义（RankIC / RankICIR 不年化 / AR = exp(252·mean(g))−1 / MDD / SR = √252·mean/std） | 同上 §10 | 照抄 |
| 日均**单边**换手（买卖 ÷ 2） | 同上 §5.2 | 照抄 |

---

## 2. 复权口径（照抄 + 落地定义）

WORK-503 v1.0 §3 / §1.1 D4：**后复权用于特征与组合收益，不复权用于涨跌停判定**。本单照抄。

后复权价的**具体公式**随数据源而变，本单逐字复刻 quantdb 视图 `v_daily_hfq`
（定义见 `Z:\quantdb\code\quant-lab\db\schema.sql`）：

```
hfq_px = ROUND(raw_px * adj_factor / MIN(adj_factor) OVER (PARTITION BY ts_code), 4)
```

`MIN(adj_factor)` 取该股**全历史**最小值（与视图定义一致，不按窗口截断）。
不复权价用于：涨跌停判定（掩码契约 §3）、开盘买入 / 收盘卖出的成交价。

单位换算（quantdb README §6 规矩 4/5 → WORK-503 的新浪口径）：
`vol` 手→股 ×100；`amount` 千元→元 ×1000；`float_share` 万股→股 ×10000。
换算后 `amount / volume` 才是「每股价格（元）」，qlib 二进制的 `vwap` 依赖这一点。

---

## 3. 成本档的说明（派单卡 vs 预注册）

派单卡写「主 8 bps + 敏感 30 bps」；WORK-503 预注册 §5.2 的**主口径是买 5 bps / 卖 15 bps**，
8 bps（单边）与 30 bps（双边）是两档敏感性。

本单处理：**三档全跑全报**（0 bps 毛值诊断 + 5/15 bps 主口径 + 单边 8 bps + 双边 30 bps），
主判定按 WORK-503 预注册的主口径（5/15 bps）执行；派单卡点名的 8 bps 与 30 bps
在报告里单列成表。理由：改判据的不是本单的权限，而多跑一档成本不增加任何自由度。

---

## 4. 停手条件（照抄派单卡）

① 库里 2009–2013 数据缺口 > 5%；② GPU 显存不足（> 9 GB 峰值）且降 batch 后仍 OOM；
③ 单档训练 > 2 h；④ 官方 PRISM-VQ 实现需要改结构才跑得动（先停，给 diff）；
⑤ 同一命令连续失败 2 次。

**排队**：同一台 3060 同时只跑一个训练进程；开工前 `nvidia-smi` 看显存占用。

---

## 5. qlib 运行时替身（技术性偏离，必须披露）

本机训练环境是 Python 3.13.15（`ds` 环境，torch 2.11.0+cu128）。**pyqlib 0.9.7 只发布
cp38 / cp39 / cp310 / cp311 / cp312 轮子，没有 cp313**。轮子里只有两个编译产物
（`qlib/data/_libs/rolling*.pyd`、`expanding*.pyd`），提供 6 个函数
（`rolling_slope / rolling_rsquare / rolling_resi` 与对应的 `expanding_*`），
Alpha158 的 158 个表达式里 `Slope` / `Rsquare` / `Resi` 三个算子都要用。

本单处理：把这 6 个函数按**上游 .pyx 源码逐行转写**成纯 numpy 实现（.pyx/.cpp 就在同一个
轮子里：`qlib/data/_libs/rolling.pyx`、`expanding.pyx`），放在本机 `.venv/qlib312/` 下，
**上游代码一行未改**（`vendor/PRISM-VQ/` 保持只读）。

**验证**：在 Python 3.9 环境里装真的 cp39 编译轮子，对 6 组输入 × 5 个窗口 × 6 个函数
逐元素对拍，**max |diff| = 0.000e+00**（完全一致，非「近似」）。
证据：`experiments/WORK-557/shim_validation.json`；脚本 `experiments/WORK-557/work557_shim_validate.py`。

---

## 6. 复现命令（本协议的一部分）

```
# 环境（两个解释器）
python experiments/WORK-557/setup_envs.py                       # py3.13 venv + py3.9 venv
python experiments/WORK-557/work557_qlib_shim.py                # qlib 替身
.venv39\Scripts\python experiments/WORK-557/work557_shim_validate.py   # 对拍

# 数据
python scripts/work557_fetch_panel.py                           # quantdb -> WORK-503 布局
python scripts/work503_build_panel.py --exp WORK-557            # 面板（照抄）
python scripts/work503_dump_qlib.py  --exp WORK-557            # qlib 二进制（照抄）
python scripts/work557_build_dataset.py --exp WORK-557          # Alpha158 特征 + 标签 + 先验

# 训练（每阶段 ≤50 轮，GPU）
.venv\Scripts\python scripts/work557_run_arms.py --arm full --seed 42 --epochs 50 --exp WORK-557
... (full / no_vq / no_moe / no_prior) x (42 / 2024)
.venv\Scripts\python scripts/work557_baseline.py --kind gru --seed 42 --epochs 50 --exp WORK-557

# 回测 / 判定 / 复算 / 报告
.venv\Scripts\python scripts/work557_backtest.py --exp WORK-557
.venv\Scripts\python scripts/work557_verdict.py  --exp WORK-557
.venv\Scripts\python audit/recompute_work557.py
.venv\Scripts\python scripts/work557_report.py    --exp WORK-557 --preview
```

---

## 7. 生效条件

本版 commit 之后才允许开始训练与回测；跑完不得再改本协议。
若发现新的协议级错误，只能再开 v1.1 并保留 v1.0。
