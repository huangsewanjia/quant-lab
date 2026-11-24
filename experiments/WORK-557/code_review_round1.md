# WORK-557 独立代码审查 · 第 1 轮

- 审查对象：`scripts/work557_verdict.py`（未提交 diff）、`scripts/work557_finish.py`（新增）、
  commit `4969ca6`（训练吞吐修复）
- 审查方式：独立 verifier 子代理（只读，不改文件），主循环不自审、不采信自述
- 结论：**需修改** —— 判词口径零改动（通过）；后处理链 4 项中等 + 3 项轻微缺陷

## 一、判词口径核对（范围 1）—— 通过

与 `scripts/work503_verdict.py` 全量对拍，差异仅 5 处：docstring 标题、`EXP` 常量、
`--exp` 默认值、`REASON` 文案、`"source": "WORK-503" → args.exp`。

逐字未变的部分：`H1` 的 Δ_drop 与 band `[-0.40,-0.15] / [-0.10,+0.10]`、
`H2` 的 inner join + block bootstrap、`H3` 的「净 SR CI 下界 > 0 且缩水 ≥ 0.40」、
`BOOT=10000 / BLOCK=5 / seed=20240613`、五条失败判据、`sign_note` 逻辑、
输出文件名与列名（`verdict.json` / `metrics_table.csv` / `metrics_by_arm.csv` / `paper_comparison.csv`）。

## 二、后处理链缺陷（范围 2）与处置

| 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|
| 中等 | `work557_finish.py` liveness 探针 | 模式串 `*work557_train_all.py*` 出现在**探针自身**命令行里 → `driver_alive()` 恒为 True，`gone>=3` 兜底永不触发，驱动真死会静默挂死 | 探针改为只认 `Name='python.exe'`。实测修复后只数到 2 个真实驱动进程（驱动 + 其 launcher 子进程），自身已排除 |
| 中等 | `work557_finish.py` 等待上限 | 默认 36000 s < 最坏训练墙钟（10 档 × 7200 s 硬停 = 72000 s） | 默认改为 90000 s；`max_wait_exceeded` 时**不跑**后处理链（不拿半截产物算 H1/H2/H3） |
| 中等 | `work557_finish.py` × `work557_backtest.py:635-636,687` | 回测**全部档缺失时仍退出 0**（`backtest_results.json` = `{}`），判词亦返回 0（判词=「无法判定」）→ 链会报 `ok=True` | 链在 `backtest_main` 后独立自检：结果非空，且至少一个 seed 同时具备 `full/no_vq/gru`（H1/H2 最小完整集），否则中断链 |
| 中等 | `work557_baseline.py:249-250` | 基线 parquet **不带 suffix**：探针已写下 `preds/gru_s42.parquet`（2 epoch）；正式 GRU 同名覆盖，**若正式档被停手条件③杀掉**，回测会拿 2-epoch 探针当 GRU 基线 → H2 静默失真 | ① parquet 落盘名改为带 `out_suffix`（正式档 `suffix=""` → 路径不变）；② 已把探针残留 `preds/gru_s42.parquet` **改名**为 `preds/gru_s42_probe2.parquet`（保留证据、不删除），正式档若被杀则 `gru_s42.parquet` 缺失 → 回测跳过 → 链自检失败 → 如实回报，不静默污染 |
| 轻微 | `work557_finish.py` × `work557_verdict.py:221` | `--exp` 未全链透传：下游有脚本写死 WORK-557，误传别的 exp 会覆盖 `experiments/WORK-503/verdict.json` 等 | 链拒收非 WORK-557 的 `--exp`（直接退出） |
| 轻微 | `work557_baseline.py:164` | `_groups_cache` 以 `id(ds)` 为键（id 在对象被回收后会复用） | 键改为 dataset 对象本身（持强引用，杜绝 id 复用；`Work503Dataset` 未定义 `__eq__`，仍按身份哈希） |
| 轻微 | `work557_baseline.py:23` | `ThreadPoolExecutor` 死导入 | 删除 |

## 三、数值等价性（范围 3，commit `4969ca6`）—— 通过

- `_ffill_bfill` 快实现：原实现两次 fill 均被紧随的 `x[mask]=np.nan` 抵消，净效果 = `nan_to_num`，
  与新实现**逐位等价**（证据 `experiments/WORK-557/ffill_equivalence.json`：`max_abs_diff=0.0`）。
- `__getstate__` 只丢 `_feat/_prior/_label` 三个惰性缓存，按需重开；`rows`/`index` 照常 pickle
  → 样本顺序、label/prior 数值不变。
- `persistent_workers` 只改 worker 生命周期；sampler 在主进程迭代，`__getitem__` 无随机数
  → batch 组成与顺序、RNG 消耗顺序不变。
- 基线 `_groups_cache` 只记忆分组切分，`list(...)` 复制外层列表 → `rng.shuffle` 不污染缓存、置换逐轮一致。
- `vendor/` 无改动。

## 四、未确认

- 四个成本档回测与独立复算的**数值**正确性：需产物生成后运行核对。
- `work557_report.py`（段4）不在本轮链内，未审。
- 正式 GRU 是否会被停手条件③杀掉：取决于实测墙钟。
