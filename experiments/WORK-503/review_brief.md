# WORK-503 独立代码审查契约（Scope Contract）

> 审查方：独立子代理（code-review-and-quality 技能）· 提交方：金融研究 agent
> 冻结日期：2026-11-22 · 本文件是审查方开工读到的第一段，等同「委派范围」。

---

## 一、审查对象（唯一，不要扩范围）

> **占位产物声明（重要，避免误报）**：本契约落盘时，四档消融与基线的**正式训练仍在进行**（本机无 GPU，单轮全量训练约 2.5–3 小时）。
> 因此工作区里 `experiments/WORK-503/preds/*.parquet`、`verdict.json`、`metrics_table.csv`、`report.html`、`preview_first_screen.png`、
> `report_print.pdf` 目前是**冒烟/占位产物**（含模拟数据），不是研究结果，终稿会覆盖它们。
> **不要把占位产物的数值当作发现**；凡是基于占位产物得出的对账结论，一律注明「基于占位产物，待终稿复跑」。
> 代码本身（`scripts/**`、`audit/**`）是终稿代码，不受此声明影响。

| 项 | 值 |
|---|---|
| 仓库 | `/srv/data/multica/workspaces/workspace-c8faa36a018a/work-503-69b2057aa375/workdir/quant-lab` |
| 分支 | `agent/agent/69b2057aa375` |
| 审查 commit 范围 | `06fd4a8`（预注册 v1.2）→ `22503f0`（当前 HEAD） |
| 预注册 | `experiments/WORK-503/preregistration_v1.2.md`（v1.0 / v1.1 保留，不删） |
| 掩码契约 | `experiments/WORK-503/mask_contract.md` |
| 数据快照 | `data/WORK-503/`（`qlib_cn/` `ds/` `panel/` `sina/` `sina_hfq/`），清单 `experiments/WORK-503/snapshot_manifest.json` |

**只审下列文件，不要通读全仓库**：

| # | 文件 | 行数 | 审什么 |
|---|---|---|---|
| 1 | `scripts/work503_run_arms.py` | 348 | 训练驱动：种子是否真播种全局 RNG；stage-1 是否按种子各训一份并在该种子三档间共享；arm 到上游 trainer 的映射是否与论文 Table 2 一致；`use_prior=False` / `LoadingGeneratorWOMoE` / 无 vqvae 三条分支是否走对文件 |
| 2 | `scripts/work503_baseline.py` | 179 | GRU 固定基线：模型形状、损失、批顺序、随机性是否可复现；输出 parquet 是否与四档同构 |
| 3 | `scripts/work503_backtest.py` | 576 | 份额级 TopK-DropN 引擎：K=30 / N=5、t 收盘卖 15bps、t+1 开盘买 5bps、退市清算、候选池剔涨停停牌、双闸门执行；掩码契约 §7 的 5 条断言是否为活代码 |
| 4 | `audit/recompute_work503.py` | ~450 | 独立复算：是否真的不共用建模函数（`independence_selfcheck()` 已用 ast 机械自检）；数值是否走独立代码路径 |
| 5 | `scripts/work503_verdict.py` | 313 | H1 / H2 / H3 判定逻辑与预注册阈值逐条对齐；失败判据 1–5 |
| 6 | `scripts/work503_check_integrity.py` / `scripts/work503_check_lookahead.py` | — | 断言 I1–I5 与前视检查是否真的在检查（不是空断言、不是恒真） |
| 7 | `scripts/work503_report.py` | 805 | 报告生成：数字是否全部来自产物文件、有无硬编码结论 |

**不要审**：`vendor/PRISM-VQ/**`（上游代码，MIT，未改动）、`experiments/WORK-449/**`（别的单号）、`data/**`（快照本体）。

## 二、必须回答的问题（逐条给结论，不要泛泛评语）

