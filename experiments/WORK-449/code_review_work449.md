# WORK-449 独立代码审查报告

> 审查对象：`quant-lab` 分支 `agent/work-449`（远程 PR #1 `origin/agent/work-449` = `fd5c7a4`；本地 HEAD = `4fc7c50`）
> 审查人：Verifier（独立第三方，非作者）　审查日期：2026-11-20　协议依据：`experiments/WORK-449/preregistration.md` + v1.1 / v1.2 / v1.3 / v2.0
> 本次审查**未修改任何项目文件**；唯一写入为本文件与 `/tmp/w449_review/` 下的临时脚本。
> （重跑 `audit/recompute_work449.py` 会覆盖 `experiments/WORK-449/audit_findings.json`；我已用备份把该文件**按 sha256 原样还原**：`7c0a7f6…5084`，66 项比较与作者版本逐项相同，仅 `generated_at` 不同。）

### 审查时序说明（重要）

审查期间（2026-11-20 12:35 → 12:45）**作者在同一工作区并行改动**，我的检查是对一个**移动靶**做的。每条发现都锚定到具体时点：

| 时点 | 状态 |
|---|---|
| 12:35–12:42 | HEAD = `4fc7c50`；工作区有 4 个未提交改动（`failure_log.md`、`registry.md`、`work449_report.py`、`report_template_WORK-449.html`）；产物未入库 |
| 12:43 | 作者新增未提交改动：`work449_train.py` 与 `work449_backtest.py` 新增 `--seed` / `--tag` 参数（默认仍保持协议冻结的文件名）；生成 `experiments/WORK-449/model_diagnostic.json`（12:43:48）；三臂 **seed 43** 复跑进行中（`train_progress_*_s43.json`） |
| 12:45 | `report_data.json` **未**重新生成，报告仍显示「`model_diagnostic.json` 未生成」占位 |

因此：**发现 1（PR 落后 2 个 commit）、发现 2、发现 3 在 12:45 时点上依然成立**；发现 6 的「诊断产物未生成」已被作者补上，但**其三项不可复现的数字仍无产物支撑**（新产物里既没有 OLS 基线，也没有常数基线 —— 见发现 6 更新）。

---

## 结论

**能否进入研究备忘录：有条件。**

研究结论本身站得住：主检验量 ΔSharpe = **+0.5516438983949656**、95% CI **[+0.0780049578498886, +1.0341699638100690]**，
我用自己的实现从 `metrics_*.json` 逐位复现（差异 0.0），按协议 §9.4 判「**未复现（退）**」正确；
独立复算脚本重跑得 blocking=0 / 66 项比较，与作者记录完全一致；掩码三档语义、窗口断言、前视纪律均按协议字面实现且被独立验证。

**但这份变更现在不能合并**：远程 PR #1 落后本地 2 个 commit（`ca86db9` + `4fc7c50`），
按 PR 现状跑会得到**崩溃的审计脚本**、**与三臂上报 Sharpe 对不上的主检验量**、**错误模板的报告**，
以及**已被 v1.3 取代的训练超参协议**。同时退出门槛第 6 条被报成「通过」，而实测值在两种口径下都超过协议阈值。
这两类问题修完（发现 1–6）即可进入研究备忘录；判词「未复现」与 §11.1 的能力限制结论不受影响。

---

## 审计对象

| 项 | 值 |
|---|---|
| 被审 commit | 本地 HEAD `4fc7c5028e881619b9017176a93886aef3a688aa`（含 4 个未提交改动：`failure_log.md`、`registry.md`、`scripts/work449_report.py`、`templates/report_template_WORK-449.html`）；远程 PR 头 `fd5c7a471c65cbc62a5a8a0daef519c66ff2c65a` |
| 协议链 | `522ecf9`(v1.0) → `dfa7372`(v2.0) → `e434508`(v1.1/v1.2) → `ca86db9`(v1.3) —— 提交时间 09:51 ~ 11:56，均早于任何回测产物（**注意：回测产物根本没有 commit**，见发现 4） |
| 数据快照 | `experiments/WORK-449/snapshot_manifest.json`（680 只 × 1,575,502 行，AKShare/东财 hfq + 不复权双调用，680/680 成功），原始快照 `data/WORK-449/*.csv` 不入库（`.gitignore`） |
| 面板 | 稠密网格 1,620,295 格（源行 1,575,502 / 停牌 44,793 / 未上市或退市 199,385）；at_limit 29,303；复权伪跳变 822；ST 格 5,649；功效检查 mask_rate **1.9077% ≥ 0.50% → 锁定 CSI300**（协议 §2.2） |
| 样本窗 | 训练 2015–2020 / 验证 2021（逐日 RankIC 选 epoch）/ 测试 2022-01-04 ~ 2024-12-31，**726 个样本外交易日**；年化分母 1092 自然日；vol √252；rf 1.5%；成本 8 bps 双边（另 5 bps 敏感性） |
| 主检验 | `experiments/WORK-449/primary_test.json`（2026-11-20T12:13:56） |
| 实测结论 | ΔSharpe **+0.5516**、CI **[+0.0780, +1.0342]**（不含 0）、ΔIC **−0.0044**、判词**未复现（退）** |

