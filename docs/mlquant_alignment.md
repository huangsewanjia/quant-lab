# ML-Quant 复现规格对齐表

> **复现目标（定死）**：ML-Quant（arXiv:**2507.07107v2**）**Table 11 的真实面板结果**。
> 本单只做**规格对齐**，不训练、不回测、不动 GPU。
>
> **三份证据**：① 论文原文（arXiv:2507.07107v2，PDF 18 页）② 官方实现 `initial-d/ml-quant-trading`
> @ `476f62a9` ③ 我们已有实现 `quant-lab/experiments/WORK-449/` 与本机新库 `/srv/share/quantdb/raw/`。
>
> **配套文件**
> · [`mlquant_factor_fields.md`](./mlquant_factor_fields.md) —— 213 因子逐个字段需求清单
> · [`mlquant_factor_fields.csv`](./mlquant_factor_fields.csv) —— 同上，机器可读
> · [`mlquant_export_spec.md`](./mlquant_export_spec.md) —— 从新库导出 `load_ochlv_csv` 长表的规格
> · [`../scripts/enumerate_mlquant_factors.py`](../scripts/enumerate_mlquant_factors.py) —— 因子字段需求的静态复算脚本

---

## 0. 一句话结论

**论文与官方代码在「因子、模型、组合、成本」四件事上口径一致；不一致集中在**（a）**掩码的第三个条件「上市>1年」官方根本没实现**，
（b）**论文 §3.4 的行业/市值中性化 Eq(2) 在官方管线里没接线**，（c）**论文 §3.5.1 要求的「t+1 也查限价」的双向标签检查官方没实现**，
（d）**GBM 增广没接进管线**。这四项都是**论文有、代码无**，不是我们的误解。
我们新库在**数据侧比官方实现更好**（有交易所真实涨跌停价、可派生真 VWAP、有退市股与停牌表），
但**必须自己补上这四项代码缺口**，否则复现出来的不是论文那个系统。

---

## 1. 证据基线

| 证据 | 版本 / 标识 | 本轮核验方式 |
|---|---|---|
| 论文 | arXiv:2507.07107v2，v2 提交日 2026-05-09，PDF 18 页 | `arxiv.org/pdf/2507.07107v2` 下载后逐页读取，页码按 PDF 物理页 |
| 官方代码 | `initial-d/ml-quant-trading` @ **`476f62a96a124d86b28b089d2d0d06616c7a728d`**（2026-11-22 06:37 UTC，PR #89 merge） | `git rev-parse HEAD` 实测；`git log` 与 GitHub API `commits` 对齐 |
| 官方许可证 | MIT | GitHub API `license.spdx_id = MIT` |
| 已有实现 | `quant-lab` @ `78e5b5e`（WORK-449 环 2 落地） | 本地 checkout |
| 新库 | `/srv/share/quantdb/raw/`，11 个数据集已入库 | parquet schema 实读（见 §5） |

> 论文 PDF 里的页码是**物理页码**（含参考文献与附录）。本单引用的「p.N」都指这个。

### 1.0 本文件的规模

| 项 | 数量 |
|---|---:|
| 主对齐表行数 | **53**（数据 13 / 因子 9 / 标签 3 / 模型 8 / 组合 10 / 成本 3 / 评价 7） |
| 指向代码的 `path:line` 引用 | **72**（交付时用脚本逐条核验：每条引用的行区间内含预期标识串。⚠️ **该核验脚本未随本单入库，第三方无法复跑**——复算入口只覆盖下面 §1.1 表里的两个脚本，不含引用核验） |
| 论文内部冲突条目 | **6**（§2.8，P1–P6） |
| 缺口与风险条目 | **20**（§6，G-01…G-21） |
| 213 因子逐条字段需求 | **213**（见 [`mlquant_factor_fields.md`](./mlquant_factor_fields.md)） |

### 1.1 本单所有数字的复算入口

本文件里的每一个量化数字都由仓库内脚本产出，不手抄：

| 数字来源 | 脚本 | 产物 |
|---|---|---|
| 新库行数、股票池、覆盖率、VWAP 单位校验、限价代理 vs 交易所限价、复权缩放保真、`adj_factor` 分布 | [`../scripts/work516_measure_library.py`](../scripts/work516_measure_library.py) | `experiments/WORK-516/library_measurements.json`（含输入文件 sha256） |
| 213 因子逐个字段需求 | [`../scripts/enumerate_mlquant_factors.py`](../scripts/enumerate_mlquant_factors.py) | `docs/mlquant_factor_fields.csv` |
| MLP / Transformer 参数量 | `scripts/work516_measure_library.py` 的 `model_parameter_counts()`（解析式）；**并已用 torch 直接实例化官方 `MLPRegressor` / `TransformerRegressor` 复核** | 同上 JSON |

复算命令：

```bash
python scripts/work516_measure_library.py \
    --raw /srv/share/quantdb/raw \
    --out experiments/WORK-516/library_measurements.json

python scripts/enumerate_mlquant_factors.py \
    --repo /path/to/ml-quant-trading \
    --out  docs/mlquant_factor_fields.csv
```

**「每一个量化数字都由仓库内脚本产出」是可以当场验的**：本文件 §5.1 / §5.4 与
[导出规格 §7](./mlquant_export_spec.md) 引用的行数，在 JSON 里都标了 `jq` 路径。例如：

```bash
jq '.row_counts.adj_factor.paper_window.rows'  experiments/WORK-516/library_measurements.json  # 9969383
jq '.adj_factor_row_coverage.expected_export_rows' experiments/WORK-516/library_measurements.json  # 10051715
```

脚本重跑后产物应与入库 JSON **除 `generated_at` 外 0 差异**（`generated_at` 是每次运行的时间戳）。

---

## 2. 主对齐表

四列：**① 论文原文（页码/章节）｜② 官方代码（`path:line`）｜③ 我们库 / 现有实现｜④ 结论**。
结论取值：`一致` / `需转换` / `缺口`（谁缺）/ `冲突`（两边不一致）/ `论文未写` / `代码未实现`。

### 2.1 数据

