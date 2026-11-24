@echo off
rem WORK-557 段2/段3/段4 的「脱离 run 生命周期」长任务。
rem 2026-11-25 00:11 实测：上一个 Multica run 终态时，它持有的后台任务被平台一并带走
rem （GRU s42 训练在 epoch 21 被杀、产物未落盘）→ 训练与后处理链必须用 Windows 计划任务
rem 起：父进程是 Task Scheduler 服务，不在本 run 的进程树里，run 终态不会带走它。
rem 清理句柄：schtasks /end /tn WORK557_run && schtasks /delete /tn WORK557_run /f
setlocal
cd /d C:\Users\MO_Yongkai\multica_workspaces_cloud\workspace-c8faa36a018a\work-557-a221b7629a32\workdir\quant-lab
set MLFLOW_DISABLE_AGENT_HINT=1
set PYTHONUNBUFFERED=1
echo === detached run start %date% %time% ===> experiments\WORK-557\run_all.log
.venv\Scripts\python.exe -u scripts\work557_train_all.py --epochs 50 --gru-first --no-wait-gpu --max-run-seconds 7200 --workers 8 >> experiments\WORK-557\run_all.log 2>&1
echo === training driver exit rc=%errorlevel% %date% %time% ===>> experiments\WORK-557\run_all.log
.venv\Scripts\python.exe -u scripts\work557_finish.py >> experiments\WORK-557\run_all.log 2>&1
echo === chain exit rc=%errorlevel% %date% %time% ===>> experiments\WORK-557\run_all.log