### 我实际跑过的质量门

| 命令 | 结果 |
|---|---|
| `OMP_NUM_THREADS=1 ../.venv/bin/python scripts/work449_test_primitives.py` | **failures=0**；6 原语 × 3 窗口 × 5 组面板，掩码不一致 0 处，值最大相对差 1.1e-13（ts_rank 5.7e-8，官方 float32 精度） |
| `OMP_NUM_THREADS=1 ../.venv/bin/python scripts/work449_test_covariance.py` | **failures=0**；Ledoit-Wolf 对 sklearn `LedoitWolf` 7 组 (n,t) 全中，δ 逐位一致，`max_rel_diff ≤ 3.41e-16` |
| `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 ../.venv/bin/python audit/recompute_work449.py --exp WORK-449` | **blocking=0，suggestion=6，comparisons=66**，与 `audit_findings.json` 完全一致（重跑后已把该文件还原为原版，sha256 未变） |
| 独立复算主检验（我从 `metrics_*.json` 重写一遍 bootstrap） | ΔSharpe 与 CI **逐位相等**（diff = 0.0），两档成本皆然 |
| 独立复算 §11.1 模型能力数字 | 见发现 5 |
| 求解器口径实质性探针（我自写，6 个抽样日重解） | 见发现 1 与 FYI-1 |

**未跑**：三臂完整回测（约 45 min/臂，按任务要求不重跑）、`work449_model_diagnostic.py`（会写 `model_diagnostic.json`，会改项目文件）。
因此「权重本身能否从零重算出来」未验证，见「未确认项」。

---

## 发现表

### 发现 1 · **Critical** · 远程 PR #1 落后 2 个 commit，按现状无法复现交付结果

- **位置**：`origin/agent/work-449` = `fd5c7a4`；`git log origin/agent/work-449..HEAD` → `ca86db9`、`4fc7c50`
- **触发条件与后果**：任何人 clone PR #1 并按 `preregistration.md` §11 的固定顺序复跑，会遇到四个问题：
  1. `audit/recompute_work449.py`（PR 版 `fd5c7a4:audit/recompute_work449.py:326`）仍是 `pi = np.array([int(k) for k in idx])` —— 扁平格号当行号用，`IndexError: index 77565 is out of bounds for axis 0 with size 2676`，**审计脚本直接崩溃**（作者在 failure_log §3.17 自认此缺陷，修复只在 `4fc7c50`）。
  2. `scripts/work449_primary_test.py`（PR 版第 63–64、72 行）仍用 `days = arms[...]["test_days"]`（**726 交易日**）做年化分母，而回测用 **1092 自然日** —— 主检验算出的 ΔSharpe **不等于** `Sharpe(no_mask) − Sharpe(full_mask)`，年化收益被高估约 1.5 倍（failure_log §3.16b）。
  3. PR 分支**没有** `preregistration_v1.3.md` —— 协议里写的仍是 Adam / lr 1e-3 / wd 1e-4 / batch 4096，而交付的模型是按 v1.3 的 AdamW / 5e-4 / 1e-5 / 8192 训的。
  4. PR 分支的模板文件名仍是 `report_template_work449.html`（小写 449），`build_report.py:50-57` 的 `_resolve_template` 找不到就**静默回落到默认模板** —— 会渲染出 WORK-404 的报告（failure_log §11.2 自认此缺陷）。
- **实测值与预期的差距**：PR 状态与交付状态的差距 = **2 个 commit / 5 个文件 / +482 行**。四项缺陷全部命中协议 §10 退出门槛 1（预注册先行）、5（独立复算一致）、6（求解器与训练可复现）。
- **可执行改法**：`git push origin agent/work-449`（PR 自动更新），并在 PR 描述里写明「PR 头 = `4fc7c50`」与产物生成时间戳。
- **验证命令**：`git log --oneline origin/agent/work-449..HEAD`；`git show fd5c7a4:audit/recompute_work449.py | grep -n "pi = "`；`git show fd5c7a4:scripts/work449_primary_test.py | grep -n "days ="`。

### 发现 2 · **必须改** · 退出门槛第 6 条报「通过」，但实测值在两种口径下都超 1e-6 阈值