| # | ① 论文原文 | ② 官方代码 `path:line` | ③ 我们库 / 现有实现 | ④ 结论 |
|---|---|---|---|---|
| A1 | §A.1（p.17）Panel 持有 `[T,N]` 的 OHLCV 张量 + 可选 A 股字段 `vwap, amount, limit_up, limit_down, last_close` | `src/mlquant/data/loaders.py:58-125` `load_ochlv_csv`；长表 CSV，首两列必须 `TRADE_DT` + `S_INFO_WINDCODE`（`loaders.py:78-79`） | 新库是 Hive 分区 parquet（`daily/trade_date=YYYYMMDD/part.parquet`），列名 `ts_code`/`trade_date`/`open`/…；WORK-449 用 AKShare 东财 per-stock CSV（`scripts/work449_fetch.py`） | **需转换**：须加一层 parquet → tab 长表导出，列名映射见[导出规格 §3](./mlquant_export_spec.md) |
| A2 | §A.1（p.17）把 `vwap` 列在 **optional** 一侧 | `src/mlquant/data/panel.py:35` `_REQUIRED_FIELDS = ("open","high","low","close","volume","vwap")`；`loaders.py:95-98` 缺 `vwap` 直接 `KeyError` | 新库有 `daily.amount`（千元）与 `daily.vol`（手），可派生**真 VWAP** | **冲突（论文↔代码）**：代码里 `vwap` 是**必需**字段，论文 §A.1 写错。我们必须给真 VWAP，不能用 `(H+L+C)/4` 代理——那正是 `data/akshare_loader.py:146` 的做法，会让 58 个因子系统性失真 |
| A3 | §3.2 Eq(1)（p.3）`M_{t,i} = S_{t,i} ∧ (¬L_{t,i}) ∧ IPO_{t,i}`，`IPO` = **listed > 1 year** | **官方 `src/` 全仓无实现**：`grep -rn "ipo\|list_date\|listed" src/` 无命中；`features/bias.py:95-130` 只管限价；loader 的 mask 只是 `~close.isna()`（`loaders.py:113`） | WORK-449 已实现 `listed_1y`（`scripts/work449_build_panel.py:170`）；新库 `stock_basic.list_date` 直接可用 | **缺口（代码）**：论文有、官方代码没有。我们能补，且有数据源 |
| A4 | §3.2 Eq(1) 的 `S` = not suspended | 无显式停牌表；靠「无行 → `close` 为 NaN → mask=False」隐式表达（`loaders.py:113`、`akshare_loader.py:150-157`） | 新库有 `suspend_d`（2015–2024 共 **330,217** 行）；实测 `daily` 里 **vol=0 的行 = 0**（抽样 12 日 45,255 行），即停牌日**根本不在 daily 里** | **一致（效果等价）**：`daily` 缺行 ⇒ mask=False ⇒ 不可交易，与停牌处理相同。但须用 `suspend_d` 显式标出，否则无法区分「停牌」与「未上市/已退市」，面板统计与自检做不了 |
| A5 | §3.2（p.3）「限价标志优先用交易所公布限价，容差 ε=1e-3；无则退回 \|r\|>0.098」；§A.2（p.17）两套口径 | `features/bias.py:115-130`：有 `limit_up/limit_down` 时用 `close < up-eps & close > down+eps`，否则 `\|ret\| <= limit_pct`；`eps=1e-3` 默认（`bias.py:99`） | 新库 `stk_limit` 有交易所 `up_limit`/`down_limit`，2015–2024 共 **9,724,911** 行，接 `daily` 覆盖率 **99.02%**（抽样 12 日） | **一致（论文↔代码）**，且**我们数据更好**：能走论文 §A.2 的「regime 1（真实限价）」。实测 9.8% 代理 vs 真实限价在 10 个抽样日上**假阳 1.31%、漏检 0.48%**（§5.3） |
| A6 | §3.2（p.3）只写 ±10% 主板 / ±20% 科创创业板；§A.2 未提板块差异 | `features/bias.py:32-35` 定义 MAIN 0.098 / GROWTH 0.198 / BSE 0.298，`board_limit_pct()` 按代码前缀分板并处理 2020-08-24 创业板改 20%（`bias.py:57-92`）；**但 `configs/paper.yaml:14` 写的是标量 `limit_pct: 0.098`**，`cli/main.py:84-88` 只有 `limit_pct == "board"` 才走分板 | WORK-449 已实现分板 + ST 5%（`work449_build_panel.py:54-82`） | **冲突（代码内部）**：官方有分板能力但默认配置不用。用真实限价则此问题自动消失 |
| A7 | §4.1（p.7）「Daily OHLCV from Tushare」；**全文未写复权方式** | `loaders.py:34-48` 别名表把裸列名排在 Wind 复权列**前面**（`close` → `S_FWDS_ADJCLOSE` → `CLOSE`）；官方无真实数据路径，这条口径实际**没被钉死** | WORK-449 用 **hfq 后复权**（东财 qfq 在低价股上 2 位小数精度会塌陷，`work449_build_panel.py:20-23`）；新库有 `adj_factor`，可自算任意基准 | **需转换 + 口径待定**：论文与官方代码都没写死，必须由我们定死并披露 → [导出规格 §2](./mlquant_export_spec.md) 给出裁定（**后复权 hfq，`price×adj_factor`，不做基准日归一**；理由：hfq 下交易所 1 分钱取整间隔 ≥ 0.00645 > `eps=1e-3`，qfq 不满足） |
| A8 | §4.1（p.7）「average ≈3,200 active stocks after masking」；§A.1 隐含全 A | `configs/paper.yaml:22` `universe: null # null = full A-share`；loader 支持 `universe` 过滤（`loaders.py:62, 83-84`） | WORK-449 用 `000300.SH` 生效日名单 **680** 只（预注册 v1.4 §4）；新库 2015–2024 `daily` 出现 **5,591** 只（其中 **260** 只 `stock_basic` 状态为 D 已退市） | **一致（论文=全 A）**，**我们需从 CSI300 扩到全 A**；新库能覆盖且天然含退市股 |
| A9 | §4.1（p.7）「2015-01 至 2024-12；2015–2020 训练 / 2021 验证 / 2022–2024 测试」 | `cli/main.py:91-96` `_oos_start()` 用 `train_frac`（默认 **0.6**）单一扩展窗切分；`configs/paper.yaml` **未设** `train_frac` | WORK-449 已按三段切（`work449_build_panel.py:49-51`）；新库 2015–2024 共 **2,431** 个交易日，其中 2022–2024 共 **726** 个 | **缺口（代码）**：官方 CLI 切不出论文的三段。**我们已有正确切分**，且 726 与 WORK-449 实测完全一致 |
| A10 | 全文未单独写退市处理 | loader 无退市逻辑；官方仓无 `stock_basic` | 新库 `stock_basic` 有 L(5,580)/D(344)/P(空) 三态 | **一致**：只要股票池用「窗口内 `daily` 出现过的全部代码」，而不是「当前在市代码」 |
| A11 | 全文未写交易日历 | 面板日历 = 文件里出现过的日期 | 新库 `trade_cal`（SSE/SZSE）可作权威日历 | **一致**：面板日历取 `daily` 分区并集；导出时用 `trade_cal` 交叉校验 |
| A12 | §A.3（p.17）原语三契约：zero-on-mask / independence / propagation | `features/tensor_factors.py:49-83` `cs_rank`、`:115-131` `ts_sum/ts_mean`、`:173-191` `ts_corr` | WORK-449 已逐条镜像（`work449_build_factors.py:88-120`），并有对拍脚本 `work449_test_primitives.py` | **一致**，我们已有对拍证据 |
| A13 | §8（p.14）「一条 `make paper CONFIG=configs/paper.yaml` 复现全部合成实验」；`configs/paper.yaml:5-10` 的注释称 `gen-data` 会检查 `data.ochlv_path`，有则走 `load_ochlv_csv` | **注释与代码不符**：`cli/main.py:242-254` 的 `cmd_gen_data` **无条件**调 `make_synthetic_panel(syn)`，**没有** `ochlv_path` 分支。**loader 本身是存在的、也是可调用的**：`src/mlquant/data/__init__.py:31` 导入 `load_ochlv_csv`、`:40` 进 `__all__`，`:48-66` 的 `make_panel(source="csv", path=…)` 会调用它（`data/loaders.py:58`）；**但官方 CLI 没有任何子命令走这条路径**——`cli/main.py:68-78` 的 `_load_panel` 只从 `panel.pt` 重建面板，`:32` 的 import 里也没有 `make_panel`/`load_ochlv_csv` | 我们走新库 parquet 直连，不依赖这条路径 | **缺口（代码）**：**官方 CLI 没有任何加载真实数据的入口**。`make paper CONFIG=configs/paper.yaml` 只能跑合成面板 ⇒ **Table 11 的真实面板数字官方仓根本复现不出来**，必须自己写数据接入层（本单交付的[导出规格](./mlquant_export_spec.md)就是这一层）。**缺口是「缺 CLI 接线 + 缺数据格式桥」，不是「缺 loader」** |

### 2.2 因子

