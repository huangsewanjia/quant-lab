@echo off
rem WORK-557 最小核实运行（2026-11-28 派单）：full/seed42，早停开 -> 若 <=25 轮则再跑满 50 轮。
rem 为什么用计划任务：2026-11-25 实测「run 终态会带走它持有的后台任务」（GRU s42 在 epoch 21 被杀）。
rem 父进程是 Task Scheduler 服务，不在本 run 的进程树里。
rem 清理句柄：schtasks /end /tn WORK557_verify         （可逆）
rem           schtasks /delete /tn WORK557_verify /f   （不可逆，产物保留）
rem 注：stdout 走 console.log，python 自己写 driver.log —— 两个文件不能同一个，
rem     否则 cmd 的 >> 会先占住句柄，python 再以 append 打开就 PermissionError（2026-11-28 实测）。
setlocal
cd /d C:\Users\MO_Yongkai\multica_workspaces_cloud\workspace-c8faa36a018a\work-557-a221b7629a32\workdir\quant-lab
set MLFLOW_DISABLE_AGENT_HINT=1
set PYTHONUNBUFFERED=1
set WANDB_MODE=disabled
if not exist experiments\WORK-557\verify mkdir experiments\WORK-557\verify
echo === verify driver start %date% %time% ===>> experiments\WORK-557\verify\console.log
.venv\Scripts\python.exe -u experiments\WORK-557\work557_verify_driver.py >> experiments\WORK-557\verify\console.log 2>&1
echo === verify driver exit rc=%errorlevel% %date% %time% ===>> experiments\WORK-557\verify\console.log