- **位置**：`scripts/work449_report.py:735`（门槛行生成）；`experiments/WORK-449/report.html` 表 7 第 6 行；`experiments/WORK-449/failure_log.md:236-251`（§3.11）
- **触发条件与后果**：门槛 6 的判据是协议 §8/§10 的「QP 与 SLSQP 在 5 个抽样日上相对差 ≤1e-6」。报告的门限行写的是「求解器 **0.005**」+ 绿色「**通过**」标签 —— 证据格里摆着的就是**违反阈值的那个数**，自相矛盾。正文 §6 虽诚实披露了超阈，但把目标函数口径四舍五入成「**0.000**」，把「也超了 1e-6」这件事抹掉了。
- **实测值与阈值差距**（我逐条读 `metrics_*.json` 的 `solver_cross_check`）：

  | 臂 | 权重口径 `max_rel_diff`（阈值 1e-6） | 目标函数口径 `obj_rel_diff`（阈值 1e-6） | 超阈实例 |
  |---|---|---|---|
  | `no_mask` | 最大 **2.8637e-3**（= 阈值的 **2,864 倍**） | 最大 **1.2721851344898035e-06**（2023-12-18） | 权重 5/5 超；**目标函数 1/5 超** |
  | `halt_only` | 最大 **4.6869e-3**（= 阈值的 **4,687 倍**） | 最大 5.3662e-07 | 权重 5/5 超；目标函数 0/5 超 |
  | `full_mask` | 最大 **2.8297e-3**（= 阈值的 **2,830 倍**） | 最大 3.2932e-08 | 权重 5/5 超；目标函数 0/5 超 |

  即：**15 个抽样日实例里，权重口径 15/15 超阈；作者自己选的目标函数口径也有 1/15 超阈（1.2722e-6 vs 1e-6）。**
  另外 failure_log §3.11 的论证用的是 **1.75e-4 / 6.5e-9** —— 那是**作废轮次**（协方差有 bug 时）和**合成问题**上的数；最终 R2 轮次的权重差比它大 **27 倍**、目标函数差大 **195 倍**。写进报告的那套辩护**没有覆盖实际交付的这一轮**。
- **可执行改法**：① 门槛 6 改为「**未通过 / 有条件**」，写清两个口径的实测值（用科学计数法，不要 3 位小数）；② §3.11 补一条「最终轮次实测」，把 1.75e-4/6.5e-9 标成作废轮次数；③ 或者把求解器精度压到 1e-6 以内（收紧 `ftol`/`MAX_ITER`，并处理 flat-subspace 的病态性）后重跑 3 臂。
- **验证命令**：`../.venv/bin/python -c "import json;[print(a, json.load(open(f'experiments/WORK-449/metrics_{a}.json'))['solver_cross_check']) for a in ('no_mask','halt_only','full_mask')]"`
- **注**：这条不影响最终判词（见 FYI-1 的实质性量化）。

### 发现 3 · **必须改** · 门槛 6 的第二个判据（同 seed 重跑）零证据，且指引指向不存在的章节

- **位置**：`report.html` 表 7 第 6 行「同 seed 重跑见 §10」；报告 §10 标题为「独立复算与可复现性」，内容只有独立复算与门槛表，**没有任何重跑记录**
- **触发条件与后果**：协议 §10 门槛 6 是两条判据的合取：「QP 求解器与 SLSQP 在 5 个抽样日上相对差 ≤1e-6；**同 seed 重跑两次，指标一致（相对差 ≤1e-6）**」。第二条在全仓库无任何产物支持：`failure_log.md` 无重跑条目、无 rerun 目录、无第二份 metrics。报告却把它标成「通过」，读者按指引去 §10 找不到任何东西。
- **实测值与预期的差距**：预期 = 2 份同 seed 指标 + 相对差 ≤1e-6；实测 = **0 份**（缺失，而非超阈）。
- **可执行改法**：跑一次 `no_mask` 臂的**同 seed（42）** 重训 + 回测（训练约 3 min，回测约 45 min），把两份 `metrics_no_mask.json` 的相对差写进 `primary_test.json` 或独立文件；否则门槛 6 只写「求解器口径」一条并标未通过。
- **注意（12:43 时点）**：作者正在跑的是 **seed 43**（`train_progress_*_s43.json`，并为此给 `work449_train.py` / `work449_backtest.py` 加了 `--seed` / `--tag`）。**seed 43 测的是「换种子会怎样」（种子敏感性），不是协议写的「同 seed 重跑两次」（可复现性）** —— 两者不能互相顶替。字面判据要满足，需要 `--seed 42 --tag _r2` 再跑一遍并逐项比对；seed 43 的结果建议作为**附加稳健性证据**单列，不并入门槛 6 的达标证据。
- **验证命令**：`grep -rn "重跑\|rerun" experiments/WORK-449/failure_log.md experiments/WORK-449/report.html`（当前无命中）。

### 发现 4 · **必须改** · 报告生成器硬编码论文的背离形状，修复只在未提交的工作区

