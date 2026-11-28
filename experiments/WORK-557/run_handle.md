# WORK-557 脱离 run 生命周期的长任务 · 运行句柄

## 为什么需要脱离

2026-11-25 00:11 实测：上一个 Multica run 终态时，**它持有的后台任务被平台一并带走**——
GRU s42 训练在 epoch 21（1627 s）被杀，基线只在训练末尾写 parquet/json，故**产物未落盘**，
该档 21 轮全部作废。Multica runtime 也明示「run 终态即孤立 run 持有的后台任务」。

因此训练与后处理链改由 **Windows 计划任务**起：进程的父级是 Task Scheduler 服务，
不在任何 run 的进程树里，run 终态不会带走它。

## 运行句柄

| 项 | 值 |
|---|---|
| 计划任务名 | `WORK557_run` |
| 入口脚本 | `experiments/WORK-557/run_all.cmd` |
| 训练命令 | `.venv\Scripts\python.exe -u scripts\work557_train_all.py --epochs 50 --gru-first --no-wait-gpu --max-run-seconds 7200 --workers 8` |
| 链命令 | `.venv\Scripts\python.exe -u scripts\work557_finish.py` |
| 总日志 | `experiments/WORK-557/run_all.log` |
| 训练逐轮日志 | `experiments/WORK-557/training_log.txt` |
| 链逐步日志 | `experiments/WORK-557/logs/finish_*.log` |
| 链收尾摘要 | `experiments/WORK-557/finish_summary.json` |
| 段2 正文（发帖前） | `experiments/WORK-557/comment_stage2.md` |

## 查看状态

```powershell
schtasks /query /tn WORK557_run /v /fo list | Select-String 'Status|Last Run'
Get-Content C:\Users\MO_Yongkai\multica_workspaces_cloud\workspace-c8faa36a018a\work-557-a221b7629a32\workdir\quant-lab\experiments\WORK-557\run_all.log -Tail 20
```

## 从零重建（清理后照此复跑）

```powershell
$repo='C:\Users\MO_Yongkai\multica_workspaces_cloud\workspace-c8faa36a018a\work-557-a221b7629a32\workdir\quant-lab'
schtasks /create /tn WORK557_run /tr "$repo\experiments\WORK-557\run_all.cmd" /sc once /st ((Get-Date).AddMinutes(2).ToString('HH:mm')) /f
schtasks /run /tn WORK557_run
```

## 电池策略（已核对：本机无电池，实际不适用）

任务 XML 里 `StopIfGoingOnBatteries=true` / `DisallowStartIfOnBatteries=true`，
但 `Get-CimInstance Win32_Battery` 返回空 → 本机为台式机、无电池/UPS 上报，
这两项**实际不触发**（2026-11-25 00:52 核对）。若将来接入 UPS，需导出任务 XML
把这两项改成 false 后重建（重建会中断正在跑的任务）。

## 停止 / 清理（可逆）

```powershell
schtasks /end /tn WORK557_run
schtasks /delete /tn WORK557_run /f
```

> 停止不会删除任何产物；`preds/`、`training_log.json`、`backtest_results.json` 等已落盘文件保留。
> 驱动本身带「产物存在即跳过」的断点续跑，重启不会重训已完成的档位。

## 链跑完会自动做的事

1. 4 个成本点回测（5/15、8/8、30/30、零成本）→ 完整性 → 前视 → 独立复算 → 判词 → 成本敏感性；
2. 每步产物自检（结果非空 + 最小完整集 + 成本点非空），自检不过即中断链并留痕；
3. 生成段2 正文 `comment_stage2.md`（五段式 + `{{USAGE_LINE}}` 占位符），回填真实用量行；
4. 发布一条段2 评论到 issue `01a20cc7-07ec-722d-8671-a6201a12dd79`
   的线程 `01a20e19-bf19-732d-b57f-e8e05364375e`；
5. 把 issue 状态置为 `in_review` —— 对应一次性 wakeup `--until-status in_review`，
   用于让下一轮智能体核验并交付段 3 / 段 4（避免 20 分钟轮询继续烧额度）。

---

## 2026-11-27 状态（段 3/段 4 拉起时现读）

- `schtasks /query /tn WORK557_run /v /fo list` → **没有在跑**：`上次运行时间 2026/11/25 0:43:00`、
  `模式: 就绪`、`上次结果: 0`。本轮**没有起任何新的计划任务**，所以「续跑句柄」当前为空。
- 若要重跑（需先修 teardown 死锁，见 `report.html` §2）：
  ```powershell
  cd C:\Users\MO_Yongkai\multica_workspaces_cloud\workspace-c8faa36a018a\work-557-a221b7629a32\workdir\quant-lab
  schtasks /run /tn WORK557_run            # 起训（脱离 run 生命周期）
  schtasks /query /tn WORK557_run /v /fo list
  Get-Content experiments\WORK-557\run_all.log -Tail 40
  # 停止（可逆，不删产物）：
  schtasks /end /tn WORK557_run
  # 彻底删除任务（不可逆，但产物保留）：
  schtasks /delete /tn WORK557_run /f
  ```
- 机器在 2026-11-26T18:14Z → 2026-11-27T13:00Z 离线 18h45m，期间目录内**零新文件**
  （见 `report.html` 表 1）——这次丢的是可用时间，不是数据。

## 2026-11-27 起训参数（返工后新增）

- **`--workers 2`**：`num_workers=8` 在这台机器上三次尝试两次硬退（RC=127，无 traceback）、
  一次成功但 worker spawn 用掉 121 s；`workers=2` 下同一档 10 轮 1,411 s 连跑、干净退出。
