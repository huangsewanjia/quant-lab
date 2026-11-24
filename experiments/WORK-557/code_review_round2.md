# WORK-557 独立代码审查 · 第 2 轮（复审第 1 轮修复）

- 审查对象：`scripts/work557_finish.py`、`scripts/work557_baseline.py`、`scripts/work557_verdict.py`
  （含第 1 轮修复 commit `5620751`）
- 审查方式：独立 verifier 子代理（只读），主循环不自审
- 结论：**可提交**（严重缺陷 0，中等 2，低 2）；A–G 七项处置基本成立

## 一、A–G 逐条复核（通过）

| 项 | 结论 | 依据 |
|---|---|---|
| A liveness 探针 | 通过（带一处中等，见下） | 带 `Name='python.exe'` 过滤探到 2 个真实驱动进程；去掉过滤会探到 3 个（多出探针自身 powershell）→ 自身不可能被计入 |
| B 等待上限 | 通过 | 默认 90000 s；`max_wait_exceeded` 直接 `return 1`，不进后处理链；最坏墙钟 10 档 × 7200 s = 72000 s ≤ 90000 s（余量 18000 s） |
| C 回测自检 | 通过 | `{}` 被拦；只要求**至少一个** seed 齐 full/no_vq/gru → 「只完成 seed 42」不被误杀；不通过则 `rc=1; break` → `ok=False` |
| D 基线 parquet 带 suffix | 通过 | 正式档 `suffix=""` → 落盘名仍为 `{kind}_s{seed}.parquet`，与改动前一致；`work557_backtest.py` 读的路径不变 → **非破坏性** |
| E `--exp` 守卫 | 通过 | 非 WORK-557 直接 `SystemExit`（rc=1），且在任何写盘之前 |
| F `_groups_cache` 键 | 通过 | `Work503Dataset` 为普通类（无 `__eq__/__hash__/__slots__`）→ 身份哈希；只换键，分组/洗牌/`list()` 复制语义未动 |
| G 删死导入 | 通过 | 仅余注释提及 `executor.map` |

## 二、回归核对（通过）

- 判词口径：`work503_verdict.py` vs `work557_verdict.py` 仅 5 处差异（docstring 标题、`EXP`、
  `--exp` 默认值、`REASON` 文案、`cmp_rows.source`）；H1/H2/H3 计算式、band `[-0.40,-0.15] / [-0.10,+0.10]`、
  `BOOT=10000 / BLOCK=5 / seed=20240613`、五条失败判据、`sign_note`、四个输出文件名与列名**逐字未变**。
- `py_compile` 三个脚本 exit=0。
- 三个脚本 + 链上 5 个下游脚本 grep `WORK-503` 仅命中注释，**写路径 0 处**。

## 三、第 2 轮新发现与处置

| 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|
| 中等 | `work557_finish.py` `driver_alive()` | powershell **非零退出 + stdout 空**时按 0 处理 → 误判「驱动已死」，与「查不到就当还活着」的保守方向相反 | 改为：`returncode != 0` 或 stdout 非数字 → 一律返回 True（保守） |
| 中等 | `work557_finish.py` 等待分支 | `driver_process_gone_without_training_log` 不短路，驱动崩了仍会跑后处理链 | 改为短路：驱动消失且 **10 档产物（json+parquet）不全** → 不跑后处理链、`ok=False`；仅当 10 档产物齐全（崩溃发生在 `collect()` 之后、写日志之前）才继续，并留痕 |
| 低 | `work557_verdict.py` H1 | 只有 1 个 seed 时 H1 仍给确定「进/退」，`verdict.json` 无 `n_seeds<2` 标注，与 docstring「>= 2 seeds」不符 | **不改判词脚本**（保持与 WORK-503 仅 5 处差异）。改在回帖层披露：若只有 1 个 seed，H1 必须写明「n_seeds=1，未满足预注册的 ≥2 seed 要求」，不得直接宣称「进/退」 |
| 低 | `work557_finish.py` × `work557_cost_sensitivity.py` | 三个成本档不在 critical 集合；产物为空时成本点被**静默跳过**、成本表缺档仍 `ok=True`，违预注册 §5.2「三档全跑全报」 | 链在每个成本回测后做非空校验：产物须非空且含至少一个 `full_s*` 档；**预注册点**（8 bps / 30 bps）自检不过即中断链，零成本诊断点不过则留痕但继续 |

## 四、仍未确认

1. 链的端到端行为未运行验证（训练仍在跑，`finish_summary.json` 未产生）。收尾后只读核对
   `experiments/WORK-557/finish_summary.json` 与 `experiments/WORK-557/logs/finish_*.log`。
2. 四成本档回测与独立复算的**数值**正确性。
3. 实际总墙钟是否 ≤ 90000 s（当前已知：GRU 基线**无早停**，跑满 50 轮 × ~75 s ≈ 62 min/档；
   四档为 patience 15 早停）。