- **位置**：`scripts/work449_report.py:207-224`（HEAD 版本）、`templates/report_template_WORK-449.html:302`（HEAD 版本）
- **触发条件与后果**：HEAD 版的 `fig_divergence()` 无条件写死 `axes[0].text(1.0, ..., "IC rises\n(no_mask > full_mask)")` 与 `"Sharpe falls\n(mask hurts)"`，模板图注写死「图 3｜背离的形状：表观 IC 上升、可实现 Sharpe 下降。…论文报告的是同一形状」。而本轮实测方向**正好相反**：表观 IC 0.0077 < 0.0121（下降）、Sharpe −0.580 > −1.132（上升）。按 HEAD 版重出报告，会给一篇判词为「未复现（退）」的报告配上一张断言「IC 上升 / Sharpe 下降」的图 —— 直接把论文的主张画在自己的结果上。
- **实测值与预期的差距**：实测 IC 方向 = **下降**（ΔIC = −0.0044），Sharpe 方向 = **上升**（ΔSharpe = +0.5516）；HEAD 代码输出 = 「上升 / 下降」，**两个轴都反**。
- **现状**：工作区已修好（条件化的箭头 + `{{T_FIG3_SHAPE}}`），我看了渲染出的 `figures/WORK-449/fig3.png`，图上写的是「IC falls (no_mask < full_mask)」与「Sharpe rises (mask helps)」，**是对的**。但 `git diff` 显示这 33 + 7 行**未提交**。
- **可执行改法**：把 `scripts/work449_report.py` 与 `templates/report_template_WORK-449.html` 的改动一并 commit（与发现 1 的 push 一起）。
- **验证命令**：`git diff scripts/work449_report.py templates/report_template_WORK-449.html`；重跑 `scripts/work449_report.py --exp WORK-449 && scripts/build_report.py --exp WORK-449` 后目视首屏。

### 发现 5 · **必须改** · 结论产物全部未入库，PR 不可自证；门槛 1 在产物上无法核验

- **位置**：`git ls-files experiments/WORK-449/` 只返回 11 个文件（协议 5 份 + `panel_stats.json` + `factor_stats.json` + `pool_membership.json` + `snapshot_manifest.json` + `st_intervals.json` + `stock_basic.json` + `failure_log.md`）
- **触发条件与后果**：`metrics_{no_mask,halt_only,full_mask}.json`、`primary_test.json`、`audit_findings.json`、`metrics_table.csv`、`paper_comparison.md`、`report_data.json`、`train_log_*.json` 全部是 **untracked 且未被 gitignore**（`git check-ignore` 对每个都返回「not-ignored」）。后果有二：① PR 的接收方拿不到任何对外数字与审计输出，门槛 5（独立复算一致）无法在仓库内核验；② 协议 §10 门槛 1 要求「预注册 commit 时间早于**任何回测产物 commit** 时间」，而**回测产物根本没有 commit**，这条门槛在交付物上只能空转。
- **实测值与预期的差距**：预期 = 证据产物入库（体积很小：13–78 KB/个）；实测 = 入库 **0 个**，未跟踪 **13 个**。
- **可执行改法**：`git add experiments/WORK-449/*.json experiments/WORK-449/*.csv experiments/WORK-449/*.md`（`.gitignore` 已放行 `metrics_table.csv`；`*.npz`/`*.pt` 仍不入库，符合设计）后一并 push。
- **验证命令**：`git status --porcelain experiments/WORK-449/`；`git ls-files experiments/WORK-449/ | wc -l`。

### 发现 6 · **必须改** · §11.1 的模型能力数字无产物支撑，三项实测复现不出来

- **位置**：报告 §11.1 及其「表 6｜模型能力诊断」（表内自己写着「`model_diagnostic.json` **未生成**」）、`failure_log.md:338-363`（§3.18）、`experiments/registry.md` WORK-449 行 ⑤
- **触发条件与后果**：报告正文与登记表引用了四组精确数字，其中一组能复现、三组复现不出。**「未生成」的诊断表下面写着具体数值，读者无法核验。**
- **实测值与阈值的差距**（我从 `panel.npz` / `factors_no_mask.npz` / `pred_no_mask.npz` / `train_log_no_mask.json` 独立重算）：

  | 报告/日志声称 | 我的独立实测 | 差距 |
  |---|---|---|
  | MLP 预测截面标准差中位数 1.117e-04 | **1.1174e-04** | ✅ 一致 |
  | 离散度比 0.48% | 0.58%（全 725 个测试日）/ 0.48%（前 200 日，与日志口径同） | ✅ 口径一致 |
  | MLP 训练 AdjMSE 4.93e-04 | **4.9315e-04**（末轮）/ 4.8458e-04（最优轮） | ✅ 一致 |
  | **单因子测试段 RankIC「\|IC\| 0.038 ~ 0.058」**（说「12 个因子」） | 12 个因子的 \|IC\| 全距 **0.0005 ~ 0.0576**；**7 个因子 \|IC\| ≤ 0.0161**（mom_60 −0.0005、ts_rank_close_60 −0.0058、mom_20 −0.0089、mom_5 −0.0101、ts_mean_ret_20 −0.0105、ts_mean_ret_5 −0.0108、ts_corr −0.0161）；声称的 0.038~0.058 只覆盖 ts_std −0.0571、range_1 −0.0576、ts_max −0.0502、ts_min +0.0417、amt_proxy −0.0382 这 5 个 | ❌ **区间下界错**：真实下界 0.0005，不是 0.038（差 76 倍） |
  | **「12 因子 OLS 基线测试段 RankIC 0.01463（t = 3.35）」** | 池化 OLS（2015–2020 训练、2022–2024 测试、同一样本集）：RankIC **+0.01000（t = 2.48）**；改用 Pearson IC 则 **+0.01654（t = 4.06）** | ❌ **两种口径都复现不出 0.01463 / t = 3.35** |
  | **「最优常数预测器 AdjMSE = 4.32e-04」** | 训练段最优常数 c = −5e-5 → **4.7243e-04** | ❌ 差 **9.3%** |

  另外：**仓库里没有任何脚本产出后两项**。`scripts/work449_model_diagnostic.py` 只做 MLP/Linear × Adj/MSE 四格，**不算 OLS 基线、也不算常数基线**；登记表却把「单因子 |IC| 0.038~0.058、OLS 基线 0.0146」归因于该脚本。
