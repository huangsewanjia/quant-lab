# WORK-566「环 2 · STOCK-JEPA」独立代码审查 · Findings Report 与整改记录

> 本文件是**独立子代理代码审查**（verifier，只读）的完整结论 + 本单的逐条整改记录。
> 审查对象：`quant-lab` 分支 `agent/agent/e0a29716dad1`，审查起始 HEAD `cef8e39`。
> 审查方式：独立子代理只读检查 + 在 `/tmp` 副本上做插桩与 A/B 实验；**未修改仓库任何文件**。
> 审查任务 ID：`bg_90e690f0-ade2-4a19-bcf3-a8a05f05d3a4`。

---

## 0. 审查结论（原文摘要）

**VERDICT：FAIL** —— 存在 **1 项 BLOCKING**（组合估值使用未来 bar 价格的前视泄漏），
另有 **11 项 NON-BLOCKING**、**37 条预注册逐条偏差表**、**5 项 VERIFIED CORRECT**、**9 项 NOT COVERED**。

BLOCKING 定义（四类）：look-ahead 泄漏 / 切分纪律 / 组合记账凭空增减资金 / 指标定义不符论文 / 审计不独立。
本次**仅 look-ahead 一类命中**，其余四类未发现。

---

## 1. BLOCKING

### B1 — 组合估值在停牌日用「全局最后一个 bar」的收盘价盯市，构成前视泄漏

- **severity**：BLOCKING（命中第一类定义「look-ahead 泄漏」）。实测对指标影响极小，不改变结论方向。
- **file:line**（审查时 HEAD）：
  - `scripts/work566_backtest.py:52-56` — `Panel.last_bar(self, stock_idx, date_idx)` 收了 `date_idx` 参数但函数体**完全不用**，直接返回该股票在全窗口最后一个有 bar 的下标
  - `scripts/work566_backtest.py:118-121` — `last_bar_of(s)` 包装上述调用
  - `scripts/work566_backtest.py:205-207` — 估值回退 `p = float(panel.close_hfq[s, last_bar_of(s)])`
  - `audit/recompute_work566.py:112-119` / `:198-201` — 审计侧同款逻辑（**结构上不可能自查出该类错误**）
  - 违反条款：`experiments/WORK-566/preregistration.md:170`「卖出日停牌 → 卖出顺延，**按最后可得收盘价**盯市」
- **审查实测证据**：`/tmp/fb/wtb_fb.py` 插桩副本（fmb / seed42 / c=0.0005，只读复现出与已提交产物一致的 net_ar 0.355194、net_sr 1.300085）
  - 估值回退分支触发 **188 次**，涉及 53 个 (stock, day) 对、29 个估值日、2 只股票；
  - **188/188 全部使用了估值日之后的价格**，未来 bar 相对估值日的日历滞后 min 15 / max 43 / 均值 28；
  - 换成「估值日之前最后一个可得 bar」重跑：net_ar 0.355194 → **0.355198**、net_sr 1.300085 → **1.299556**、MDD −0.200826 不变。
- **为何重要**：
  1. 位于产出**每一个头条数字**的代码路径上；
  2. 泄漏会**传播进交易决策**——下一批次预算是 `budget = min(V_t/H_HOLD, max(cash,0)/(1+cost))`，其中 `V_t` 就是被未来价格污染过的 NAV；
  3. 审计侧复刻了同一套回退 → 独立复算**结构上不可能发现**这一类错误。

**本单整改（已落码）**：
- `Panel.last_bar` 明确为**仅供退市判定**（返回全局最后 bar）；新增「估值日之前最后一个可得 bar」路径：
  `run_backtest` 预计算 `last_before[s, i] = max{ bar_idx ≤ i }`，估值回退改用它（`scripts/work566_backtest.py` 估值段）。
- 审计侧独立重写同一逻辑（`audit/recompute_work566.py`，仍不 import 建模函数）。
- **修后实测**（fmb / seed42 / c=0.0005）：net_ar **0.349456**、net_sr **1.278589**、MDD **−0.200884**、
  RankIC 9.3776（不变）、涨停剔除 170 / 停牌剔除 0 / 顺延卖出 1161 / 退市清算 31（全部不变）。

---

## 2. NON-BLOCKING（11 项）与整改

