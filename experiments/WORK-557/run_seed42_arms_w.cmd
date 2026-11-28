@echo off
rem WORK-557 seed42 两档（full → no_vq），**无控制台**版本。
rem 为什么用 pythonw：2026-11-28 16:53 第一次拉起时，MKL 的 Fortran runtime 对
rem 「window-CLOSE event」直接 abort（forrtl error 200），主进程与 8 个 DataLoader worker
rem 全灭在 epoch 6。pythonw.exe 属 GUI 子系统、没有控制台，收不到那个事件。
rem 清理：schtasks /end /tn WORK557_seed42w   /   schtasks /delete /tn WORK557_seed42w /f
setlocal
cd /d C:\Users\MO_Yongkai\multica_workspaces_cloud\workspace-c8faa36a018a\work-557-a221b7629a32\workdir\quant-lab
set MLFLOW_DISABLE_AGENT_HINT=1
set PYTHONUNBUFFERED=1
set WANDB_MODE=disabled
if not exist experiments\WORK-557\logs mkdir experiments\WORK-557\logs
if not exist checkpoints mkdir checkpoints
set LOG=experiments\WORK-557\logs\seed42_arms_w.log
set PYW=.venv\Scripts\pythonw.exe

echo === seed42 arms (pythonw) start %date% %time% ===>> %LOG%
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader >> %LOG% 2>&1

echo === [1/2] full s42 START %time% ===>> %LOG%
start /wait "" %PYW% -u scripts\work557_run_arms.py --arm full --seed 42 --epochs 50 --exp WORK-557 --workers 2 --threads 8 >> %LOG% 2>&1
echo === [1/2] full s42 exit %time% ===>> %LOG%

nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader >> %LOG% 2>&1
echo === [2/2] no_vq s42 START %time% ===>> %LOG%
start /wait "" %PYW% -u scripts\work557_run_arms.py --arm no_vq --seed 42 --epochs 50 --exp WORK-557 --workers 2 --threads 8 >> %LOG% 2>&1
echo === [2/2] no_vq s42 exit %time% ===>> %LOG%

nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader >> %LOG% 2>&1
echo === seed42 arms done %date% %time% ===>> %LOG%