- **对结论的影响**：**方向不变、程度被低估**。MLP 在自己的训练目标上仍差于最优常数（4.93e-04 > 4.72e-04），输入因子最强的 5 个 |IC| 仍达 0.038~0.058（|t| 5.2~7.8），远高于 MLP 的测试 RankIC 0.013 —— 「模型欠拟合」这一判断成立，且真实缺口比报告写的更大。但报告里那三组数字本身**不可复现**。
- **可执行改法**：① 跑 `scripts/work449_model_diagnostic.py` 并把 `model_diagnostic.json` 入库，让表 6 有内容；② 把 OLS 基线与常数基线加进该脚本（现在没有）；③ 把 §11.1/§3.18/登记表的数字改成可复现值，或标注「口径：RankIC / Pearson IC」。
- **12:43 更新**：作者已生成 `experiments/WORK-449/model_diagnostic.json`（12:43:48）。该产物含 4 格模型对比（`mlp+adj` 测试 RankIC +0.01657、`mlp+mse` −0.00169、`linear+adj` −0.02426、`linear+mse` −0.00958；`mlp+adj` 离散度比 0.0011），**但仍然没有 OLS 基线、也没有常数基线** —— 上面表里那三项复现不出的数字依然没有产物支撑；且 `report_data.json` 尚未重出，报告里仍是「未生成」占位。
  - 附带一条对作者有利的新证据：`linear+adj`（闭式线性 + AdjMSE）测试 RankIC **−0.024**，比 MLP 的 +0.0166 更差 → 进一步支持 §3.18「问题在优化没走完、不是模型类不合适」的判断。
- **验证命令**：`../.venv/bin/python scripts/work449_model_diagnostic.py --exp WORK-449`（约 10 min，会写 `model_diagnostic.json`）。

### 发现 7 · **Optional** · `stock_basic.json` 是无出处、无产出者的空对象

- **位置**：`experiments/WORK-449/stock_basic.json`（2 字节，内容 `{}`）；`scripts/work449_build_panel.py:157` 与 `audit/recompute_work449.py:214` 只**读**它
- **触发条件与后果**：`grep -rn "stock_basic" --include="*.py"` 在全仓库**找不到任何写入方** —— 这个文件是仓外产出的。读者打开它只看到 `{}`，无法区分「端点返回空」与「压根没取过」。对比同目录的 `st_intervals.json` 有 `_source` / `_scope_note` 两个说明字段，这里缺了。
- **实测值与预期的差距**：预期 = 文件自带出处与「IPO 退化为快照首行」的说明（预注册 v1.1 §3.2 已规定该退化）；实测 = 空对象、无说明、无生成脚本。
- **影响**：**功能上无风险** —— 建模侧与审计侧都用同一退化规则（首行日期），且两者独立重建出的掩码 0 格不一致。
- **可执行改法**：写成 `{"_note": "Tushare stock_basic 端点返回空；ipo_date 退化为快照首行（preregistration v1.1 §3.2）", "intervals": {}}`，并让 `work449_fetch.py` 负责落盘。
- **验证命令**：`grep -rn "stock_basic" --include="*.py" .`

### 发现 8 · **Optional** · block bootstrap 永远抽不到最后 6 个测试日，补偿因子未说明

- **位置**：`scripts/work449_primary_test.py:82-89`
- **触发条件与后果**：`nb = len(a) // BLOCK = 726 // 20 = 36`，块起点 `rng.integers(0, 36)` → 可达下标 0..719，**第 720~725 日（0.83%）任何一次重采样都抽不到**；年化用 `boot_days = cal_days × 720/726 = 1083.1` 补偿。对 CI 的影响可忽略，但协议 §9.3 未规定尾部处理，报告也没写。
- **实测值与预期的差距**：预期（若按「全部 726 日」的直觉）= 726 日可达；实测 = **720 日可达，6 日不可达**，补偿比 720/726 = 0.99173。
- **可执行改法**：在 §8 统计口径处加一句「块长 20 整除到 720 日，尾部 6 日不入样；年化跨度按 720/726 等比缩放」。
- **验证命令**：`../.venv/bin/python -c "print(726//20, (726//20)*20)"`