- **不要省 `wandb.init(mode="disabled")`**：上游 `train_vqvae.py:97` 每轮末调用
  `wandb.log(wandb.Image(plt))`，不 init 会联网挂住（本轮 faulthandler 实测栈在
  `wandb/sdk/lib/asyncio_manager.py`）。生产路径 `work557_run_arms.py` 本来就有，标定脚本已补。
- 单档定价（stage-1，实测）：**136.1 s/轮**、峰值显存 **0.077 GB**、50 轮 **1.89 h**。
  复跑：`.venv\Scripts\python.exe -u experiments\WORK-557\work557_epoch_timing.py --epochs 10 --workers 2 --seed 42`

## 2026-11-28 最小核实运行（两个计划任务 + 结果）

| 计划任务 | 跑什么 | 日志 | 结果 |
|---|---|---|---|
| `WORK557_verify` | `work557_verify_driver.py`：阶段一（早停开）→ 条件触发阶段二 | `verify/driver.log`、`verify/console.log` | 阶段一 28 轮停，fit 3,825.8 s |
| `WORK557_verify_full50` | `work557_verify_run.py --phase full50`（早停关，跑满 50 轮） | `verify/console_full50.log` | 50/50 轮，fit 6,648.5 s = 1.847 h，rc=0 |

**实测（stage-1，full/seed42，workers=2，含完整 valid 段）**
- 满 50 轮：**6,648.5 s = 1.847 h**，均值 **133.0 s/轮**、稳态中位 **126.7 s**、逐轮 125.0–151.8 s
- 早停口径：**第 28 轮停**（min val_loss 0.5881 @ epoch 12，patience=15）→ 3,825.8 s = **1.063 h**
- 峰值显存 **0.077 GB**；两段训练墙钟合计 10,474.3 s = **2.91 h**
- 确定性复现：两段同 seed 同数据的前 28 轮 `val_loss` **逐位相同（28/28）**

**停止 / 清理（可逆，不删产物）**
```powershell
schtasks /end /tn WORK557_verify
schtasks /end /tn WORK557_verify_full50
schtasks /delete /tn WORK557_verify /f          # 不可逆，产物保留
schtasks /delete /tn WORK557_verify_full50 /f
```

**中断续跑**（checkpoint 在 `experiments/WORK-557/verify/ckpt/last.ckpt`，每轮覆盖）
```powershell
cd C:\Users\MO_Yongkai\multica_workspaces_cloud\workspace-c8faa36a018a\work-557-a221b7629a32\workdir\quant-lab
.venv\Scripts\python.exe -u experiments\WORK-557\work557_verify_run.py --phase full50 --seed 42 --workers 2 --max-epochs 50 --resume
```

## 2026-11-28 晚：seed42 两档（full / no_vq）

| 计划任务 | 跑什么 | 日志 | 结果 |
|---|---|---|---|
| `WORK557_seed42`（**16:53 失败**） | full → no_vq（python.exe） | `logs/seed42_arms.log` | `forrtl: error (200) window-CLOSE event`，主进程 + worker 全灭于 epoch 6 |
| `WORK557_seed42w`（成功启动） | 同上，改 `pythonw.exe`（无控制台） | `logs/seed42_arms_w.log`（stdout 丢，仅 cmd 回显） | full 18:17 训完 stage-1、18:58 退出；no_vq 19:32 退出 |
| 补推理 `work557_infer_only.py` | 两档 stage-2 checkpoint → `preds/<arm>_s42.parquet` | `logs/infer_full_s42.log`、`logs/infer_no_vq_s42.log` | full RankIC .0716 / no_vq .0617 |

**教训**：`python.exe` 在计划任务里会被 console-close 事件打死（MKL Fortran runtime 直接 abort）；
改用 `pythonw.exe`（GUI 子系统、无控制台）后不再复现，**代价是 stdout 全丢**，只能靠产物与 checkpoint 判断进度。

**停止 / 清理**：`schtasks /end /tn WORK557_seed42w`、`schtasks /delete /tn WORK557_seed42w /f`
（`WORK557_seed42` 那次的句柄同样可 /end /delete）

## 2026-11-28 深夜：seed 2024 两档（full / no_vq）

| 计划任务 | 日志 | 结果 |
|---|---|---|
| `WORK557_seed2024w`（pythonw，无控制台） | `logs/seed2024_arms_w.log` | full：20:07 起 → 21:02 stage-1 完 → 21:40 退出；no_vq：21:40 → 22:15 退出 |
| 补推理 `work557_infer_only.py --seed 2024` | 同上 | full RankIC .0700 / no_vq .0655 |

**停止 / 清理**：`schtasks /end /tn WORK557_seed2024w`、`schtasks /delete /tn WORK557_seed2024w /f`

**链修复（2026-11-28 深夜）**
- `audit/recompute_work557.py`：`PriceBook` 的键是 `YYYYMMDD`、recompute 传的是 `YYYY-MM-DD` → 全部取价 miss（`n_pos=0`）。
  加 `_k()` 归一 + `_get()` 双写法回退后，**5 个 run × 6 个字段全部 0 mismatch @1e-6**。
- `scripts/work557_check_integrity.py`：I3 改为按**解析后的日期**比较（两份日历只是 `2006-01-04` vs `20060104` 格式差异），
  I3 从 false → **true**；I4 仍是 5,798 条，全部落在 2012-01-04 起头 20 个交易日（检查读的是 503 时代新浪快照，起点 2012）。
- `scripts/work557_check_lookahead.py`：qlib 缺票导致 `KeyError: sh600001` → 改为按候选序跳过并记录；
  现在能跑完，但 **feature/prior 两轴仍未通过**（标签轴 0 差）。
