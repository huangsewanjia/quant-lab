# WORK-637 独立代码审查简报（交给子审查者）

## 你的角色

你是**独立代码审查者**，按 skill `code-review-and-quality`（id `53fc921a-9abe-4ab7-ab8b-5a2edd21ab6a`）执行。
你**不得修改**被审代码；发现问题只写报告，按 **blocking / suggestion / nit** 分级。
你不是被审代码的作者，也不参与过它的设计。

## 仓库与提交

- 仓库：`quant-lab`，工作目录由调用方给出（绝对路径）。
- 审查对象：本次提交涉及的下列文件（`git log --oneline -5` 可看到 WORK-637 相关提交）。
- 预注册协议（**审查的基准**，不得被代码偷偷绕过）：
  `experiments/WORK-637/preregistration.md`

## 要审的文件

| 文件 | 作用 |
|---|---|
| `scripts/work637_common.py` | PIT 池、周历、面板、可成交判定 |
| `scripts/work637_snapshot.py` | 端点快照与 sha256 台账 |
| `scripts/work637_render.py` | 140×140 K 线渲染（20 日窗口 + MA20 + 量） |
| `scripts/work637_encode.py` | 冻结 DINOv2-S 编码（CLS + L2） |
| `scripts/work637_signal.py` | 递归扩张库 + 精确 top-K + 信号 + 前视断言 |
| `scripts/work637_backtest.py` | 组合、成本、掩码、指标、bootstrap |
| `scripts/work637_assert_check.py` | 断言「是否真的会抛错」的对照运行 |
| `audit/recompute_work637.py` | 独立复算（不得 import 建模阶段函数） |

## 必查清单（逐条给结论，不要泛泛而谈）

1. **前视偏差**：库可用性判据 `idx(τ)+6 <= idx(t)` 是否在每一处成立；`index_weight` 的 as-of 是否只用 `snapshot_date <= t`；
   标签 `open_qfq(t+6)/open_qfq(t+1)-1` 与组合持有区间是否一致；有无任何用未来数据的地方（含信号平局打散、bootstrap）。
2. **可成交性**：涨停不买 / 跌停不卖 / 停牌不成交的判定用的是**未复权价 vs stk_limit**吗；卖出顺延规则是否可能把收益算错
   （顺延到未来开盘价是否等于「实际能卖出的价格」）；`forced_exit` 是否被静默忽略。
3. **成本**：`|Δw| × bp` 的 `Δw` 是否含「漂移后再平衡」；换手是否双边；成本是否作用在净值上而不是只扣一次。
4. **指标定义**：Sharpe 年化（√52）、年化收益（复利）、MDD（running peak）、RankIC（Spearman）是否正确；
   bootstrap 是否为 block bootstrap 且块长 5、p 值/CI 的取法是否与预注册一致。
5. **独立复算**：`audit/recompute_work637.py` 是否真的不 import 建模函数（`grep` 验证）；
   是否真的换了代码路径（不是复制粘贴同一段逻辑）；相对差 ≤1e-6 的比对是否逐项且失败即列出。
6. **断言有效性**：`work637_assert_check.py` 是否真的让三条断言各抛一次，产出的 `.txt` 是否含原文；
   有没有「断言存在但永远不成立」的假门。
7. **数据与口径**：`v_daily_qfq` 是否被当成复权价正确使用；停牌缺行是否被前向填充（不允许）；上市天数门槛是否生效。
8. **可复现性**：随机种子是否覆盖所有随机源；`np.random.default_rng` 是否被跨进程复制导致不可复现；
   产物 sha256 是否真的对应落盘文件。

## 你要交回什么

1. 分级清单：每条 = 触发条件 / 后果 / 实测值（能测的就测）/ 代码位置（`文件:行号`）/ 可执行改法。
2. **独立复跑**：自己跑一次
   `../.venv/bin/python -u scripts/work637_backtest.py --seed 42 --K 1000 --cost-bp 12`
   （或等价命令）与 `../.venv/bin/python -u audit/recompute_work637.py`，
   报告两次输出是否一致、与已落盘 JSON 是否一致。
3. 结论：能否进入研究备忘录；未确认项清单。
4. 明确写：**你没有修改任何被审文件**。

## 环境

- Python 解释器：`<REPO>/../.venv/bin/python`（torch 2.9.1+cpu / faiss / duckdb / transformers 已装）。
- quantdb 只读：`/srv/share/quantdb/db/quantdb.duckdb`。
- 图片库：`/srv/share/quantdb/code/WORK-637/imagelib/seed42/`。
- 大产物不要落 `/tmp`；需要临时文件时用仓库同级的 `_review_tmp/`。
