@echo off
rem WORK-557 核实运行 Phase 2：关早停，full/seed42 跑满 50 轮，拿「满 50 轮真实墙钟」。
rem 触发条件来自派单 §2.3：阶段一（早停开）实际在 28 轮停，故需另跑满 50 轮。
rem 清理句柄：schtasks /end /tn WORK557_verify_full50
rem           schtasks /delete /tn WORK557_verify_full50 /f
setlocal
cd /d C:\Users\MO_Yongkai\multica_workspaces_cloud\workspace-c8faa36a018a\work-557-a221b7629a32\workdir\quant-lab
set MLFLOW_DISABLE_AGENT_HINT=1
set PYTHONUNBUFFERED=1
set WANDB_MODE=disabled
if not exist experiments\WORK-557\verify mkdir experiments\WORK-557\verify
echo === full50 start %date% %time% ===>> experiments\WORK-557\verify\console_full50.log
.venv\Scripts\python.exe -u experiments\WORK-557\work557_verify_run.py --phase full50 --seed 42 --workers 2 --max-epochs 50 >> experiments\WORK-557\verify\console_full50.log 2>&1
echo === full50 exit rc=%errorlevel% %date% %time% ===>> experiments\WORK-557\verify\console_full50.log
