# WORK-503 · 改动清单 vs 上游 commit sha 记账

> 派单卡第 2 段要求：「复用官方实现……改动只限『数据加载 + 先验因子替换 + 掩码 + 成本』，
> **每一处改动列清单并对照上游 commit sha 记账**，保持将来能跟上游同步。」
> 本文件就是那份记账。**上游仓库一行未改**——本单的适配全部落在本单自己的脚本层。

## 一、上游基线

| 项 | 值 |
|---|---|
| 论文 | PRISM-VQ, arXiv:2605.13407v1 |
| 官方仓库 | `finxlab/PRISM-VQ`（MIT） |
| 上游 commit | **`7d02635d0cdeec2f4e7278a9e73acd0eb67a8fa6`** |
| 落地路径 | `vendor/PRISM-VQ/`（本仓库内 vendored，**60 个文件全部入库**） |
| 记账标记 | `vendor/PRISM-VQ/.upstream_commit` 内容即上面的 sha |

**「上游未改」的可核验证据**（不是口头声明）：

```
$ git log --oneline -- vendor/PRISM-VQ/
588cd47 docs(experiment): WORK-503 预注册 v1.1 ……        ← 全仓唯一一次触碰 vendor/ 的 commit，即导入

$ git diff --stat 588cd47 HEAD -- vendor/PRISM-VQ/
（空 —— 自导入起 60 个文件一个字节都没动过）
```

因此**将来跟上游同步时，`vendor/` 可以整目录直接 rebase / replace**，不需要解冲突。

## 二、本单实际调用的上游代码（全部只读）

| 上游文件 | 提供的对象 | 本单用途 |
|---|---|---|
| `trainer/train_vqvae.py` | `FactorVQVAE` | stage-1 VQ-VAE（码本）训练 |
| `trainer/train_ypred.py` | `GenerateReturn` | stage-2 完整模型（码本 + MoE + 先验） |
| `trainer/train_ypred_wo_moe.py` | `GenerateReturn` | stage-2 `no_moe` 档 |
| `trainer/train_ypred_wo_prior.py` | `GenerateReturn` | stage-2 `no_prior` 档 |
| `trainer/train_ypred_wo_stage1.py` | `GenerateReturn` | stage-2 `no_vq` 档（去码本，无 stage-1） |
| `utils/test.py` | `run_inference` | 测试段推理 + 逐日 RankIC |
| `utils/metric.py` | `RankIC` / `Cal_IC_IR` | 指标公式（见 §四 的口径差异） |
| `utils/__init__.py` | `seed_everything` | 播种 |

调用点全部集中在 `scripts/work503_run_arms.py`（`work503_run_arms.py:240,280,307-313,338`），
**没有一处 monkey-patch 上游类**。

## 三、改动清单：四类允许的改动 → 本单实现

