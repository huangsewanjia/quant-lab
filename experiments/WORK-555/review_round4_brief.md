# WORK-555 · 第四轮独立复审交办书（产物轮）

本文件是第四轮独立审查的交办书。审查方是**独立子代理**，主循环不参与判决。

---

## 0. 审查对象与边界

- **仓库**：`/srv/share/quantdb/code/quant-lab`，分支 `agent/work-555-dee`。
- **审查范围**：`9bbb1f1..HEAD`（本轮代码与证据链改动）+ **最终产物 commit**（产物落地后补入）。
  第三轮审查（`experiments/WORK-555/review_round3.json`）覆盖到 `7de12b6`，本轮要覆盖其后的一切。
- **前置轮次留痕**：`experiments/WORK-555/review_round1.json`、`review_round2.json`、`review_round3.json`
  （三轮判决均为 FAIL）。**重点复核第三轮的 B1/B2/B3 三项 blocking 是否真的关闭**，以及
  第二轮 7 项与第一轮 9 项处置是否仍然有效（修复可能被后续改动破坏）。
- **写盘禁令**：审查方**不得写入被审仓库**。所有脚本副本、日志、沙箱产物写到仓库外的
  临时目录（`/srv/data` 或 `/tmp` 下自建；`/tmp` 空间有限，产物大的场景用 `/srv/data`）。
  审查开始前先记录仓库工作区基线（`git status --porcelain` + `git rev-parse HEAD`），
  结束时复核仓库未被改动。

## 1. 必查项：第三轮 B1/B2/B3 的复核

### B1（blocking）审计产物与被审计产物同代
- **当时的问题**：提交的 `audit_findings.json` 比它审计的产物旧一代；主检验量复算值与建模侧
  符号相反却被写成「逐位一致」。
- **复核方法**：
  1. 读 `experiments/WORK-555/audit_findings.json` 的 `generated_at` 与
     `artifact_manifest_sha256`；
  2. 实测 `sha256sum experiments/WORK-555/artifact_manifest.json`，与上面记录逐位比对；
  3. **独立重跑审计**：`/tmp/w555venv/bin/python audit/recompute_work555.py`
     （会覆写 `audit_findings.json` —— **必须在沙箱副本里跑**，或跑完用 `git checkout` 复原），
     看判词与项数是否与提交的一致；
  4. 比对 `audit_findings.json` 里 `primary_test_recomputed.delta_ic_mean` 与
     `final_numbers.json` 里 `primary.delta_ic_mean`，看相对差；
  5. 检查 `scripts/work555_final_numbers.py --check` 与 `scripts/work555_selfcheck.sh`
     是否真的会因为「审计陈旧」而失败（**做负控**：把审计记录里的 `artifact_manifest_sha256`
     改成一个假值，确认 selfcheck 报 FAIL）。
- **判据**：审计必须与被审计产物同代；两者不一致时措辞必须如实；陈旧审计必须被门禁挡住。

### B2（blocking）交付件内部数字互相矛盾
- **当时的问题**：`report.html` 判词 −27.05% vs 翻案条件段 +89.76%；registry 33.4% vs
  failure_log ~39%；registry「— 棵树」vs failure_log「1 棵树」。
- **复核方法**：把以下文件里的**同一个量**逐处对齐，任何一处对不上即算 finding：
  - `experiments/WORK-555/report.html`
  - `experiments/WORK-555/failure_log.md`（注意 §12.2/§12.3/§12.4/§13 是
    `work555_final_numbers.py` 的生成块，内容应与 `final_numbers.json` 一致）
  - `experiments/registry.md` 的 WORK-555 行
  - `experiments/WORK-555/final_numbers.json`
  - `experiments/WORK-555/audit_findings.json`
  - `experiments/WORK-555/run_*.json`、`backtest_*.json`
  重点量：Δ_ic 逐种子与均值、CI、DEE/MLP/XGBoost 的 RankIC、净 Sharpe(rf=0, 8bps)、
  年化、MDD、日均双边换手、年化摩擦、Δ_ic 的两处表述、年化换手与日均换手是否混写、
  XGBoost 实际用了几棵树。
- **建议做法**：写一个一次性脚本把这些量抽出来排序比对（放仓库外），别靠肉眼。

### B3（blocking）自证门禁对未关闭 blocking 盲视
- **当时的问题**：238 项 blocking 未关闭而 selfcheck 报 8/8 PASS。
- **复核方法**：**做负控** —— 在沙箱副本里制造一个「审计存在 blocking」的状态
  （例如把 `audit_findings.json` 的 `n_blocking` 改成非 0，或把某条 finding 的
  severity 改成 blocking），确认 `scripts/work555_selfcheck.sh` 退出码非 0 且打印 FAIL。
  再确认在**正常状态**下它退出码为 0。
- **判据**：门禁必须真的会红。

## 2. 本轮新增的实质改动（需要独立判断是否成立）

### 2.1 产物 float64 落盘（`a03db1c`）
- **背景**：收口审计剩最后 1 项 blocking（`ic::dee_s43::test_rankic_mean`，相对差 2.526e-06
  > 预注册 §7.2 的 1e-6 判据）。
- **改动**：`scripts/work555_dee.py` 与 `scripts/work555_xgb_diag.py` 把
  `pred` / `per_expert_pred` / `expert_valid_rankic` 由 float32 改 float64 落盘；
  标签 `y` 保持 float32（内存中本就是 float32，无损失）。
  **训练轨迹与指标计算完全未动**。