| # | 问题 | 审查量化 | 本单处置 | 状态 |
|---|---|---|---|---|
| **N1** | **卖出端交易成本未计**：`work566_backtest.py:164` 买端计费、`:191` 卖端 `cash += u*p` 无成本；审计 `:160` / `:187` 同构。预注册 `:133` 明写「**买入与卖出都收**（含最终清算）」 | fmb seed42：c=0.0005 net_sr 1.300085→**1.279121**；c=0.0015 → 1.195082；c=0.0030 → **1.068994**。净 SR 被高估 0.02–0.13 | 卖端与**最终清算**均补计 `cash -= abs(u)*p*cost`（建模侧与审计侧同步） | ✅ 已修 |
| **N2** | **FastEncoderLayer 训练模式与官方 torch 不等价**：用了 4 次 dropout 且**缺 FF 隐层 dropout**，有效 dropout 率 0.19 vs 官方 0.10。审查实测 train 模式 max\|diff\| = **1.048** | 见 §3 专项 | 按 torch 2.15 源码逐位复刻官方布局与 4 次 dropout 的调用顺序；修后 train 模式 max\|diff\| = **0.000e+00** | ✅ 已修（训练重启） |
| **N3** | **停牌日价格未前向填充**，与 `build_panel.py:13` docstring、`:491` 写入的 `meta.json` 的 `suspended` 字段、`preregistration.md:77`、`feature_contract.md:21` 三处声明不符（实测停牌格 close 全 NaN） | 实测影响 0（连续性断言已剔除停牌格，样本对中 `has_bar=False` 数量为 0） | **不重建面板**（会改 sha 并使已跑先验/训练失效）；改为修正脚本文案 + meta.json 字段描述，并在报告偏离表披露 | ✅ 已处置（见 §4） |
| **N4** | 审计 `calendar_crosscheck` 用**下标**比日期，恒为 0 命中；修复只在**未提交工作区**。`79ed047` 提交信息声称含该修复，但 `git show --stat 79ed047` 只有 2 个文件 | HEAD 实测命中 0/578 | 审计修复随本次整改一并提交；**不改写 `79ed047` 历史**（force push 属破坏性操作），改以本文件 + failure_log 订正其提交信息 | ✅ 已修（提交信息差异已记档） |
| **N5** | 读出推理路径 `predict_all_arms:654-655` 缺 `clamp(-8,8)`，训练侧 `take():583` 有 → 训练/推理口径不一致 | `mlp_std` 只用于 r0–r3 对照臂，当时无产物 | 推理路径补 `xb.clamp(-8.0, 8.0)` | ✅ 已修 |
| **N6** | H1 未强制「≥2 个种子」；预注册失败判据 3「**两个种子均** ≤ +1.0 → 判退」未实现（现按中位数） | — | 主检验加两条守卫：有效种子 < 2 → 判词「未交付（有效种子 < 2）」；两个种子 ΔRankIC 均 ≤ +1.0 → 判退；另加全局「种子守卫」标出只有 1 个种子的臂×成本档 | ✅ 已修 |
| **N7** | 预注册 `:136`「NAV ≤ 0 → 终止并报告账户失败」无守卫 | 实测 min NAV = 0.877023，不触发 | 建模侧估值后 `assert` NAV > 0；审计侧独立加同样守卫 | ✅ 已修 |
| **N8** | 退市清算触发日晚 1 日（prereg `:171` 字面是「自其**最后一个有 bar 的交易日起**」） | 金额口径一致，仅时点差 1 日 | **不改实现**：在 `lb` 当日无法知道它是最后一个 bar，按 `lb` 收盘价在**其后首个交易日**清算才是因果口径；作为口径对齐项在报告偏离表披露 | ✅ 已披露（不改码） |
| **N9** | 断言证据只证明**注入分支** raise，未证明引擎级守卫存在（引擎正常路径是静默跳过 + 顺延） | — | 新增**引擎级守卫** `assert_fill_legal()`：成交日无 bar / 价格非正 / 越出涨跌停带 → 抛 AssertionError；`--assert-violation` 改为在首笔成交处**改写该笔原始成交价**，由引擎自身的守卫报错中止 | ✅ 已修（见 §3 证据） |
| **N10** | HEAD 上主检验 / 独立复算 / 报告产物全部缺失；训练臂无预测产物 → 已落盘数字只属 FMB 基线臂 | — | 由 Stage 3 全链重建（9 臂 × 2 种子 × 3 成本档 + 主检验 + 复算 + 报告 + 渲染 + manifest） | ✅ 由 Stage 3 收口 |
| **N11** | `data/WORK-566/ret/{r1,r5,r21,r63,r126}.npy` 5 个 legacy 文件**全为 NaN**（有限值占比 0.0000%），与实际使用的 `r_*.npy` 命名相近，易被误取 | — | legacy 文件改名为 `.legacy_unused` 后缀（代码路径无法再按原名加载），并在 failure_log 记档 | ✅ 已处置 |

