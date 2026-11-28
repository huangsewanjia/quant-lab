@echo off
rem WORK-557：只跑 GRU 基线 seed 42（把 H2 补到 2/2）。pythonw 无控制台，避免 window-CLOSE 打死。
rem 清理：schtasks /end /tn WORK557_gru42w   /   schtasks /delete /tn WORK557_gru42w /f
setlocal
cd /d C:\Users\MO_Yongkai\multica_workspaces_cloud\workspace-c8faa36a018a\work-557-a221b7629a32\workdir\quant-lab
set MLFLOW_DISABLE_AGENT_HINT=1
set PYTHONUNBUFFERED=1
set WANDB_MODE=disabled
set LOG=experiments\WORK-557\logs\gru42_w.log
echo === gru s42 start %date% %time% ===>> %LOG%
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader >> %LOG% 2>&1
start /wait "" .venv\Scripts\pythonw.exe -u scripts\work557_baseline.py --kind gru --seed 42 --epochs 50 --exp WORK-557 --workers 2 >> %LOG% 2>&1
echo === gru s42 exit %time% ===>> %LOG%
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader >> %LOG% 2>&1