### 发现 9 · **Nit** · 「首日收益为 0」的说法与实测不符

- **位置**：`report.html` 图 2 图注「起点为现金、次日建仓，故首日收益为 0（三臂一致）」；`failure_log.md:396` §五.5
- **实测值**：三臂 `net_returns[0]` 全为 **−8.0e-04**（`gross_returns[0] = 0`，但建仓成本 8 bps 要扣）。净收益不是 0。
- **可执行改法**：改成「首日**毛**收益为 0、**净**收益为 −8 bps（建仓成本），三臂一致」。
- **验证命令**：`../.venv/bin/python -c "import json;print([json.load(open(f'experiments/WORK-449/metrics_{a}.json'))['main']['net_returns'][0] for a in ('no_mask','halt_only','full_mask')])"`

### 发现 10 · **Nit** · 死代码与一处「缺模型」的调度味

- `scripts/work449_backtest.py:36` `EPS_PROJ = 1e-14` —— 定义后**从未使用**。
- `scripts/work449_primary_test.py:74` `h = np.array(arms["halt_only"][...]["net_returns"])` —— 赋值后**从未使用**（`halt_only` 的 Sharpe 另有一路取用）。
- `scripts/work449_build_factors.py:238-260` —— 12 个因子靠 `kind` 字符串的 8 分支 if/elif 派发；`FACTOR_SPEC` 的 `span_days` 与 `kind`/`window` 是**两套必须手工保持一致**的事实（`shift_ratio` 取 w+1、滚动算子取 w），是典型的「缺一个显式模型」气味。建议把 span 并入 kind 的语义（`shift_ratio` 的 span = window + 1 由代码算出）。

### 发现 11 · **Optional（架构）** · 仓库里现在有两套「独立复算」实现

- `audit/audit_recompute.py`（既有，WORK-404 用）文件头就写着同一条契约「禁止 import 建模阶段（scripts/build_analysis.py 等）的任何函数」；`audit/recompute_work449.py`（本次新增）是同一思路的第二份独立实现，且 `experiments/registry.md` 自己记着「S2 的判定表未过 `audit_recompute.py` 独立复算 = 已知缺口」。
- **可执行改法**：把「快照 → 面板 → 收益 → 掩码 → 指标」的骨架抽成一个参数化审计器，判据按实验配置；或明确写清为何本单必须另起一份。
- **注**：这不是本次改动引入的回归（既有脚本早于本次改动），但本次改动新增了第二份同角色实现。

### FYI-1 · 「用目标函数相对差判读」这一口径**在实质上是站得住的**（但门槛行的写法不站得住）

作者把协议 §8/§10 未指明分母的「相对差 ≤1e-6」解释成**目标函数值相对差**，理由写在 failure_log §3.11。我的判断：**口径选择可接受，判据结论不可接受**。

- 支持该口径的证据（我的独立探针）：两个求解器确实落在**同一个最优点**上 —— 目标函数值一致到 3.3e-8 ~ 1.3e-6，而权重差最大 1.41e-4（单票上限 3% 的 0.47%）。平坦性论证是可量化的：α=10、Σ 近各向同性时 H = 2αΣ，ftol=1e-12 对应的位移 √(2·ftol/H) ≈ 1.5e-5，与实测 ~1e-5 位移同阶；而且本轮模型自身预测的截面离散度只有 1.12e-4（标签的 0.58%），μ̂ 相对风险梯度极小，目标函数更平。
- **实质性影响（我的探针，6 个抽样日重解）**：最差一天上两解的**次日毛收益贡献差**为 2.7e-7 ~ 4.1e-6，而当日毛收益本身是 2.8e-3 ~ 6.1e-3（≤0.15%）；**换手差** ≤2.8e-4，对当日双边换手 0.13~0.56（≤0.5%）。据此放大的 ΔSharpe 扰动 **≲0.02**，相对交付的 0.5516 **≤3.7%**。→ **求解器口径不可能翻掉判词。**
- 但门槛 6 仍应标为「有条件/未通过」：一是协议写死的字面判据（权重口径）超阈 2,830~4,687 倍；二是**作者自己选的口径在 15 个实例中有 1 个（no_mask / 2023-12-18，1.2722e-6）也超阈**；三是把 1.2722e-6 显示成「0.000」等于把超阈事实藏起来。
- **建议**：把口径选择写成协议修订 v1.4（而不是只写在失败日志里），两个口径都用科学计数法披露，并把那 1 个超阈实例单列。

### FYI-2 · ΔSharpe 里有相当一块是换手×成本，但报告没有量化这条机制