---

## 3. 两项重点整改的技术依据与证据

### 3.1 N2：S2 编码器训练期 dropout 不等价（根因 + 修法 + 证据）

**审查给的结论**：「官方 `nn.TransformerEncoderLayer(norm_first=True)` 的 dropout 共 **3** 次」。
本单按 **torch 2.15 安装源码**复核后修正为 **4 次**（审查这一处漏算），理由：

- `torch/nn/modules/transformer.py:961-977` — `_sa_block` = `self_attn(..., need_weights=False)` → `self.dropout1(x)`；
- `torch/nn/modules/transformer.py:980-982` — `_ff_block` = `linear2(dropout(activation(linear1(x))))` → `dropout2(x)`；
- `torch/nn/functional.py:7151` — `need_weights=False` 分支调用
  `scaled_dot_product_attention(q, k, v, attn_mask, dropout_p, is_causal)`，
  **训练期对注意力权重也施加一次 dropout**（`dropout_p = self.dropout.p`）。

→ 官方训练期 dropout 顺序固定为 ④ 次：
① 注意力权重（SDPA 内）→ ② `dropout1`（attn 输出）→ ③ FF 隐层 → ④ `dropout2`（FF 输出）。

**旧实现的两个错误**：① 位置错位（attn 输出 2 次、缺 FF 隐层、FF 输出 2 次）；② **内存布局不同**——
官方在 `MHA(batch_first=True)` 内部把 `(B,T,E)` 转置成 `(T,B,E)` 再做
`in_proj → chunk → reshape(T,B*H,dh) → transpose(0,1) → view(B,H,T,dh)`，
而旧实现走的是 `(B,L,H,dh) → transpose` 另一套 stride。CPU 上 dropout 掩码由
「张量形状 + 内存布局 + RNG 偏移」共同决定 → 掩码不同 → train 模式 max\|diff\| = 1.048。

**修法与证据**（`scripts/work566_train.py` `FastEncoderLayer.forward`）：

- 逐位复刻官方布局与 4 次 dropout 的调用顺序；
- 自检扩展到 **train 模式**（同一 seed=1234）：
  - eval：max\|diff\| = **4.768e-07**（float32 舍入量级）
  - train：max\|diff\| = **0.000e+00**（**逐位一致**）
- 旧实现训练出的 checkpoint 已移入 `data/WORK-566/stale/ckpt_seed42_rep.wrong_dropout.pt` 作废，
  **不参与任何交付数字**；训练已于 2026-11-25 13:33 用修复后的实现重启。

### 3.2 N9：断言证据改为「引擎自身守卫触发」

- 新增引擎级守卫 `assert_fill_legal(panel, s, day, side)`（建模侧）：
  ① 成交日必须有 bar；② 原始成交价必须为正且有限；③ 必须落在当日 `[down_limit, up_limit]` 带内；
  违反 → `raise AssertionError`（不是静默跳过、不是顺延）。
- 落地前先做**全窗口可行性扫描**（避免守卫在真实数据上误伤）：
  测试窗 578 个交易日 × 5,783 只，涨跌停带有效格点 **3,117,261** 个，
  `open_raw` 越上界 **0** 例、越下界 **0** 例、无价 **0** 例；`close_raw` 同为 **0** 例 → 守卫可严格启用。
- `--assert-violation` 改为：在**首笔成交处**把该笔的原始成交价改写为涨停价的 1.5 倍，
  随后由**引擎自身的守卫**抛错中止（告警原文见 `experiments/WORK-566/assert_violation_log.txt`，
  traceback 落在 `assert_fill_legal` 内部，而不是注入块自己 raise）。

### 3.3 B1 + N1 修后实测（fmb / seed42 / c=0.0005，与审查员 A/B 对齐）

| 指标 | 修复前 | 审查员 A/B 预期 | 本单修复后 |
|---|---|---|---|
| net_ar | 0.355194 | 0.349455（仅 N1） | **0.349456** |
| net_sr | 1.300085 | 1.279121（仅 N1） | **1.278589** |
| MDD | −0.200826 | — | **−0.200884** |
| RankIC(%) | 9.377620 | 不变 | **9.377620** |
| 涨停剔除 / 停牌剔除 / 顺延卖出 / 退市清算 | 170 / 0 / 1161 / 31 | 不变 | **170 / 0 / 1161 / 31** |

---

## 4. 预注册逐条偏差表（审查原文 37 条，摘要）