1. **种子有效性**：`work503_run_arms.py:230-237` 的 `seed_everything` + `pl.seed_everything(workers=True)` 是否足以让「种子 42 / 2024」可复现？模型初始化、dropout、每日批次顺序三个随机源是否都被覆盖？
2. **消融映射**：四档（full / no_moe / no_prior / no_vq）是否真的只改一个组件？上游 `train_ypred_wo_prior.py` 是否硬编码 `use_prior=False`（是则 no_prior 的先验确实被去掉）？`no_moe` 是否用 `LoadingGeneratorWOMoE`？
3. **组合规则**：TopK-DropN 的 N=5 是「每次换掉 5 只」还是「跌出 5 只才换」？换手口径是单边（÷2）还是双边（不除 2）？与预注册 §6、论文 Table 1 的 0.3259 是否自洽？
4. **前视**：回测在 t 收盘卖、t+1 开盘买；买入用 t+1 开盘价是否算前视？掩码断言是否覆盖「决策日买入后又当日卖出」这类路径？
5. **断言活性**：5 条掩码断言是否都有可触发路径（对照 `experiments/WORK-503/assertion_violation.txt` 的触发原文）？
6. **复算独立性**：`audit/recompute_work503.py` 的组合重算与建模侧是否真的不同源（不是同一份代码抄两遍）？`audit/README.md` 规则 2 要求纯标准库，本脚本用了 numpy/pandas/scipy——这是**已知偏离**，请评估它是否削弱独立性结论（parquet 需第三方读取器）。
7. **判定阈值**：H1 的进/退/部分复现三档、H3 的「净 SR<0 直接判退」、失败判据 1–5，是否与 `preregistration_v1.2.md` 逐字一致？有无放宽阈值让结论变绿的写法？
8. **报告数字来源**：`scripts/work503_report.py` 生成的正文数字是否全部来自 `verdict.json` / `metrics_table.csv` / `paper_comparison.csv` / `audit_recompute.json`，有无硬编码？

## 三、交付物（必须交回这三样，缺一不可）

1. **问题清单**，每条标注 `blocking` / `suggestion` / `nit`，并写清：触发条件与后果、实测值与阈值或预期的差距（涉及量化指标时）、代码位置（`文件:行号`）、可执行改法。
   - 只报**你亲自验证过**的问题；未验证的写「未验证」，不要猜。
   - 「差一点通过」要带实测值与差距；构造本身有问题时连测量值一起指出。
2. **独立复跑结果**：把下列命令实际跑一遍，把命令与关键输出贴进报告：
   ```bash
   cd /srv/data/multica/workspaces/workspace-c8faa36a018a/work-503-69b2057aa375/workdir/quant-lab
   ../env/.venv/bin/python -u audit/recompute_work503.py --out /tmp/work503_audit_reviewer.json
   ../env/.venv/bin/python -u scripts/work503_verdict.py
   ../env/.venv/bin/python -u scripts/work503_check_integrity.py
   ```
   并报告：`audit_recompute.json` 的 `n_mismatches`、`independence.modeling_imports_found`、以及 `verdict.json` 的 `verdict` 与 `H1.delta_ic`。
   - **审计方不得修改被审代码**（audit/README.md 规则：风控审计不能改被审模型）。发现问题只报不改；需要改动由提交方执行后再复跑。
   - `work503_verdict.py` 会覆盖仓库里的 `verdict.json` / `metrics_table.csv` —— 这是允许的（提交方在终稿后会自己再跑一遍），但**不要改任何 `scripts/**` 或 `audit/**` 里的文件**。
   - `work503_check_integrity.py` 会重算数据集完整性断言，CPU 较重；**如与训练抢资源，可跳过并在报告里写「未完成 + 原因」**，不要因此停工。
3. **复现性检查**：同种子两次独立跑的结果是否逐位一致（对照 `preds/` 中已有的同种子复跑记录）。若无法在预算内完成，明确写「未完成 + 原因」，不要写成「通过」。

## 四、预算与停止条件

- **工具调用预算**：≤ 60 次（含 bash 与文件读取）。预算用尽即停，把已验证部分交回，未验证项写「未验证」。
- **时间预算**：≤ 45 分钟。
- **停止条件**：① 三项交付物齐备；② 预算用尽；③ 发现 blocking 且已给出实测值——此时停止继续深挖，先把 blocking 交回。
- **不要**：跑训练（CPU 已被占满）、重建数据集或面板、修改被审代码、联网取数。

## 五、已披露的已知偏离（不必重复报，除非你认为它使结论不成立）

1. 审计脚本非纯标准库（见上文问题 6）。
2. 上游 `stage1.py` 写死 `fixed_seed = 42`；本单改为**每个种子各训一份 stage-1** 并在该种子的三档间共享（预注册 §13 已披露）。
3. 上游 yaml（`close→close`）与论文正文（`open(t+1)→close(t+k)`）标签口径冲突，本单以**论文正文**为准（预注册 D3）。
4. JKP 13 因子无 A 股个股级对应值，先验改为自建 6 个 A 股因子（偏离 D2）→ **`no_prior` 档的降幅不能用来支持「码本有效」**。
5. 无 GPU，训练段 2015–2019、≤6 轮（论文 2009–2019、≤50 轮）→ 绝对指标系统性低于论文，**只比较四档之间的相对差异**。