- **审查方要独立回答的问题**：
  1. 这是不是「放宽阈值 / 降级检查 / 删断言」式的绕过？预注册 §7.2 要求
     「以复算值为准，回退到对应阶段重做」——本处置是否符合？
  2. 建模侧数值是否真的没变？（对照 `experiments/WORK-555/rerun_v2_reference.json`
     与 §13 生成块：新口径数值应与上一轮逐位相同，只是产物精度变了。）
  3. 建模侧函数与审计侧函数是否真的一致？
     用 `scripts/work555_float32_quant_diag.py` 的思路独立复算一遍：
     把 `work555_dee.rank_ic_by_day` 与 `recompute_work555.daily_ic` 指向同一份 npz，
     看是否逐位相等。
  4. float64 落盘后，审计复算值与建模侧报告值的相对差是否真的变成 0？

### 2.2 不可成交断言全 8 组合实测（`9bbb1f1`）
- `scripts/work555_nomask_all.py`：4 臂 × 2 种子逐组真跑 `--no-mask`，记录退出码 +
  命中的 `UNTRADABLE_PRICE_READ` 原文 + 每组 stderr 的 sha256，落盘
  `experiments/WORK-555/nomask_assert.json`；任一组合未触发即非 0 退出。
- 复核：读 `nomask_assert.json`，确认 8/8 触发且退出码非 0；
  **独立复跑至少两组**验证；确认交付评论里的这张表是**从产物读**的，不是叙述性断言。

### 2.3 对外数字唯一真源
- `scripts/work555_final_numbers.py`（生成块 + `--check`）、
  `scripts/work555_artifact_manifest.py`（产物 sha256 清单 + `--check`）、
  `scripts/work555_float32_quant_diag.py`（精度门禁）。
- 复核：`--check` 全部退出 0；**做负控**：改一个数字看 `--check` 是否报错。

## 3. 交付件与可复现性

- `experiments/WORK-555/report.html`（单文件、内嵌 base64 图、无外链、断网可开）
- `experiments/WORK-555/report_print.pdf`、`report_firstscreen_1440x900.png`
- `figures/WORK-555/fig1..fig4*.png`
- 快照 6 份 sha256：`experiments/WORK-555/snapshot_manifest.json`
- 产物清单 43 份：`experiments/WORK-555/artifact_manifest.json`
- 字节级复现：`experiments/WORK-555/repro_check.json`（沙箱重跑 vs 仓库产物逐份 sha256）
- **复核字节级复现**：看 `repro_check.json` 的每份 sha256 是否真的一致；
  如需自验，可在 `/srv/data` 下自建沙箱（拷 `scripts/` + 软链 `sample.npz`、
  `sample_meta.json`）重跑一个臂做对照。

## 4. 必须独立重算、不得复用建模函数的部分

预注册 §7 判据 2 要求「以复算值为准」。审查方应**自带一套从头写的复算实现**
（不 import `scripts/work555_dee.py` / `scripts/work555_backtest.py`），
至少覆盖：
1. 逐日截面 Spearman RankIC 与 Pearson IC（DEE / MLP-only 两臂 × 两种子），
   与 `run_*.json` 的 `test_rankic_mean` / `test_ic_mean` 比对；
2. 组合账：选股（top-50 等权）、不可成交掩码（涨停不可买、跌停/停牌不可卖、强制持有）、
   **现金账本**口径的调仓与权重、成本（单边 8bps + 卖出印花税 10bps）、
   日频净收益序列、年化（365.25 自然日）/ 波动（√252）/ Sharpe(rf=0) / MDD / 双边换手，
   与 `backtest_*.json` 比对；
3. 权重不变量：Σw ≤ 1、Σw + cash ≡ 1。

## 5. 判词（预注册 §6.2，冻结）

主检验量 `Δ_ic = (RankIC_DEE − RankIC_MLP-only) / RankIC_MLP-only`（同种子）：
- 进带 **[+2%, +30%]** → 「进」；
- `Δ_ic ≤ 0` → 「退」；
- `Δ_ic > +50%` → 「退」（基线不可比）；
- **DEE 净 Sharpe(rf=0, 8bps) < 0 → 直接退**；
- 两种子 RankIC 极差 > 0.01 须明说不稳定；
- 95% CI 含 0 → 记「未复现」但仍算交付；
- 必须 ≥2 种子，报 mean ± range。

审查方**不要重下判词**，而是核实交付件里的判词是否与产物一致、是否按上述规则机械得出。

## 6. 输出要求

请按下列结构给出**分级**结论（blocking / suggestion / nit），每条都要带
**实测命令 + 实测输出 + 文件:行号**：

1. **判决**：`PASS` / `FAIL`（有 blocking 即 FAIL）
2. **逐项分级清单**：编号、级别、位置（`文件:行号`）、问题、**你实测的证据**、建议处置
3. **B1/B2/B3 复核结论**：每项写「已关闭 / 未关闭 / 部分关闭」并附证据
4. **独立复算结论**：你自写的复算与建模侧的逐项比对结果（PASS/FAIL + 项数 + blocking 数）
5. **未取到 / 未覆盖**：明确写出你**没能核实的部分**，不要用「通过」代替「没查」

## 7. 纪律

- 不修改被审仓库的任何文件（含 `experiments/` 下的产物）；需要跑脚本就把仓库**拷到沙箱**再跑。
- 沙箱用 `/srv/data`（空间充足）；`/tmp` 仅约 1.7 GB。
- 每条 finding 都要有你自己跑出来的证据，不能引用主循环的叙述。
- 没查到就写「未取到」，不要推断补全。