| 类别 | 条数 | 结论 |
|---|---|---|
| OK（实现与预注册一致） | 23 | 切分与 purge、分块拟合池、标签前视与连续性断言、K=30 等权 long-only、不足 30 整日跳过、预算公式、turnover 定义、Net AR/SR/MDD/RankIC 定义、三档成本、涨停剔除与顺延补位、跌停顺延、停牌顺延、退市 1% 清算金额口径、降级配置逐项、种子 {42,2024}、H1 阈值、H2 bootstrap 参数、H3 CI 判据、H4 只报数、失败判据 4（prereg 早于首个回测产物）、审计不 import 建模函数 |
| deviation | 9 | #3 停牌未前向填充（N3，影响 0）、#9 卖端成本未计（**N1，已修**）、#11 NAV≤0 无守卫（**N7，已修**）、#19 盯市用全局最后 bar（**B1，已修**）、#21 断言只覆盖注入路径（**N9，已修**）、#25 单种子未强制（**N6，已修**）、#27 失败判据 3 未实现（**N6，已修**）、#35 推理缺 clamp（**N5，已修**）、#36 FastEncoderLayer 等价声明不准确（**N2，已修**） |
| not verifiable | 3 | #31 失败判据 1 无自动检查（以 `panel_audit.json` 人工口径判定）、#37 论文对照表数值无法回溯仓库内原文（论文原文在本单 `work/paper/paper.txt`，已逐项核对 Table 1/3/4/5）、#4 股票池未与 quantdb 原始 `daily` 独立复核 |

## 5. 审查 VERIFIED CORRECT 摘要

特征后视与标签前视分离成立（`r_1[t] = logC[t] − logC[t−1]` maxdiff 2.34e-07；
`R21[t] = logC[t+21] − logC[t]` maxdiff 0.0）；目标列顺序 `['y21','R5','V5','M5','E5','R21','V21','M21','E21']`；
切分两侧均 purge，测试期 578 个预测日日历下标连续；组合记账不凭空增减资金（min NAV 0.877023 > 0、无负现金）；
指标定义逐条相符；审计独立性成立（只 import 标准库 + numpy）；审计 vs 建模 NAV max\|diff\| 4.44e-15；
A 股约束行为两引擎计数完全相同；`exit_day`/`exit_pos` 统一用「预测日序列位置」，无日历下标与数组位置混用；
并列 tie-break 用稳定排序，符合论文「Ties are resolved by asset order in the prediction file」；
涨跌停比价单位正确（raw vs raw，无 hfq/raw 混比）；失败判据 4 未触发（prereg 11:15:50 早于首个回测产物 12:51:54）。

## 6. 审查 NOT COVERED（本单补做情况）

| 未验证项 | 本单处置 |
|---|---|
| 论文原文逐字核对 | 已做：`work/paper/paper.txt`（2069 行）逐项核对 Table 1/3/4/5 与附录 B/C/D/E/F/G/H、Eq.42/43 |
| H1–H4 判词、审计 PASS 计数、报告成品 | 由 Stage 3 全链产出（本次整改后重跑） |
| 训练臂 `full`/`s1_s2`/`s1_only`/`r0`–`r3` 的任何结果 | 本单重跑训练（含 N2 修复）后产出 |
| N2 对最终模型指标的实际影响 | 本单直接修码重训（而非披露），最终数字全部来自修复后的模型 |
| 40 维特征自定部分与论文的可比性 | 已在 `feature_contract.md` 自陈「本单自定、与论文不可比」，报告只判相对增量 |
| 掩码组逐字段实现细节 | 掩码 token 存在性已核；逐字段细节仍未逐条核（报告列为已知限制） |
| 股票池与 quantdb 原始 `daily` 的独立复核 | Stage 0 端点复测已覆盖库内 17 端点；面板 5,783 只与论文 5,669 只差 +2.0%（盈余非缺口） |
| 两引擎在 B1 修复后的全量一致性 | 本单整改后由独立复算重跑核对（判据 ≤1e-6） |

---

## 7. 整改后的交付口径声明

- 本次整改**未改动预注册任何口径**：B1/N1/N2/N5/N6/N7/N9 全部是让实现回到预注册已写明的条款；
- N3/N8 以**披露**方式处理（改文案 + 报告偏离表），不改动任何数字口径；
- 预注册 commit（`16b24fb` v1.0 / `7580a3d` v1.1）仍**早于**所有回测产物 commit，次序成立；
- 旧实现训练出的 checkpoint 与旧引擎产出的数字**一律作废**，不进入任何交付表。