| 卡片允许的改动类别 | 上游原做法 | 本单实现（自己的脚本层） | 为什么必须换 |
|---|---|---|---|
| **数据加载** | 官方 `scripts/data_collector.py` 走 Qlib 官方 `cn_data`（只到 2020-09-25，且无 `$vwap`） | `scripts/work503_fetch*.py` → `work503_build_panel.py` → `work503_build_dataset.py` → `work503_dump_qlib.py`：PIT 名单（baostock 历史成分）、新浪日频（后复权 + 不复权）、财务先落快照，再写成本单自己的 Qlib 二进制；**特征/标签/切片由 `work503_run_arms.py` 的 `Work503Dataset` 自建**（T=20、158 特征 + 6 先验 + 10 标签，`ffill+bfill`，标签做截面 rank 归一） | 官方数据底座覆盖不到测试段 2022-01-04~2024-12-31 |
| **先验因子替换** | 13 个 JKP 全球因子 | 自建 6 个 A 股先验（20 日市场收益、流通市值对数、账面市值比、12-1 动量、60 日波动、5 日反转），**全部滞后一个交易日**；`no_prior` 档整档置零 | JKP 13 因子在 A 股无逐股对应值（`data_probe.json` 的 `not_obtained` 已记） |
| **掩码** | 无独立实现 | `experiments/WORK-503/mask_contract.md`（口径）+ `scripts/work503_backtest.py` 的掩码段（涨停不可买 / 停牌延后到下一可交易日 / 退市清算）；**断言是活跑的**，故意违规开关 `WORK503_ASSERT_VIOLATION` 的触发原文见 `assertion_violation.txt` 与 `assertion_violation_weight.txt` | 卡片要求掩码断言必须真的在跑 |
| **成本** | **上游仓库不含组合构造 / 回测代码** | `scripts/work503_backtest.py`：份额级 TopK-DropN（K=30 / N=5、只做多、等权、单票上限写死、买入次日开盘 / 卖出当日收盘），成本买 5 bps + 卖 15 bps，另跑单边 8 bps 与双边 30 bps 两档敏感性 | 卡片明确要求 TopK-DropN + 只做多 + 两档成本敏感性 |

**卡片没列、但本单必须自己做的三件事**（一并记账，避免以后被当成「悄悄扩了范围」）：

| 事项 | 本单实现 | 理由 |
|---|---|---|
| **种子** | `work503_run_arms.py`：`seed_everything(seed)` + `pl.seed_everything(seed, workers=True)`（`workers=True` 是关键，否则 DataLoader 的 worker 不继承种子）；每个种子各训一份 stage-1 并在该种子的三档间共享 | 上游 `stage1.py` 把 seed 写死成 42，卡片要求 ≥2 个种子各跑一次 |
| **判定 / 独立复算 / 报告** | `work503_verdict.py` / `audit/recompute_work503.py` / `work503_report.py` | 上游无判词与复算逻辑；审计脚本**不 import 建模阶段任何函数**（`ast` 自检 `modeling_imports_found = 0`） |
| **GRU 基线** | `work503_baseline.py`：用上游 Qlib 自带的 `qlib.contrib.model.pytorch_gru_ts.GRU`，只换训练循环与取样层 | 卡片要求「最便宜的口径校验」先跑通线性/GRU 基线 |

本单自有层共 **17 个 `scripts/work503_*.py`**，全部入库。

## 四、上游与本单的口径差异（并列披露，不掩盖）

| 项 | 上游 | 本单 | 影响 |
|---|---|---|---|
| AR 年化 | `utils/metric.py:61`：`daily_log_returns.mean() * 252`（对数口径） | `exp(mean(log1p(r)) × 252) − 1`（复利口径） | 两者在年化收益为负时不等价；本单用复利口径，**报告里两个口径都标注** |
| 换手 | `utils/metric.py:75`：`series['turnover'].mean()`，**未定义单边/双边** | 本单预注册口径为**单边**（买+卖 ÷2），报告同时给双边值 0.333 与论文的 0.3259 做「同量级」比较 | 论文的 0.3259 到底是单边还是双边，**无法从上游代码判定**（仓库没有组合构造代码）→ 报告写「未取到」，不猜 |
| SR | `utils/metric.py:63`：`annual_return / annual_std`，与本单等价（都是 √252 缩放） | 同 | 无差异 |
| 长/多空与成本假设 | **上游仓库不含相关代码** | 本单按卡片写死：只做多、无杠杆 | 论文的建仓假设**无法从代码核实**，报告写「未取到」 |

## 五、复现本单所需的上游状态

```
上游 commit : 7d02635d0cdeec2f4e7278a9e73acd0eb67a8fa6
vendor 路径 : vendor/PRISM-VQ/（60 文件，自导入起未改）
本单代码层 : scripts/work503_*.py（17 个）
```

三样都不动，即可复跑本单；`vendor/` 需要升级时按 §二 的调用点清单逐个回归即可。