- 实测：日均双边换手 0.2563 / 0.1314 / 0.5618（no_mask / halt_only / full_mask），full_mask 是 no_mask 的 **2.19 倍**。8 bps 下 ΔSharpe = +0.5516，5 bps 下 = +0.4070 → **成本口径贡献了 0.1446（26%）**，其余 0.4070 来自毛收益；年化收益差 −4.40% vs −11.50%（7.1 pp）里约 2.0 pp 来自成本、5.2 pp 来自毛收益。
- 好消息：**两档成本下判词都是「未复现」**（5 bps 的 CI 含 0 但 ΔSharpe 仍为正），所以结论对成本假设**稳健**。建议在 §7/§8 明确写一句这条机制与占比，否则读者容易把 0.55 当成纯 alpha 差异。

### FYI-3 · 审计脚本的 `rel_diff` 在 expected = 0 时退化（作者已在登记表披露）

`audit/recompute_work449.py:42-48` 的 `rel_diff(a, b)` 以 `max(|a|,|b|,1e-12)` 为分母，`expected = 0` 时任何非零实测都会得到 1.0。后果：6 项因子值抽查的 `rel_diff` 恒为 1.0（真实实测值 5.6e-8 ~ 5.9e-8，仍 < 1e-6），且**掩码/面板类比较实际要求的是「精确相等」而不是「相对差 ≤1e-6」**（更严，不会假通过）。建议在 `rel_diff` 里对 expected == 0 走绝对差分支，并把这 6 项的实测绝对差打进 blocking 判定范围。

### FYI-4 · 年化用 1092（不含末日）而非 1093（含末日）自然日

`metrics()` 与 `calendar_days()` 都用 `end − start`，2022-01-04→2024-12-31 = 1092 天（含首尾应为 1093）。影响 0.09% 的年化收益，且**两侧口径完全一致**（`delta_sharpe_consistency_abs_err = 0.0`），不改结论。可作为口径备注补一句。

---

## 五轴小结

| 轴 | 结论 |
|---|---|
| **正确性** | 核心链路（面板 → 掩码 → 因子 → 训练 → 组合 → 指标 → 主检验 → 独立复算）**实现正确且按协议字面落地**，我逐段独立核过：① 掩码三档 = `has_row` / `has_row & ~suspended` / `M_exec`，与 §5.2 字面一致；② 窗口断言真实在跑（no_mask 5,517,361 > 0、halt_only 4,816,155 > 0、full_mask = 0），机制对两臂能触发、对 full_mask 结构性为 0，符合 §5.4 的三行期望；③ 前视纪律干净：`pred[t]` 是用 t 日因子对 r_{t+1} 的预测（`work449_train.py:261` 落在 t0）、回测 IC = corr(pred[t], r_eff[t+1])（`work449_backtest.py:320-325`）、w_{t−1} 承担 t 日收益（:296-299）、协方差只用 `r_eff[lo:t]`（:255-262）、选 epoch 只用 2021 验证段（`work449_train.py:225`）、归一化只用训练段（:164-173）；④ Ledoit-Wolf 确为 sklearn LW2004 的 β/δ（7 组 (n,t) δ 逐位一致，最大相对差 3.41e-16）；⑤ SLSQP 梯度对自由块解析正确（固定块只经 SigFB 进入）；⑥ 成本/换手/指标口径与协议一致（8 bps 双边、5 bps 敏感性、rf 1.5%、365.25 自然日 / √252、收盘 cummax、单双边换手），三臂走**同一个 `metrics()` 函数**，不可比风险为零。**缺陷集中在「口径门限的判定与呈现」与「交付打包」，不在算法实现**：门槛 6 被误判为通过（发现 2）、门槛 6 第二判据无证据（发现 3）、§11.1 三项数字不可复现（发现 6）。 |
| **可读性与简洁** | 好。协议引用、章节注释、模块 docstring 密度高且信息量大；三个脚本互不越界（审计脚本独立重写而非复用）。小瑕疵：因子派发是 8 分支 if/elif（发现 10）、两处死变量/死常量（发现 10）、`primary_test.py` 里 `h` 取了不用。整体未到「1000 行该拆」的程度，但 `scripts/work449_report.py` 已 782 行，再加图与表格逻辑建议按「数据层 / 渲染层」拆开。 |
| **架构** | 分层清楚：取数 → 面板 → 因子 → 训练 → 回测 → 主检验 / 审计 → 报告，各阶段用文件交接，审计与建模解耦（这是本单最值得称道的结构决策）。两处架构气味：① 两套独立复算实现并存（发现 11）；② 报告生成器把「结论方向」写死在图注里，而不是从数据派生（发现 4 的根因 —— 正确做法已在工作区实现，应推广为「方向文本一律由指标派生」）。 |
| **安全** | **无问题**。全仓库扫描无密钥/token/口令/PII；`failure_log.md:33` 只提到凭据**路径** `~/.config/multica/secrets/github.cred`，未落任何令牌材料；`127.0.0.1:8899` 是本机 CONNECT 代理，不是凭据。原始行情快照按 `.gitignore` 不入库（仓库为 public），`.gitignore` 规则合理（放行 `metrics_table.csv`，挡掉 `*.csv`/`*.npz`/`*.pt`/`report.html`）。外部数据（AKShare/东财）只作数值使用，未拼进 SQL/HTML，未见注入面。 |
| **性能** | 本机 BLAS/torch 多线程退化已被作者实测并量化（`eigh(600)` 单线程 0.062 s vs 8 线程 5.379 s），代码里以 `OMP_NUM_THREADS=1` + `torch.set_num_threads(2)` 固定并三臂同设置 —— 这是正确处理，**属于环境适应而非性能债**。回测热点是每日 600×600 协方差特征分解（约 45 min/臂），用「日度全量重解」是协议要求（每日调仓），无法降频。`_roll` 的 chunk 化与 warm start 已把 QP 从 6–7 s/天降到 0.37 s/天。可优化项（本单不必做）：`psd_clip` 的 `eigh` 与 `ledoit_wolf` 每步重复做特征分解（可复用分解），以及 `metrics()` 里对 726 天逐日 `np.corrcoef` 的向量化。 |

