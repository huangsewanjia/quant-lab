# WORK-503 独立代码审查 · 第二轮（终稿产物复核）

> 这是第一轮审查（`review_brief.md`）的续轮。第一轮结论：**blocking 2 / suggestion 5 / nit 3**，全部由提交方修复。
> 第二轮只做三件事：**① 复核修复是否真的修好了；② 对终稿产物做独立复跑；③ 确认判词与产物的数字自洽**。
> 审查方**不得修改任何被审文件**。

## 一、修复复核（逐条给「已修 / 未修 / 部分修」，附证据行号）

| # | 第一轮的问题 | 修复位置（提交方填写，审查方核对） |
|---|---|---|
| blocking 1 | H1 公式与判词带符号矛盾 | 预注册 `preregistration_v1.3.md` + `preregistration_v1.4.md`；判定脚本 `scripts/work503_verdict.py` 的 H1 段 |
| blocking 2 | `sr_stat` 不接受 `axis` → 判定脚本必崩 | `scripts/work503_verdict.py` 的 `sr_stat()` |
| suggestion 1 | FC2 / FC5 不改判词 | `scripts/work503_verdict.py` 失败判据段 |
| suggestion 2 | 报告把不可算的 Δ 渲染成「部分复现」 | `scripts/work503_report.py` §4 判词段 |
| suggestion 3 | `assert_weight_sum` 恒真 | `scripts/work503_backtest.py` 的盯市段与目标构造段 |
| suggestion 4 | FC1 的 PIT 覆盖率半边没实现 | `scripts/work503_check_integrity.py` 的 I5 段 + `scripts/work503_verdict.py` 的 FC1 |
| nit 2 | GRU 基线每 epoch 重置 RNG | `scripts/work503_baseline.py` 的 `rng` |
| — | **第一轮误报复核**：同 seed 两个并发训练进程 | 实测为 DataLoader worker（PPID = 训练主进程）；本轮请再看一眼并给结论 |
| — | **新增实现**（第一轮之后、改过代码） | 市值加权基准改用真实 CSI300 指数 + 自建总市值篮子并列披露；成本敏感性汇总脚本 |

## 二、终稿产物的独立复跑（必须实跑，把命令与输出贴进报告）

```bash
cd /srv/data/multica/workspaces/workspace-c8faa36a018a/work-503-69b2057aa375/workdir/quant-lab
../env/.venv/bin/python -u audit/recompute_work503.py --out /tmp/work503_audit_round2.json
../env/.venv/bin/python -u scripts/work503_test_verdict.py
../env/.venv/bin/python -u scripts/work503_verdict.py
```

必须报告：
- `n_mismatches`（相对差 ≤ 1e-6 的逐项对账）、`independence.modeling_imports_found`；
- `verdict.json` 的 `H1.delta_drop`、`H1.delta_formula`、`H1.verdict`、顶层 `verdict`、`H3.net_sr_negative`、`H3.tradicable`；
- 边界单测的通过数（应为 21 项全过）。

## 三、终稿数字的自洽性检查（用自己的算式重算，不看现成结论）

1. **H1 手算**：用 `metrics_table.csv` 里 full / no_vq 两档的 RankIC 中位数，按预注册 v1.3 §3 的口径手算 Δ_drop，看是否与 `verdict.json` 一致（相对差 ≤ 1e-9）。
2. **H3 手算**：用 `preds/full_s42_daily.parquet` 的 `ret` 列手算年化 SR（√252 × mean(log1p(ret)) / std(log1p(ret))，ddof=0），看是否与 `backtest_results.json` 的 `sr` 一致。
3. **换手**：用 `preds/full_s42_trades.parquet` 手算单边换手（按决策日记账、÷2），看是否与 `turnover` 一致。
4. **基准**：核对 `preds/full_s42_bench.parquet` 的 `index` 列与 `data/WORK-503/index_sh000300.csv` 的日收益一致（逐日相对差 ≤ 1e-9）；并核对 alpha_index 的超额年化 = (组合年化 − 基准年化) 的量级是否自洽。
5. **判词链**：确认顶层 `verdict` 与 H1/H3 的触发规则一致（净 SR < 0 直接判退这一条是否被正确触发）。

## 四、交付前自检（逐项给「过 / 不过」）

- [ ] `experiments/WORK-503/report.html` 是单文件：图 base64 内嵌、**无外链** JS/CSS/字体（可用 grep 查 `http://` / `https://` / `<script src` / `<link href`）。
- [ ] `preview_first_screen.png` 是 1440×900，`report_print.pdf` 是 A4 纵向。
- [ ] 四张图的碰撞审计（`audit_figure_collisions.py`）全部 PASS（0 fail）。
- [ ] 报告正文里的每个数字都能在 `verdict.json` / `metrics_table.csv` / `paper_comparison.csv` / `audit_recompute.json` / `integrity_check.json` / `lookahead_check.json` 里找到对应（不得有硬编码结论）。
- [ ] `experiments/registry.md` 的 WORK-503 行已加，字段齐全。
- [ ] 失败记录 `failure_log.md` 里有：基准口径修正、零成本诊断（预注册外）、第一轮审查的 blocking 与我方自身的错误论证更正。

## 五、预算与停止条件

- **工具调用 ≤ 45 次、时间 ≤ 30 分钟**；用尽即停，已验证部分照交，未验证项写「未验证」，不得写成「通过」。
- **不得**修改 `scripts/**`、`audit/**` 里的任何文件；需要改动由提交方执行后再复跑。
- **不得**跑训练（CPU 已被占满）、不得重建数据集或面板、不得联网取数。
- 报告写完即停。
