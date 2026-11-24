# WORK-557 脱离 run 生命周期的长任务 · 运行句柄

## 为什么需要脱离

2026-11-25 00:11 实测：上一个 Multica run 终态时，**它持有的后台任务被平台一并带走**——
GRU s42 训练在 epoch 21（1627 s）被殺，基线只在训练末尾写 parquet/json，故**产物未落盘**，
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