| # | ① 论文原文 | ② 官方代码 `path:line` | ③ 我们库 / 现有实现 | ④ 结论 |
|---|---|---|---|---|
| B1 | Table 1（p.5）家族构成：`alpha 9 / better 28 / best 21 / old 50 / stock 22 / extra 14 / add 30 / change 5 / original 28 / cs_rank 6` = **213** | 实测注册 **213** 个，逐家族计数与 Table 1 **逐项一致**（`scripts/enumerate_mlquant_factors.py`）；10 个模块 `src/mlquant/features/_factors_*.py` | WORK-449 只有 **12** 个自建因子（`work449_build_factors.py:42-56`） | **一致（论文↔代码）**；**我们缺 201 个** |
| B2 | §3.4（p.4）「每个因子是独立函数，`@register_legacy_factor("name")` 自动注册」 | `features/legacy_factors.py:45-53` 装饰器 + `LEGACY_REGISTRY` | 无 | **一致**；MIT 许可，可直接复用官方 10 个模块，**不必自造** |
| B3 | §3.4（p.4）Neutralisation Eq(2)：**逐日 OLS 对行业哑变量（29 个中证行业）+ 可选 log 市值** 残差化（`torch.linalg.lstsq`），**随后** cross-sectional z-scoring | `features/legacy_factors.py:129-131` 实际只做 `cs_zscore(values, mask)`；`features/neutralize.py:27-74` 的 `neutralize_industry` **实现了但全仓无调用**（只在 `tests/test_neutralize.py`） | WORK-449 用 MAD 5× 去极值 + z-score（预注册 v1.4 §4） | **冲突（论文↔代码）**：论文 Eq(2) 在官方管线里**没生效**；且我们库**没有 29 个中证行业分类**（只有 `index_classify` SW2021 的 L1=31 类 与 `stock_basic.industry` 的 110 类）→ **双重缺口** |
| B4 | 全文未写去极值（winsorize） | 代码无 | WORK-449：MAD 5× | **论文未写 / 代码未实现**；我们保留 MAD 5× 须**披露为偏离** |
| B5 | §3.3（p.4）「分母用有效股票数，不是总股票数」 | `features/tensor_factors.py:81-82` `denom = mask.sum(dim=1)` ✓ | WORK-449 已镜像 | **一致** |
| B6 | §3.3（p.4）「`ts_corr` 的输出掩码是窗口内所有格的 AND——任一格被掩就判为不可靠」 | **代码不是 AND**：`tensor_factors.py:190` `out_mask = (mw.sum(dim=1) >= 2)`（≥2 个有效格即可）。逐原语实际规则：`ts_mean` `>0`(:130)、`ts_std` `>=2`(:142)、`ts_min/max` `any`(:150,:158)、`ts_rank` `all`(:169)、`ts_sum` `all`(:118)、`ts_corr` `>=2`(:190) | WORK-449 已按**代码**实现（`work449_build_factors.py:10-14` 的 docstring 逐条记录） | **冲突（论文↔代码）**：论文描述的保守 AND 规则只对 `ts_rank`/`ts_sum` 成立。我们跟代码（与 WORK-449 一致），并把差异列为披露项 |
| B7 | §3.3（p.4）+ §5.2（p.10）EWMA 递推用 float64 | `features/tensor_factors.py:211-220` `ewma` 显式 `x.to(torch.float64)` ✓ | WORK-449 未用 EWMA | **一致（论文↔代码）** |
| B8 | §3.4（p.4）未逐因子列字段；§A.1 给出 Panel 字段清单 | **实测（AST 静态解析 213 个函数）**：只需 OHLCV **155** 个；需 `vwap` **58** 个（其中 **15** 个读 `panel.amount`）；**0** 个因子读 `limit_up/limit_down/last_close` | 见 [`mlquant_factor_fields.md`](./mlquant_factor_fields.md) | **一致（代码↔实测）**。字段需求全景：需 `close` 161、`volume` 91、`high` 52、`low` 50、`open` **32**、`vwap` **58** |
| B9 | §3.4（p.4）「因子注册后 `compute_legacy_set` 迭代注册表，堆成 `[T,N,213]` 并对所有掩码取交集」 | `features/legacy_factors.py:138-160` ✓（`joint = joint & m`） | 无 | **一致**；可直接复用 |

### 2.3 标签

| # | ① 论文原文 | ② 官方代码 `path:line` | ③ 我们库 / 现有实现 | ④ 结论 |
|---|---|---|---|---|
| C1 | §3.5.1（p.5）「`y` 是次日简单收益 `r_{t+1,i}`」 | `features/target.py:77-79` `target_01` → `forward_return(panel, horizon=1)` = `close[t+1]/close[t]-1`；**纯价格收益，不含分红再投** | WORK-449 用复利后的 `r_eff`（`work449_build_panel.py:201-208`），本质是分红调整后的价格收益 | **一致（论文↔代码）**。须明示后果：**只有把 `close` 做成复权序列，标签才隐含分红再投**；用不复权价会在除权日造出假跌 |
| C2 | §3.5.1（p.5）「样本入集须**同时** `M_{t,i}=1` 且 `M_{t+1,i}=1`——若 t+1 撞板，标签收益不可成交，这个双向检查至关重要」 | **未完整实现**：`cli/main.py:295` 的 `label_mask` 来自 `target_01(panel)`，而 `panel` 是**原始面板**（`cli/main.py:293`），其 mask 只有「有 close」；`training/dataset.py:52-57` 用 `label_valid = label_mask[:-1]`。⇒ **t+1 的限价条件从未进入标签掩码** | WORK-449 的 `m_exec` 双臂一致（`work449_build_panel.py:227-230`），标签用 `m & m[1:]`（`panel_stats` 的 `label_cells_test`） | **缺口（代码）**：论文 §3.5.1 的双向**限价**检查官方没做。**必须我们自己补**——[导出规格 §5](./mlquant_export_spec.md) 给做法 |
| C3 | §3.5.1 标签本身不做中性化；中性化写在 §3.4 因子层 | `training/dataset.py:50-64` 只做掩码与有限性筛选 | 同 | **一致**（中性化是因子层的事） |

### 2.4 模型

