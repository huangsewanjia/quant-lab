# WORK-557 独立代码审查 · 第 3 轮（链自动发帖 + 计划任务脚本）

- 审查对象：commit `a43487c`（`work557_finish.py` / `work557_comment_stage2.py` / `run_all.cmd` / `run_handle.md`）
- 审查方式：独立 verifier 子代理（只读），主循环不自审
- 结论：**需修改** —— 1 项严重 + 4 项中等 + 若干低

## 一、严重缺陷

**S1：自动发帖在计划任务下必然崩 + 连带丢失 `finish_summary.json`**

- 根因：计划任务 `WORK557_run` 的环境块实测**无 `MULTICA_CLI_PATH`**（实测读 PID 23692 的 PEB），
  且 PATH 只含注册表 user+machine，仓库根 / `.venv\Scripts` / `.local\bin` 均无 `multica*`
  → `subprocess.run(["multica", …])` 抛 `FileNotFoundError`。
- 连带：异常逃逸出 `main()` → `finish_summary.json` 的写盘在其后 → **整轮链记录丢失**，
  issue 也不会被置 `in_review` → 一次性唤醒永不触发 → issue 静默。
- 处置：
  1. 新增 `experiments/WORK-557/runtime_paths.json`（记 `multica.exe` 绝对路径），
     链按 **环境变量 → runtime_paths.json → 裸 multica** 三级解析（`_runtime_cli_path()`）；
  2. `run_all.cmd` 显式 `set MULTICA_CLI_PATH=…`（对新起的任务生效）；
  3. `finish_summary.json` **先落盘再发帖**（`_dump()`），且 `notify_ready()` 包 try/except
     → 链记录不再因发帖异常而丢；
  4. 正文生成器读到的 `finish_summary.json` 因此是**本轮**的（M1 同时解决）。

## 二、中等缺陷

| # | 问题 | 处置 |
|---|---|---|
| M1 | 发帖时正文读到的是**上一轮**的 `finish_summary.json`，丢掉本轮已通过的 backtest/cost 自检 | 写盘移到发帖之前（见 S1-3） |
| M2 | `usage_line()`：①文案「累计 = 61.9M + 本轮增量」读法歧义；②会话目录缺失时 `collect()` 静默返回全 0 → 会输出「0.0M / 增量 −61.9M」；③口径含 cacheWrite（项目口径为 cache-read+input+output） | ①文案改为「= 派单基准 61.9M + 本轮新增 X M」；②`grand <= 0` 时输出「未取到」标记；③去掉 cacheWrite |
| M3 | 无幂等保护，重跑 `run_all.cmd` / `schtasks /run` 会**再发一条**几乎相同的评论 | 记 `notify.posted_at` + `notify.comment_id`（发帖用 `--output json` 取回 comment id），已发过则跳过。**注意**：因写盘移到发帖之前，上一轮 notify 记录会被覆写 → 由 `main()` 在覆写前抓取 `prev_notify` 传入 `notify_ready(prev_notify)`（主循环自查发现并修） |
| M4 | 任务 XML `StopIfGoingOnBatteries=true` / `DisallowStartIfOnBatteries=true`，机器转电池会停掉数小时训练 | `Get-CimInstance Win32_Battery` 返回空 → 本机台式机、无电池/UPS 上报，两项**实际不触发**（已核对并写入 `run_handle.md`）；将来接入 UPS 才需重建任务改这两项 |

## 三、低危项

- 正文里硬编码的 GPU 型号「RTX 3060」不由产物自证 → 改为从 `training_log.json` 的 `gpu.device` 读。
- `run_handle.md` 缺「从零重建」命令、缺电池策略结论 → 已补。
- `run_all.cmd` 的 `%date%` 中文星期在 OEM 代码页乱码 → 改为 `%TS%` 自拼时间戳。
- `threads=8` 属驱动启动参数（非产物字段）→ 保留但标注来源为启动参数。

## 四、审查判定为「通过」的部分

- `run_all.cmd`：`%errorlevel%` 展开时机正确（临时目录探针实测 rc=3 / rc=0）；链无条件执行（驱动非 0 也跑）；两条 python 均 `>>log 2>&1`。
- `schtasks /end` + `/delete /f` 正确、可逆、不删产物。
- 判词口径回归：`work557_verdict.py` 与 `work503_verdict.py` 仍只允许 5 处差异 → 判词口径未动。
- `py_compile` exit=0。
- 两个 Markdown 表均 9 列（表头/分隔/数据行一致）；`metrics_table.csv` 字段与生成器读取键逐一吻合；
  `n_seeds<2` 时正文明确写「不宣称任何进/退」。