---

## 未确认项（明确说明我没有验证到的部分）

1. **三臂回测未从头重跑**（按任务要求，约 45 min/臂）。因此「权重数组本身能否从零重算出来」未验证 —— 我验证的是「在交付的权重上，指标算术与主检验**逐位**正确」（审计 66 项相对差 0.0、主检验 diff 0.0），以及「QP 在 6 个抽样日上两种求解器一致」。**回测主循环本身（掩码→可交易集合→自由块→warm start→每日重解）未被端到端重跑。**
2. **训练未重跑**（约 3 min/臂 × 3）。我验证了训练产物的自洽性（`pred[t]` 落位、样本集口径、train/val/test 样本数与我独立重建的 809,512 行完全吻合、best_epoch 与 val RankIC 自洽），**未验证同 seed 两次重训是否给出逐位一致的模型**（发现 3 的缺口）。
3. **`scripts/work449_model_diagnostic.py` 未运行**（会写 `model_diagnostic.json`，属修改项目文件）。§11.1 的数字我是用自写脚本从已入库产物独立重算的（见发现 6），**没有**核对诊断脚本本身跑出来是什么。
4. **论文对照表里的论文侧数字**（Table 11：IC 0.047/0.043/0.039、Sharpe 1.23/1.39/1.63、ΔSharpe −0.40、ΔIC +0.008）**我未回原文核对**（本次审查未访问 arXiv:2507.07107v2 与官方仓库 `initial-d/ml-quant-trading`）。本单的 `ΔSharpe` 定义、方向与协议一致；论文侧数值若引用有误，本审查不背书。
5. **协议 §2.2 的功效回退规则**只走到「mask_rate 1.9077% ≥ 0.50% → 锁定 CSI300」这一步，**未验证**「若走 CSI300∪CSI500 会怎样」——该分支按规则未触发，不影响交付。
6. **`pool_membership.json` 的生效日名单**（123 份 / 680 只）我只核了回测里的「按生效日取最近一份名单」逻辑（`work449_backtest.py:208-221`），**未独立核对名单本身**是否与 baostock `query_hs300_stocks` 的历史一致（该端点本次未调用，取数渠道已随 v1.1 变更）。
7. **ST 缺口上界 148 格**（`panel_stats.json:st_diagnostic.possible_missed_st_limit_cells_test`）我只核了它被写进了产物与报告，**未逐格复核**这 148 格的判定。
8. **报告 PDF/PNG 渲染**（`report_print.pdf`、`preview_first_screen.png`）我只做了首屏 PNG 的目视核对（图 3 方向正确）与 HTML 文本核对，**未逐页校对 PDF 版式**。

---

## 附：我跑过的命令（可复现）

```bash
cd /srv/data/multica/workspaces/workspace-c8faa36a018a/work-449-acc3a29910af/workdir/qlab
OMP_NUM_THREADS=1 ../.venv/bin/python scripts/work449_test_primitives.py      # failures=0
OMP_NUM_THREADS=1 ../.venv/bin/python scripts/work449_test_covariance.py      # failures=0
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  ../.venv/bin/python audit/recompute_work449.py --exp WORK-449                # blocking=0, 66 comparisons
# 以下三个为我的独立复算脚本，放在 /tmp/w449_review/（不改项目文件）
../.venv/bin/python /tmp/w449_review/primary_recheck.py    # 主检验逐位复现
../.venv/bin/python /tmp/w449_review/claim_check.py        # §11.1 数字复算
../.venv/bin/python /tmp/w449_review/solver_materiality.py # 求解器口径实质性探针
```

---

**VERDICT：FAIL**（变更层面：发现 1、2、3、4、5、6 必须处理后合并）
**研究结论层面：有条件通过** —— 判词「未复现（退）」与其数值经独立复现成立，求解器口径、模型能力欠拟合均不构成翻案理由。