| # | ① 论文原文 | ② 官方代码 `path:line` | ③ 我们库 / 现有实现 | ④ 结论 |
|---|---|---|---|---|
| D1 | §3.5.3（p.5）MLP：两层隐层、**128** 单元、GELU、dropout 0.1、线性输出到标量；输入 213 维；**参数量 ≈44K** | `models/nets.py:15-31`：`Linear(in,128)→GELU→Dropout→Linear(128,128)→GELU→Dropout→Linear(128,1)`；**torch 实测**（直接实例化官方类）in=213 时参数量 = **44,033** ✓ | WORK-449 用 hidden=128（`scripts/work449_train.py:82-88`，结构与官方 `MLPRegressor` 逐层一致），但输入只有 12 维 ⇒ torch 实测 **18,305** 参数 | **一致（论文↔代码）**。⚠️ WORK-449 预注册 v1.3 §2 写的 12 维参数量「18,177」**算错了 128**（应为 18,305，漏了一个 bias）；**hidden=128 的裁定不受影响**（两个数都指向 128），但该数字需在后续引用时更正 |
| D2 | §3.5.3（p.5）Transformer：213 个标量因子→共享线性层投到 **R^64** + 可学习位置编码 + 前置 CLS；**2 层** encoder、**4 头**、feedforward **256**；处理 214 token；**参数量 ≈220K** | `models/nets.py:34-62`：`d_model=64, n_heads=4, depth=2, dim_feedforward=4×64=256` ✓ 结构逐项一致；**torch 实测**（实例化官方 `TransformerRegressor`）：depth=2 → **113,857**（≈114K）；depth=3 → 163,841；depth=4 → **213,825** | — | **冲突（论文↔代码）**：结构描述一致，**参数量对不上（论文 ~220K vs 代码实测 113,857，约 1.93×）**。**本单裁定 depth = 2**（依据见 §3.5）；~220K 作为**披露项 + 首个敏感性测试项**保留 |
| D3 | §3.5.3 明写 hidden=**128** | `configs/paper.yaml:17` `hidden: **256**`；但 `models/nets.py:18` 类默认 `hidden=128`；`cli/main.py:309` `hidden=cfg.get("hidden", 128)` | WORK-449 预注册 v1.3 §1.4 已裁定 **以论文正文为准，hidden=128** | **维持 v1.3 裁定（hidden=128）**——见 §3 专节 |
| D4 | §3.5.2 Eq(3)（p.5）AdjMSE：同号 → `γ`，异号 → `1+γ`；`γ=0.1` 时惩罚比 11:1 | `models/losses.py:44-56`：`same_sign = (pred*target) > 0` → `γ`，否则 `1+γ` ✓ | WORK-449 v1.3 已对齐 | **一致**。边界差异须披露：代码用 `>0`，论文写 `sign(ŷ)=sign(y)`；**任一为 0 时两者不等价**（代码判为异号） |
| D5 | §3.5.2（p.6）「AdamW, lr 5e-4, wd 1e-5, grad clip 1.0, batch 8192, ≤60 epochs, **10% validation holdout 上 early stopping**」 | `configs/paper.yaml:36-45` 全部对上 ✓；`training/trainer.py:61-65` AdamW；`:188-190` `clip_grad_norm_`；`:97-106` 验证集 = **时间上最后 10% 的完整日期**（`val_split_mode="time"`）；`:153-163` 跑满 60 epoch，**保存 val loss 最优的 checkpoint**，**没有 patience、没有提前退出** | WORK-449 v1.3 §3 **不采用** 10% 留出，保留 2015-2020/2021/2022-2024 三段切，理由已写死 | **基本一致，两处口径必须写死**：①「10% holdout」在代码里是**按时间切**（最近 10% 日期），不是随机留出；② 代码**没有 early stopping**，是「跑满 + 保留最优 checkpoint」。论文措辞与代码不等价，须按代码走并披露 |
| D6 | §3.5.1（p.5）样本 = (t 日因子向量, t+1 收益) | `training/dataset.py:28-64` ✓ | 同 | **一致** |
| D7 | §3.5.4（p.6）GBM 增广：估 per-stock μ̂/σ̂ → 生成 log-return → **在合成面板上重算全部 213 因子** → 拼接；创新项按 **21 天块**抽样；`n_s=1` 数据翻倍 | `training/augment.py:23-89`：产出 `[n_s·T, N]` 的**收益**张量与全 True 掩码；`block_size=21` ✓、`n_synthetic=1` ✓、μ̂/σ̂ 用掩码历史 ✓。**但 `gbm_augment` 在 `src/` 内没有任何调用方**（只被 `training/__init__.py` 导出） | 无 | **缺口（代码）**：**「合成面板重算因子」这一步官方没实现、也没接线**。论文 Algorithm 1（p.17）第 5–8 行的流程在 CLI 里没有对应物 ⇒ Table 4/12 的 +0.19 Sharpe **无法用 `make paper CONFIG=configs/paper.yaml` 直接复现** |
| D8 | §3.3（p.4）/§4.4（p.8）单 A100 GPU、4.2 GB 显存、51× 加速 | `configs/paper.yaml:42` `device: cuda` | 本机无 GPU（CPU 路线） | **需确认**：不影响规格对齐，影响复现耗时 |

### 2.5 组合

| # | ① 论文原文 | ② 官方代码 `path:line` | ③ 我们库 / 现有实现 | ④ 结论 |
|---|---|---|---|---|
| E1 | §3.6 Eq(4)（p.6）`max wᵀμ − α·wᵀΣw`，s.t. `1ᵀw=1, 0≤w≤w_max` | `portfolio/markowitz.py:148-155`：`Maximize(mu@w − α·sum_squares(Fᵀw))`，约束 `sum(w)==1−cash`、`0≤w≤cap` ✓ | WORK-449 同口径 | **一致** |
| E2 | §3.6（p.6）风险厌恶 `α = 10` | `configs/paper.yaml:48` `risk_aversion: 10.0`；**但 `MarkowitzConfig` 类默认是 `1.0`**（`markowitz.py:46`） | WORK-449 用 10 | **一致**（必须显式传配置，否则静默落到 1.0） |
| E3 | §3.6（p.6）单票上限 `w_max = 0.03` | `configs/paper.yaml:49` `weight_cap: 0.03`；类默认 `0.05`（`markowitz.py:47`） | WORK-449 用 3% | **一致**。**注意实现细节**：`markowitz.py:117-122` 先 clip 再 `w *= (1−cash)/sum(w)` 归一化，归一化**可能把单票推回 3% 之上**（若 `sum(w)<1`），复现时须复检上限 |
| E4 | §3.6（p.6）只做多（A 股融券限制） | `configs/paper.yaml:50` `long_only: true` ✓ | 同 | **一致** |
| E5 | 全文未写现金权重 | `configs/paper.yaml:51` `cash_weight: 0.0`；`markowitz.py:95-100` 约束 `sum(w) = 1 − cash_weight` | WORK-449 全仓满仓 | **论文未写**；代码 = 0 |
| E6 | §3.6（p.6）「`sklearn.covariance.LedoitWolf`，**120 日**回看窗」 | `configs/paper.yaml:16` `cov_lookback: 120`；`markowitz.py:128-132` `LedoitWolf().fit(history).covariance_`；`cli/main.py:342` **默认 60**；`cli/main.py:357` `history = rets[t−look_back:t, idx]` | WORK-449 用 LW + 120 日 | **一致**（同样必须显式设 120） |
| E7 | §3.6（p.6）「PSD 投影，特征值下限 1e-10」；§5.2（p.10）「协方差特征值符号翻转」 | `portfolio/markowitz.py:134-139` `np.clip(w, 1e-10, None)` ✓ | WORK-449 同 | **一致** |
| E8 | §2/§3.6 用 cvxpy + SCS（参考文献 [31] O'Donoghue 等） | `configs/paper.yaml:52` `solver: SCS`（注释「change to MOSEK if licensed」）；`markowitz.py:107-110` `problem.solve(solver=cfg.solver, warm_start=True)`，SolverError 退回 SCS | WORK-449 v1.4 §1.3：两口径相对差均超 1e-6（权重口径最大 **4.686871e-03**） | **论文↔代码一致**；**我们与官方求解器口径差异已披露且已量化**（见 WORK-449 v1.4） |
| E9 | §3.6（p.6）「问题只建一次 + `cp.Parameter` 占位，warm-start，1.2s→0.2s」 | `portfolio/markowitz.py:141-157` 按**股票数 n** 缓存 problem；`:72` `self._problems: dict[int, ...]` | 同 | **一致**（注意按 n 分桶 ⇒ 换手导致 n 变动会新建 problem，warm-start 命中率会掉） |
| E10 | Algorithm 1（p.17）第 10–14 行逐日解 QP | `cli/main.py:351-363` 逐日循环 ✓ | 同 | **一致**（每日调仓） |

### 2.6 成本

| # | ① 论文原文 | ② 官方代码 `path:line` | ③ 我们库 / 现有实现 | ④ 结论 |
|---|---|---|---|---|
| F1 | §3.6（p.6）`r_net = wᵀ_{t−1} r_t − c·‖w_t − w_{t−1}‖₁`，`c` = 每单位换手 5–8 bps | `backtest/engine.py:79-89`：`w_lag[1:]=weights[:-1]`；`gross=(w_lag*returns).sum(1)`；`delta[0]=\|w₀\|`，`delta[1:]=\|w_t−w_{t−1}\|`；`cost=delta.sum(1)·(costs_bps·1e-4)`；`net=gross−cost` | WORK-449 口径相同（8 bps 双边） | **一致**（滞后一期权重、**初始建仓也计费**） |
| F2 | Table 2 题注（p.7）「net of **8 bps** transaction costs」；§3.6「5–8 bps per unit of turnover」；§7.2（p.13）「5–8 bps 线性成本模型」 | `configs/paper.yaml:15` `costs_bps: 8.0`；`backtest/engine.py:12-15` docstring 称 "**Round-trip** transaction cost in basis points charged on absolute weight changes" | WORK-449：8 bps 主口径 / 5 bps 敏感性 | **口径必须写死**：成本按**全 L1 换手**计（一次满仓换手 L1≈2），故 8 bps × L1 ≈ **单边 16 bps**。论文的 "round-trip" 一词与 "per unit of turnover" 不是一回事，不能混用 |
| F3 | §7.2（p.13）「线性成本对大组合偏乐观，真实冲击成本对交易规模是凸的；`w_max=3%` 部分缓解」 | `backtest/engine.py:16-19` 明说滑点/借券/T+1 交收「都放到兄弟模块」；另有 `backtest/constrained.py` / `self_financing.py` / `capacity.py` 三个可选核算模块，但 **`paper.yaml` 未启用**（`backtest_accounting` 默认 `target_difference`，`cli/main.py:388`） | WORK-449 用线性成本 | **一致**（线性、无冲击）；须在报告里披露 |

