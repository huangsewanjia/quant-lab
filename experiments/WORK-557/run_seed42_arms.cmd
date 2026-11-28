@echo off
rem WORK-557 seed42 两档：full（stage-1 早停 + stage-2）→ no_vq（无 stage-1，直接 stage-2）。
rem 派单 2026-11-28 §3：只跑这两档、每档开工前门禁、不许改 vendor、单档 >3 h 硬撑即停。
rem 脱离 run 进程树（2026-11-25 教训：run 终态会带走后台任务）。
rem 清理：schtasks /end /tn WORK557_seed42   /   schtasks /delete /tn WORK557_seed42 /f
setlocal
cd /d C:\Users\MO_Yongkai\multica_workspaces_cloud\workspace-c8faa36a018a\work-557-a221b7629a32\workdir\quant-lab
set MLFLOW_DISABLE_AGENT_HINT=1
set PYTHONUNBUFFERED=1
set WANDB_MODE=disabled
if not exist experiments\WORK-557\logs mkdir experiments\WORK-557\logs
if not exist checkpoints mkdir checkpoints
set LOG=experiments\WORK-557\logs\seed42_arms.log

echo === seed42 arms start %date% %time% ===>> %LOG%
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader >> %LOG% 2>&1

echo === [1/2] full s42 START %time% ===>> %LOG%
.venv\Scripts\python.exe -u scripts\work557_run_arms.py --arm full --seed 42 --epochs 50 --exp WORK-557 --workers 2 --threads 8 >> %LOG% 2>&1
echo === [1/2] full s42 exit rc=%errorlevel% %time% ===>> %LOG%

nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader >> %LOG% 2>&1
echo === [2/2] no_vq s42 START %time% ===>> %LOG%
.venv\Scripts\python.exe -u scripts\work557_run_arms.py --arm no_vq --seed 42 --epochs 50 --exp WORK-557 --workers 2 --threads 8 >> %LOG% 2>&1
echo === [2/2] no_vq s42 exit rc=%errorlevel% %time% ===>> %LOG%

nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader >> %LOG% 2>&1
echo === seed42 arms done %date% %time% ===>> %LOG%