### 2.7 评价

| # | ① 论文原文 | ② 官方代码 `path:line` | ③ 我们库 / 现有实现 | ④ 结论 |
|---|---|---|---|---|
| G1 | §3.7（p.6）「年化收益、Sharpe、Sortino、Calmar、最大回撤、L1 换手；另有 IC 计算报 Pearson 与 Spearman」 | `backtest/metrics.py:21-94` 全部实现 ✓ | WORK-449 同类指标 | **一致** |
| G2 | §3.7 只说「按 Bailey & López de Prado 惯例」 | `backtest/metrics.py:18` `DAYS_PER_YEAR = 252`；`:21-24` 年化 = `(1+r).prod()**(252/n)−1`（几何）；`:31-36` Sharpe = `mean/std(ddof=1)·√252`，**`rf` 默认 0** | WORK-449 用 **365.25 自然日**年化 + **rf 1.5%** | **需转换**：官方 252 交易日 + rf=0，我们 365.25 + rf=1.5%。**两组 Sharpe 不可直接比**，对照表须同时给两套口径 |
| G3 | §3.7 未定义回撤窗口 | `backtest/metrics.py:52-57` 用日收益累乘净值 → `(peak−equity)/peak` | WORK-449 收盘 cummax | **一致** |
| G4 | §3.7 说「**L1 turnover**」；§3.6 成本公式用**全 L1** | `backtest/metrics.py:91-94` `turnover = 0.5 · mean(\|w_t − w_{t−1}\|)` ⇒ **半 L1，且不含首日建仓** | WORK-449 自算换手 | **冲突（论文↔代码内部）**：报出的 turnover 是 L1 的**一半**，而成本按**全 L1** 计。论文 Table 2 的 `Turn. 0.29` 是这个半 L1 口径。对照时必须按同一口径重算 |
| G5 | §3.7「单独算 Pearson 与 Spearman IC」；Table 11 用「Apparent IC」 | `backtest/factor_ic.py:16-74` 逐日截面 IC，`min_assets=3`，无效日返回 **NaN 而非 0**；`metrics.py:67-88` 单截面版本 | WORK-449 `primary_test.json` 口径自定 | **一致**（算法一致）。但 **"Apparent IC" / "Realisable IC" 的具体算法（是否按 `M_{t+1}` 过滤）论文没给公式**，§6.3（p.12）只有文字描述 → 须自行定义并披露 |
| G6 | §4.2（p.7）DSR = 0.978（真实）/ 0.994（合成），`N≈50` 有效配置、`T≈756`、偏度 `−0.31`、峰度 `4.7`、`c_SR0≈0.93` | **代码未实现 DSR** | 无 | **代码未实现**；我们若要报 DSR 需自己实现并写明参数（尤其 `T` 要用 726 还是 756，见 G7） |
| G7 | §4.2（p.7）与 §7.2（p.13）「真实面板测试段 **≈756** 个交易日」 | — | 新库 2022–2024 = **726** 个交易日（242/年 × 3），与 WORK-449 实测的 726 **完全一致** | **冲突（论文↔现实）**：756 = 3 × 252 是**日历假设**，A 股实际是 242/年 ⇒ 726。差 30 天（4.0%）。**复现以 726 为准并披露** |

### 2.8 论文自身的内部冲突（必须知道，否则会对错数）

这一节不是「我们 vs 论文」，是**论文自己前后不一致**，直接影响 Table 11 的对照基准。

| # | 冲突点 | 证据 | 结论 |
|---|---|---|---|
| P1 | **Table 10 的 IC 列是合成面板的，Sharpe 列是真实面板的** | Table 10（p.13）「No mask 0.058 / Halt only 0.052 / Full 0.049」与 Table 11（p.18）**合成**行完全一致；但同表 Sharpe 1.23/1.39/1.63 又是 Table 11 **真实**行 | **论文表格串行**。§6.3 的「全掩码 realisable IC 0.049 最高」用的也是这个被污染的数 |
| P2 | **真实面板 Full 掩码 IC 到底是 0.039 还是 0.049** | Table 11（p.18）真实 Full 行 = **0.039**；Table 10（p.13）+ §6.3（p.12）= **0.049** | **以 Table 11 为准（0.039）**——Table 11 是本单的复现目标。WORK-449 的 `paper_comparison.md` 用的 0.047/0.043/0.039 ✓ 正确 |
| P3 | **摘要与 §1 说「IC 被抬高 18%」是真实面板的事** | 摘要（p.1）与 §1（p.1）「On real A-share data it inflates apparent information coefficient by 18%」；§4.3.1（p.7）「increases apparent IC from 0.049 to 0.058 (+18%)」——这组数是**合成**面板的 | **论文措辞错位**。真实面板的同类变化是 0.039 → 0.047 = **+20.5%**（Table 11 真实行）。复现报告须按真实面板口径写 |
| P4 | **Table 2 与 Table 11 真实行是否一致** | Table 2（p.7）Full system：Sharpe 1.63 / Ann.Ret 15.8% / MaxDD 11.4% / Sortino 2.31 / Turn 0.29；Table 11（p.18）真实 Full 行：1.63 / 15.8 / 11.4 | **一致 ✓**（这两张表可互证）。Turn 0.29 是半 L1 口径（见 G4） |
| P5 | **`hidden` 128 vs 256** | §3.5.3（p.5）写 128 并自述 ~44K；`configs/paper.yaml:17` 写 256 | 见 §3 专节 |
| P6 | **Transformer 参数量 ~220K vs 代码实测 113,857** | §3.5.3（p.5）写 ~220K 且写「Two Transformer encoder layers」；`models/nets.py:34-62` 实测 depth=2 ⇒ 113,857（depth=4 ⇒ 213,825） | 见 D2 与 §3.5。**本单裁定 depth=2**（正文结构 + 官方代码一致，参数量近似值权重最低）；depth=4 列为首个敏感性测试项 |

---

## 3. 关键裁定：hidden 128 vs 256

### 3.1 裁定

**沿用 WORK-449 预注册 v1.3 §1.4 的裁定：`hidden = 128`，不推翻。**

### 3.2 理由（本轮复核，未改变结论，但补了两条新证据）

1. **原理由仍然成立且被本轮独立复核**：论文 §3.5.3 明写「Two hidden layers (128 units, GELU, dropout 0.1)… Total parameters: ~44K」；
   `models/nets.py:15-31` 的结构**用 torch 直接实例化实测**，in=213 时参数量 = **44,033**，与论文自述自洽；
   而 `hidden=256` 时为 **120,833**，与「~44K」差 2.75 倍。论文正文 + 参数量自洽性双重校验 ⇒ 以正文为准。
2. **新证据①：`hidden` 的默认值就是 128**。`models/nets.py:18` 的签名默认 `hidden: int = 128`，
   `cli/main.py:309` 的 fallback 也是 `cfg.get("hidden", 128)`。**只有 `configs/paper.yaml:17` 这一行把它改成了 256**。
   也就是说 256 是**配置文件的一处改动**，不是模型类的设计。
3. **新证据②：256 会直接改变 Table 11 的复现基准**。Table 11 的真实面板 Full 行是 Transformer + AdjMSE + GBM + LW 的结果；
   hidden 从 128 改到 256，参数从 44,033 涨到 120,833（2.75×）。§7.2（p.13）自述「Transformer 过拟合很严重（train IC 0.12 / test IC 0.03），
   加一个合成面板后稳定下来（train IC 0.08 / test IC 0.049）」——**参数容量是这个结论的核心变量**。
   要复现 Table 11 的 1.63，hidden 必须按论文正文取 128。

### 3.3 但要同时记住的一件事

**这不代表官方 `paper.yaml` 的 256 是笔误。** 它与论文正文矛盾这件事在 WORK-449 v1.3 §1.4 已记录。

**但要把两件事分开看**：

- **`hidden` 是真冲突**：官方**配置**（256）与论文**正文**（128）直接矛盾，且 256 会让参数量从 44K 涨到 121K，
  直接改变 Table 11 的复现基准 ⇒ 必须裁定，取 128。
- **`depth` 不是真冲突**（见 §3.5）：论文**正文**说 2 层、官方**代码**也是 depth=2，**二者一致**；
  唯一的异数是论文自述的参数量「≈220K」，而参数量是标量近似值、不是结构规格 ⇒ 取 depth=2，不需改代码。

两者合起来的真实含义是：**官方 `configs/paper.yaml` 相对论文正文有至少一处结构性偏离（`hidden`），
因此 `make paper CONFIG=configs/paper.yaml` 复现不出论文 Table 11 的真实面板数字** ——
再加上官方 CLI 根本没有真实数据入口（见 A13），这条命令只能跑合成面板。
复现时必须显式写死：**hidden=128、depth=2**。

### 3.5 顺带裁定：Transformer `depth = 2`（论文正文 + 官方代码一致）

**问题**：论文 §3.5.3 同时说「**Two** Transformer encoder layers」和「参数量 **≈220K**」；
官方 `models/nets.py:34-62` 是 `depth=2`（实测 **113,857**）；`depth=4` 才是 **213,825**（≈220K）。

**裁定：`depth = 2`。** 依据：

1. **正文的具体结构陈述与官方实现一致**（2 层 / 4 头 / d_model 64 / ff 256，四项逐项对上）。
   官方 `configs/paper.yaml` 自称「Full paper-reproduction config」，作者发布的代码就是 depth=2。
2. **参数量是自述的近似值**（「≈220K」），不是结构规格。把它当成规格去反推 depth，是拿一个
   标量去推翻一条结构描述。§3.2 的 `hidden` 裁定用的是**相反**的权重（正文 128 + 44K 自洽 ⇒ 正文赢），
   这里同理：**正文与官方代码一致时取二者共同的值，自述参数量权重最低。**
3. **depth=4 需要改动官方代码**（`TransformerRegressor` 的默认 `depth=2` 是作者写的），
   而 depth=2 不需要任何改动。

**披露与回滚**：本条进报告的「口径偏离与未解释项」一节。
**敏感性路径**：若复现出的 Sharpe 明显低于 Table 11 的 1.63，**第一个要跑的就是 `depth=4`**
（参数量 213,825，与论文自述 ~220K 对齐），在 2021 验证段上对比即可，不动测试段。

### 3.6 两个裁定的关系与回滚方式

**裁定 A（`hidden=128`）沿用 WORK-449 预注册 v1.3 §1.4**，理由是「正文 + 参数量自洽性」双重支持正文。
**裁定 B（`depth=2`）是本单新增**，理由是「正文结构陈述 + 官方代码实现」一致。
两条裁定用的是**同一条权重原则**：**正文的具体规格陈述 > 官方配置文件的一行改动 > 自述的标量近似值**。
（`hidden` 上官方配置与正文冲突 ⇒ 正文赢；`depth` 上官方代码与正文一致 ⇒ 一致值赢，参数量是唯一的异数。）

**回滚方式**：两条都只写在文档里，不改代码、不改数据。
- 推翻 A：改写 v1.3 §1.4 + 在 `experiments/WORK-449/failure_log.md` 追加推翻证据 + 重训三臂。
- 推翻 B：把 §3.5 的裁定改写为 depth=4（改动点：`TransformerRegressor(depth=4)` 一处），并在
  验证段上先做对比。**训练前推翻成本最低。**

---

## 4. 213 因子字段需求清单（结论摘要）

完整逐因子表见 **[`mlquant_factor_fields.md`](./mlquant_factor_fields.md)**（213 行 + 机器可读 CSV），
复算脚本 **`scripts/enumerate_mlquant_factors.py`**（只用标准库 `ast`，不装 torch 也能跑）。

### 4.1 确定数字

| 口径 | 因子数 | 占比 |
|---|---:|---:|
| **只靠 OHLCV**（open/high/low/close/volume）即可算出 | **155** | 72.8% |
| **需要 vwap**（volume 已有） | **43** | 20.2% |
| **需要 vwap 且读取 `panel.amount`** | **15** | 7.0% |
| **需要库外字段**（限价 / 停牌 / 流通市值等） | **0** | 0% |
| **合计** | **213** | 100% |

### 4.2 单字段需求计数

| 字段 | 需要它的因子数 | 说明 |
|---|---:|---|
| `close` | 161 | 几乎所有因子都要 |
| `volume` | 91 | |
| `vwap` | **58** | 新库须派生（`amount×10÷vol`） |
| `high` | 52 | |
| `low` | 50 | |
| `open` | **32** | **WORK-449 现有面板没有 open 列 —— 硬缺口** |
| `amount` | 15 | 有则用 amount，无则退化为 `vwap×volume`（**数值不同**） |

### 4.3 家族 × 字段桶

| 家族 | 论文 Table 1 | 代码模块 | OHLCV | 需 vwap | 需 vwap+amount |
|---|---:|---|---:|---:|---:|
| `alpha_*` | 9 | `_factors_alpha101.py` | 9 | 0 | 0 |
| `better_*` | 28 | `_factors_better.py` | 8 | 15 | 5 |
| `best_*` | 21 | `_factors_best.py` | 16 | 5 | 0 |
| `old_*` | 50 | `_factors_old.py` | 33 | 17 | 0 |
| `stock_*` | 22 | `_factors_stock.py` | 17 | 5 | 0 |
| `extra_*` | 14 | `_factors_extra.py` | 10 | 4 | 0 |
| `add_*` | 30 | `_factors_add.py` | 25 | 5 | 0 |
| `change_*` | 5 | `_factors_change.py` | 5 | 0 | 0 |
| `original_*` | 28 | `_factors_original.py` | 28 | 0 | 0 |
| `cs_rank_*` | 6 | `_factors_market.py` | 4 | 2 | 0 |
| **合计** | **213** | | **155** | **43** | **15** |

> 家族计数与论文 Table 1 逐项一致。官方 `features/legacy_factors.py:3` 的 docstring 写「Ports all ~150 factors」，
> 与实际注册的 213 不符 —— **以代码为准，docstring 过时**。

---

## 5. 新库现状（本轮实测）

### 5.1 已入库的数据集与 schema

| 数据集 | 分区数 | 行数（2015–2024） | 关键列 | JSON 字段（`jq` 取值） |
|---|---:|---:|---|---|
| `daily` | 2,431 | **9,482,407** | `ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount` | `.row_counts.daily.paper_window` |
| `adj_factor` | 2,433 | 9,969,383 | `ts_code, trade_date, adj_factor` | `.row_counts.adj_factor.paper_window` |
| `daily_basic` | 2,431 | 9,392,135 | `turnover_rate, turnover_rate_f, volume_ratio, pe, pb, total_mv, circ_mv, free_share, …` | `.row_counts.daily_basic.paper_window` |
| `stk_limit` | 2,431 | 9,724,911 | `ts_code, trade_date, up_limit, down_limit` ← **交易所真实涨跌停价** | `.row_counts.stk_limit.paper_window` |
| `suspend_d` | 2,431 | 330,217 | `ts_code, trade_date, suspend_timing, suspend_type` | `.row_counts.suspend_d.paper_window` |
| `index_daily` | 2,431 | 21,976,288 | 指数行情（含 CSI All-A 基准） | `.row_counts.index_daily.paper_window` |
| `stock_basic` | L/D/P | 5,580 / 344 / 0 | `ts_code, symbol, name, area, industry, market, list_date, act_name, act_ent_type` | `.universe.stock_basic_partition_rows` |
| `trade_cal` | SSE/SZSE | 7,630 / 7,630 | `exchange, cal_date, is_open, pretrade_date` | `.row_counts.trade_cal` |
| `index_classify` | SW2021 | 511 | `index_code, industry_name, level(L1=31/L2=134/L3=346), industry_code` | `.row_counts.index_classify` |

> 表里**每一个行数都能用 `jq` 从 `experiments/WORK-516/library_measurements.json` 取到同值**，例如
> `jq '.row_counts.adj_factor.paper_window.rows'` → `9969383`。日期型数据集的 `paper_window` 子对象同时带
> `partitions` 与 `rows`（窗口 = 2015-01-01…2024-12-31）；`row_counts.<数据集>.rows` 是**含 1 年预热的
> 2014–2024 全窗**计数，与本表的 2015–2024 口径**不是同一个数**，引用时不要混用。

**全库覆盖**：2006-01-04 → 2026-11-20，5,072 个交易日分区，`daily` 共 15,966,167 行。

### 5.2 尚未入库（导出规格不依赖，但影响后续）

`index_weight` / `namechange` / `dividend` / `cashflow` / `fina_indicator` **当前 `/srv/share/quantdb/raw/` 下不存在**。
其中：
- `namechange` —— WORK-449 用它做 ST 区间判定（`work449_build_panel.py:172-181`）；用真实限价后 ST 判定**不再必需**，但 ST 仍影响可交易性披露。
- `dividend` —— 若改用不复权价 + 分红再投标签才需要；本单裁定走复权序列，**暂不需要**。
- `index_weight` —— 若要复现「CSI All-A 指数」基准或限定指数成分股才需要；全 A 池不需要。

### 5.3 三项已实测的量化结论

| 结论 | 数字 | 怎么测的 |
|---|---|---|
| **VWAP 公式确认**：`vwap = amount(千元) × 10 ÷ vol(手)` | 与 `(H+L+C)/3` 的中位相对偏差 **0.12%–0.28%**，p90 ≤ 0.8%，>5% 的占比 ≤ 0.11% | 抽样 3 个交易日全量行 |
| **真实限价 vs 9.8% 代理**：代理不等价 | 10 个抽样日上代理**假阳 1.31%**、**漏检 0.48%**；限价事件合计占 7.18% 的行 | `stk_limit.up_limit/down_limit` vs `\|close/pre_close−1\|>0.098` |
| **复权缩放不破坏限价判定** | qfq（基准 2024-12-31）缩放后，用 `eps=1e-3` 判定「触板」的集合与**未缩放前逐格一致（6 个抽样日 100% 相同）** | `close×f` vs `up_limit×f`，`f = adj_factor[t]/adj_factor[2024-12-31]`，`f` 实测范围 0.052–6.29 |

### 5.4 关键行数（导出预期）

| 窗口 | 交易日 | `daily` 行数 | JSON 字段（`jq`） |
|---|---:|---:|---|
| 2014-01-01 – 2024-12-31（**含 1 年预热**，推荐导出窗） | 2,676 | **10,053,442** | `.row_counts.daily` |
| 2015-01-01 – 2024-12-31（论文真实面板窗） | 2,431 | 9,482,407 | `.row_counts.daily.paper_window` |
| 2015–2020（训练段） | 1,462 | 4,665,263 | `.row_counts._segments.train_2015_2020` |
| 2021（验证段） | 243 | 1,085,445 | `.row_counts._segments.val_2021` |
| 2022–2024（测试段） | **726** | 3,731,699 | `.row_counts._segments.test_2022_2024` |

> **注意两套口径不要混用**：上表是 `daily` 的**原始行数**。**导出后**的行数（剔除缺 `adj_factor` 的行）
> 在 `.adj_factor_row_coverage.by_window` 里，2014–2024 窗 = **10,051,715** 行，见
> [导出规格 §7](./mlquant_export_spec.md#7-预期行数实测可直接用来验收)。

---

## 6. 缺口与风险清单

> 每条写清：**缺口是什么 → 影响哪张表/哪个因子 → 能不能用现有数据近似 → 近似的代价**。
> 「应该可以」不算结论。

### 6.1 官方代码缺口（论文有、代码无 —— 我们必须自己补）

| ID | 缺口 | 影响 | 能不能近似 | 代价 |
|---|---|---|---|---|
| **G-01** | **IPO「上市>1年」过滤未实现**（`src/` 全仓无 `list_date` 逻辑） | 论文 Eq(1) 的第三个条件；影响全部 213 因子与标签的样本选择 | **能，且不用近似**：新库 `stock_basic.list_date` 直接可用，WORK-449 已有 `listed_1y` 实现（`work449_build_panel.py:170`） | 低。须注意**退市股**也要用其原始上市日，不能用「当前在市」清单 |
| **G-02** | **行业/市值中性化 Eq(2) 未接线**（`neutralize_industry` 只在 tests 里被调用） | §3.4；影响 213 个因子进入模型前的分布 | **部分能**：① 行业 —— 用 `index_classify` SW2021 **L1=31 类**或 `stock_basic.industry`（110 类）替代论文的「29 个中证行业」，**分类体系不同**；② 市值 —— `daily_basic.total_mv`/`circ_mv` 可用 | **中–高**。分类体系替换会改变残差化的自由度与含义，**不能声称复现了 Eq(2)**，只能声称「用 SW2021 L1 做了等价的行业剔除」。若坚持中证 29 类，需另找数据源 |
| **G-03** | **§3.5.1 的 t+1 双向限价标签检查未实现**（`cli/main.py:295` 用的 label_mask 来自原始面板，不含限价条件） | 标签样本选择；直接影响 Table 11 的 Sharpe | **能，不用近似**：用 `stk_limit` 在 t+1 判触板，构造 `M_{t+1}` 再与 `M_t` 取交集 | 低，但**必须做**——这正是论文「上游污染」论证的核心环节，漏掉它等于把论文最想验证的那件事跳过了 |
| **G-04** | **GBM 增广「合成面板重算因子」未实现、未接线**（`gbm_augment` 在 `src/` 内没有任何调用方） | 论文 Table 4/12 的 +0.19 Sharpe；Algorithm 1 第 5–8 行 | **能，但要自己写**：按 §3.5.4 用 μ̂/σ̂ + 21 天块创新项生成合成价格路径，再跑一遍 213 因子 | **高（工程量）**。这一步在官方仓里不存在，属于「从论文重建」。**建议单独立子单做**，不阻塞 P0 规格对齐 |
| **G-04b** | **官方 CLI 没有真实数据入口**（`cli/main.py:242-254` 的 `gen-data` 无条件走合成数据；`configs/paper.yaml:5-10` 的 `data.ochlv_path` 注释与代码不符）。**缺口在 CLI 接线与数据格式桥，不在 loader**：`load_ochlv_csv`（`data/loaders.py:58`）本身可用，经 `src/mlquant/data/__init__.py:48-66` 的 `make_panel(source="csv", path=…)` 调用（`:31` 导入、`:40` 进 `__all__`）；但 `cli/main.py:68-78` 的 `_load_panel` 只从 `panel.pt` 重建面板、`:32` 未 import `make_panel`/`load_ochlv_csv` ⇒ **官方 CLI 无任何子命令走这条路** | **Table 11 的真实面板数字**——官方仓复现不出来 | **能，不用近似**：本单交付的[导出规格](./mlquant_export_spec.md)就是这一层（parquet → `load_ochlv_csv` 长表），列名/单位/复权口径全部写死 | 低（写脚本的活），但**这是整个复现的入口，没有它后面都跑不了** |

### 6.2 我们自身的缺口

| ID | 缺口 | 影响 | 能不能近似 | 代价 |
|---|---|---|---|---|
| **G-05** | **现有 WORK-449 面板没有 `open` 列** | 213 因子中 **32** 个需要 `open`（`old_*` 9、`add_*` 6、`alpha_*` 4、`stock_*` 4、`best_*` 3、`better_*` 3、`extra_*` 2、`cs_rank_*` 1） | **能，不用近似**：新库 `daily.open` 直接有 | 低。换数据源时一并补 |
| **G-06** | **现有 WORK-449 只有 12 个因子，缺 201 个** | Table 11 对照 | **能，不用自造**：官方 MIT 许可，10 个 `_factors_*.py` 直接复用 | 低（但需 torch + GPU/CPU 性能评估；§4.4 声称 51× 是 A100 上的数字，本机 CPU 需实测） |
| **G-07** | **现有 WORK-449 股票池是 CSI300（680 只），论文是全 A（≈5000 只）** | Sharpe 水平、权重集中度、换手、冲击成本的可比性 | **能**：新库 2015–2024 有 5,591 只（含 260 只退市股） | **中**。池子一变，**绝对 Sharpe 不可与论文逐位比**，只能比**方向与量级**（这与 WORK-449 预注册 §12 的原则一致） |
| **G-08** | **Transformer 参数量：论文自述 ~220K vs 代码实测 113,857（depth=2）/ 213,825（depth=4）** | Table 11 的复现基准（D2） | **能**：按「正文结构 + 官方代码一致」取 depth=2，depth=4 作为首个敏感性测试 | **低**（已裁定，见 §3.5）。披露要求：报告里必须写出参数量差异；敏感性：`depth=4` 在 2021 验证段上先比 |
| **G-09** | **`hidden` 冲突（128 vs 256）** | 模型容量 | 已裁定 **128**（§3） | 低，但要记录这是「以论文正文压过官方配置」的偏离 |
| **G-10** | **无 GPU** | §4.4 的 51× 加速、4.2 GB 显存结论无法在本机复现；213 因子 × 5000 股 × 2676 日的 CPU 成本未知 | 能（CPU 跑） | **中**。官方 `rolling_backend='linear'` 提供 FP64 块矩 CPU 路径（`features/rolling_backend.py`），但 `cli/main.py:259-263` 明说它「会改变下游排序」。**先在小子集上实测耗时**再决定是否需要 GPU |

### 6.3 口径冲突（两边都对，但不能混用）

| ID | 冲突 | 影响 | 结论 |
|---|---|---|---|
| **G-11** | **换手口径**：报出的 turnover 是**半 L1**（`metrics.py:91-94`），成本按**全 L1** 计（`engine.py:85-88`）；论文 §3.7 却说「L1 turnover」 | Table 2/11 的 `Turn.` 列 | **按代码口径（半 L1）报，并同时给全 L1**。对照时两边都用半 L1 |
| **G-12** | **年化口径**：官方 252 交易日 + `rf=0`；我们 365.25 自然日 + `rf=1.5%` | Sharpe 的绝对值 | **两套都报**，主口径用官方的 252+rf=0（对齐论文），365.25+rf=1.5% 作敏感性 |
| **G-13** | **8 bps 的含义**：乘在**全 L1** 上 ⇒ 一次满仓换手 ≈ 16 bps 单边 | 成本口径 | 写死「8 bps × 全 L1」，报告里同时给 5 bps 敏感性 |
| **G-14** | **论文 Table 10 的 IC 列是合成面板的**（P1–P3） | 对照基准 | **以 Table 11 真实行（0.047/0.043/0.039）为准**，并在报告里注明论文摘要的「+18%」是合成面板数 |
| **G-21** | **WORK-449 预注册 v1.3 §2 的 12 维 MLP 参数量写成 18,177，正确值是 18,305**（漏一个 bias） | 只是账面数字，**不影响 hidden=128 的裁定**（两个数都指向 128） | **torch 实测**：`MLPRegressor(in=12, hidden=128)` = 18,305。本单**不改** v1.3（历史预注册不改写），只在引用处注明正确值 |

### 6.4 数据缺口

| ID | 缺口 | 影响 | 能不能近似 | 代价 |
|---|---|---|---|---|
| **G-15** | **`stk_limit` 对 `daily` 的覆盖率 99.02%**（10–12 个抽样日实测，缺口 0.00%–1.47%，2015-01-05 缺 6 行、2020-01-02 缺 56 行） | 限价掩码的 ~1% 行 | **能**：缺带的行退回 9.8% 代理（论文 §A.2 的 regime 2）。但代理与真实限价在实测中有 1.31% 假阳 / 0.48% 漏检 ⇒ 这 ~1% 的行掩码会有偏差 | **低–中**。须在报告里给出「真实限价覆盖率」数字，并做一次「全代理 vs 真实限价」的双口径敏感性 |
| **G-16** | **`namechange` / `dividend` / `index_weight` / `cashflow` / `fina_indicator` 未入库** | ST 区间；分红再投标签；指数成分/基准 | ST：用真实限价后**不再是必需**（5% ST 带宽由交易所限价体现）；分红：走复权序列后**不需要**；指数：全 A 池 + `index_daily` 够用 | 低。**前提**：确认这些数据集在本单之后确实会被补上；若长期缺失，「ST 状态」只能从 `stock_basic.name` 的当前名称判断（对历史 ST 无效） |
| **G-17** | **`daily` 不含停牌日行**（实测 vol=0 的行 = 0） | 停牌口径 | **能**：用 `suspend_d` 显式标出 | 低。但**必须接**，否则无法区分停牌 vs 未上市，面板统计与掩码自检做不了 |
| **G-18** | **交易日历**：论文说测试段 ≈756 个交易日，实际 726 | DSR 的 `T` 参数（§4.2 用 `T≈756`） | **能**：用新库 726 | 低。DSR 须用 726 重算，或同时给 726/756 两版 |

### 6.5 结论

- **论文与官方代码在因子/模型/组合/成本四组口径上是一致的**（A、B、D、E、F 组的「一致」项），可以直接作为复现基准。
- **真正需要我们动手补的是 4 件代码缺口**：G-01（IPO 过滤）、G-02（行业/市值中性化接线）、G-03（t+1 双向限价标签）、G-04（GBM 增广重算因子）。
  其中 **G-01、G-03 成本低、影响大，必须做**；**G-02 只能做近似替代**（分类体系不同）；
  **G-04 工程量大，建议单独立单**。
- **数据侧我们比官方实现强**（真实涨跌停价、真 VWAP、退市股、停牌表），但**多出 3 个必须自己填的口径**：复权基准日、股票池、年化/换手口径。
- **本单未发现「结构性不一致」**（即整个口径体系不同、无法逐项对齐的情况）。所有偏差都是**可枚举、可标注、可给出代价**的局部偏差。

---

## 7. 下一步（按依赖排序）

| 顺序 | 事项 | 依赖 | 备注 |
|---|---|---|---|
| 1 | 按 [`mlquant_export_spec.md`](./mlquant_export_spec.md) 实现导出脚本 | 本单 | 本单只出规格，不实现 |
| 2 | 复用官方 10 个 `_factors_*.py`（MIT），接上 G-01（IPO）与 G-03（t+1 限价标签） | 1 | 这两项是 Table 11 复现的必要条件 |
| 3 | 若 2021 验证段上 Sharpe 明显低于预期，跑 **G-08 的 `depth=4` 敏感性**（本单已裁定 depth=2，理由见 §3.5） | 2 | 改动点只有 `TransformerRegressor(depth=4)` 一处 |
| 4 | 跑因子 IC 体检（213 个因子在 2022–2024 的逐日 IC 分布） | 2 | 先看 IC 分布，再谈训练 |
| 5 | G-02 的行业中性化替代方案定稿（SW2021 L1 vs 不做） | — | 影响可比性声明的措辞 |
| 6 | G-04 GBM 增广（若要做） | 2 | 建议单独立单 |
| 7 | 最终对照表：按官方口径（252 日 + rf=0 + 半 L1 换手 + 8bps×全 L1）出 Table 11 对照 | 2,4 | |

> **本单不做**：不训练、不回测、不改 `/srv/share/quantdb`、不写对外报告。
