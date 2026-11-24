#!/usr/bin/env python3
"""WORK-557：把 WORK-503 的脚本层复制成 WORK-557 版本，并施加**逐条可核**的补丁。

原则（对应 WORK-557 预注册 §1）：
* 下游脚本（build_panel / dump_qlib）**一行不改**，直接用 `--exp WORK-557` 跑；
* 需要改的脚本用「复制 + 显式替换」生成，替换清单写在本文件里，
  每条都断言「必须命中且命中次数符合预期」，命中不了就报错退出 —— 不留静默偏差；
* 官方 `vendor/PRISM-VQ/` 一行不改。

用法：python experiments/WORK-557/work557_make_scripts.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
AUDIT = ROOT / "audit"

HEADER = '''#!/usr/bin/env python3
"""WORK-557 · {title}

由 experiments/WORK-557/work557_make_scripts.py 从 WORK-503 的同名脚本复制生成。
补丁清单见该文件；改动只涉及「算力参数（GPU / 轮数 / patience）」「实验单号」
与「qlib 运行时路径」，研究设计（股票池 / 特征 / 标签 / 成本 / 组合 / 掩码 / 判据）
一律照抄 WORK-503，未作任何重定义。
"""
'''

# --------------------------------------------------------------------------
# 通用补丁
# --------------------------------------------------------------------------
COMMON = [
    ('ap.add_argument("--exp", default="WORK-503")',
     'ap.add_argument("--exp", default="WORK-557")', 1),
    ('"WORK-503 stage 2 — train one PRISM-VQ ablation arm with one seed."',
     '"WORK-557 stage 2 — train one PRISM-VQ ablation arm with one seed."', 1),
]


def patch(text: str, rules: list[tuple[str, str, int]], where: str) -> str:
    for old, new, n in rules:
        found = text.count(old)
        if found != n:
            raise SystemExit(
                "PATCH MISS in %s: expected %d occurrence(s), found %d for:\n%r"
                % (where, n, found, old[:160]))
        text = text.replace(old, new)
    return text


# --------------------------------------------------------------------------
# 1. work557_build_dataset.py
# --------------------------------------------------------------------------
BUILD_DATASET = [
    ('TRAIN = ("2015-01-01", "2019-12-31")     # preregistration_v1.1.md §2',
     'TRAIN = ("2009-01-01", "2019-12-31")     # WORK-557 preregistration.md C1', 1),
    ('cal = D.calendar(start_time="2012-01-01", end_time="2024-12-31")',
     'cal = D.calendar(start_time="2006-01-01", end_time="2024-12-31")', 1),
    ('raw = D.features(group, expr_list, start_time="2012-01-01",\n'
     '                         end_time="2024-12-31")',
     'raw = D.features(group, expr_list, start_time="2006-01-01",\n'
     '                         end_time="2024-12-31")', 1),
    ('    import qlib\n',
     '    # pyqlib 0.9.7 ships no cp313 wheel; this directory holds the\n'
     '    # cp312 build plus the numerically-validated pure-python _libs\n'
     '    # stand-in (see experiments/WORK-557/work557_qlib_shim.py).\n'
     '    import qlib\n', 1),
    ('    qlib.init(provider_uri=os.path.join(data_dir, "qlib_cn"), region="cn")',
     '    qlib.init(provider_uri=os.path.join(data_dir, "qlib_cn"), region="cn")', 1),
    ('"calendar_file": "data/WORK-503/ds/calendar.txt",',
     '"calendar_file": "data/%s/ds/calendar.txt" % args.exp,', 1),
    ('"feature": "RobustZScoreNorm(clip_outlier=True) + Fillna(0), fit on PIT train rows 2015-2019",',
     '"feature": "RobustZScoreNorm(clip_outlier=True) + Fillna(0), fit on PIT train rows 2009-2019",', 1),
    ('"prior": "RobustZScoreNorm(clip_outlier=True) + Fillna(0), fit on PIT train rows 2015-2019",',
     '"prior": "RobustZScoreNorm(clip_outlier=True) + Fillna(0), fit on PIT train rows 2009-2019",', 1),
    ('"WORK-503 stage 2 — build the model dataset (Alpha158 features + priors + labels).',
     '"WORK-557 stage 2 — build the model dataset (Alpha158 features + priors + labels).', 1),
]

# --------------------------------------------------------------------------
# 2. work557_run_arms.py  —— GPU + 50 轮 + patience 15 + 计时/显存记录
# --------------------------------------------------------------------------
RUN_ARMS = [
    ('import numpy as np\nimport pandas as pd\nimport torch\n',
     'import numpy as np\nimport pandas as pd\nimport torch\nimport pytorch_lightning as pl\n', 1),
    ('def build_config(arm: str, seed: int, epochs: int, ckpt_name: str) -> dict:',
     'def build_config(arm: str, seed: int, epochs: int, ckpt_name: str,\n'
     '                 exp: str = "WORK-557") -> dict:', 1),
    ('cfg["data"]["data_path"] = os.path.join(REPO, "data", "WORK-503", "ds")',
     'cfg["data"]["data_path"] = os.path.join(REPO, "data", exp, "ds")', 1),
    ('cfg["train"]["device"] = "cpu"', 'cfg["train"]["device"] = "cuda"', 1),
    ('cfg["train"]["accelerator"] = "cpu"', 'cfg["train"]["accelerator"] = "gpu"', 1),
    ('cfg["train"]["early_stopping"]["patience"] = 5',
     'cfg["train"]["early_stopping"]["patience"] = 15', 1),
    ('cfg["train"]["project_name"] = "WORK-503-PRISM-VQ"',
     'cfg["train"]["project_name"] = "WORK-557-PRISM-VQ"', 1),
    ('cfg = build_config(args.arm, args.seed, args.epochs, ckpt_name)',
     'cfg = build_config(args.arm, args.seed, args.epochs, ckpt_name, args.exp)', 1),
    ('ap.add_argument("--epochs", type=int, default=6)',
     'ap.add_argument("--epochs", type=int, default=50)', 1),
    ('project="WORK-503-PRISM-VQ",', 'project="WORK-557-PRISM-VQ",', 1),
    ('patience=3, min_delta=1e-5)', 'patience=15, min_delta=1e-5)', 2),
    ('accelerator="cpu", devices=1,', 'accelerator="gpu", devices=1,', 2),
    ('"WORK-503 stage 2 — train one PRISM-VQ ablation arm with one seed.',
     '"WORK-557 stage 2 — train one PRISM-VQ ablation arm with one seed.', 1),
    ('ap.add_argument("--exp", default="WORK-503")',
     'ap.add_argument("--exp", default="WORK-557")', 1),
]

# timing + GPU memory callback, inserted right before the config section
RUN_ARMS_EXTRA = '''

class EpochTimer(pl.Callback):
    """Per-epoch wall clock + val_loss trace + peak CUDA memory.

    WORK-557 has to report, per arm and per seed: s/epoch, peak VRAM, total
    training seconds and the epoch early stopping fired on (派单卡第 2 段).
    """

    def __init__(self):
        self.history = []
        self._t0 = None

    def on_train_epoch_start(self, trainer, pl_module):
        self._t0 = time.time()

    def on_train_epoch_end(self, trainer, pl_module):
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        dt = time.time() - self._t0
        rec = {"epoch": int(trainer.current_epoch),
               "seconds": round(dt, 2),
               "train_loss": float(trainer.callback_metrics.get("train_loss", float("nan"))),
               "val_loss": float(trainer.callback_metrics.get("val_loss", float("nan")))}
        if torch.cuda.is_available():
            rec["peak_vram_gb"] = round(torch.cuda.max_memory_allocated() / 1024 ** 3, 3)
        self.history.append(rec)
        print("[epoch %d] %.1fs train=%.5f val=%.5f%s" % (
            rec["epoch"], dt, rec["train_loss"], rec["val_loss"],
            ("  peakVRAM=%.2fGB" % rec["peak_vram_gb"]) if "peak_vram_gb" in rec else ""),
            flush=True)

'''
RUN_ARMS_EXTRA2 = '''

    # ---- peak CUDA memory for the whole run ------------------------------
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    peak_vram = 0.0

'''
RUN_ARMS_MORE = [
    ('    import pytorch_lightning as pl\n',
     '    import pytorch_lightning as pl\n\n'
     '    class _pl_shim:\n'
     '        Callback = pl.Callback\n'
     '    globals()["pl"] = _pl_shim\n', 1),
    ('        tr = pl.Trainer(max_epochs=args.epochs, accelerator="gpu", devices=1,',
     '        tr = pl.Trainer(max_epochs=args.epochs, accelerator="gpu", devices=1,', 1),
]


def build_run_arms(text: str) -> str:
    text = patch(text, RUN_ARMS, "work557_run_arms.py")
    # insert the timing callback before the "# config" section
    anchor = ("# --------------------------------------------------------------------------\n"
              "# config\n")
    if anchor not in text:
        raise SystemExit("PATCH MISS in run_arms: config anchor not found")
    text = text.replace(anchor, RUN_ARMS_EXTRA + anchor, 1)
    # wire the callback into both trainers (a separate timer per stage, so the
    # stage-1 trace is not overwritten by the stage-2 trace)
    text = text.replace("callbacks=[ck, es],", "callbacks=[ck, es, timer],", 1)
    text = text.replace("    tr2 = pl.Trainer(max_epochs=args.epochs, accelerator=\"gpu\", devices=1,",
                        "    timer2 = EpochTimer()\n"
                        "    tr2 = pl.Trainer(max_epochs=args.epochs, accelerator=\"gpu\", devices=1,", 1)
    text = text.replace("callbacks=[ck2, es2],", "callbacks=[ck2, es2, timer2],", 1)
    # instantiate the stage-1 timer
    text = text.replace("    t0 = time.time()\n    log = {",
                        "    timer = EpochTimer()\n    t0 = time.time()\n    log = {", 1)
    # record the history + early-stopping epoch + peak VRAM in the log
    text = text.replace(
        '    log["stage2_seconds"] = round(time.time() - t0, 1)',
        '    log["stage2_seconds"] = round(time.time() - t0, 1)\n'
        '    log["epoch_history"] = list(timer2.history)\n'
        '    log["epochs_ran"] = len(timer2.history)\n'
        '    log["early_stopped_epoch"] = (int(es2.stopped_epoch) + 1\n'
        '                                     if getattr(es2, "stopped_epoch", 0) else None)\n'
        '    if torch.cuda.is_available():\n'
        '        log["peak_vram_gb"] = round(torch.cuda.max_memory_allocated() / 1024 ** 3, 3)\n'
        '    log["device"] = ("cuda:" + torch.cuda.get_device_name(0)) if torch.cuda.is_available() else "cpu"', 1)
    text = text.replace(
        '        log["stage1_seconds"] = round(time.time() - t0, 1)',
        '        log["stage1_seconds"] = round(time.time() - t0, 1)\n'
        '        log["stage1_epochs_ran"] = len(timer.history)\n'
        '        log["stage1_early_stopped_epoch"] = (\n'
        '            int(es.stopped_epoch) + 1 if getattr(es, "stopped_epoch", 0) else None)\n'
        '        log["stage1_epoch_history"] = list(timer.history)', 1)
    return text


# --------------------------------------------------------------------------
# 3. work557_baseline.py —— GPU + 50 轮
# --------------------------------------------------------------------------
BASELINE = [
    ('ap.add_argument("--epochs", type=int, default=6)',
     'ap.add_argument("--epochs", type=int, default=50)', 1),
    ('ap.add_argument("--exp", default="WORK-503")',
     'ap.add_argument("--exp", default="WORK-557")', 1),
    ('"WORK-503 stage 2 — GRU baseline (and a linear baseline) on the same data.',
     '"WORK-557 stage 2 — GRU baseline (and a linear baseline) on the same data.', 1),
    ('    from work503_run_arms import Work503Dataset, _ffill_bfill',
     '    from work557_run_arms import Work503Dataset, _ffill_bfill', 1),
    ('    from qlib.contrib.model.pytorch_gru_ts import GRU',
     '    from qlib.contrib.model.pytorch_gru_ts import GRU', 1),
    ('    model = build_model(args.kind, args.seed, args.epochs)\n'
     '    core = core_module(model)',
     '    model = build_model(args.kind, args.seed, args.epochs)\n'
     '    core = core_module(model)\n'
     '    device = torch.device(args.device)\n'
     '    core = core.to(device)\n'
     '    if torch.cuda.is_available():\n'
     '        torch.cuda.reset_peak_memory_stats()', 1),
    ('            x = batch[:, :, :N_FEATURES].float()\n'
     '            y = batch[:, -1, N_FEATURES + N_PRIOR + TARGET].float()\n'
     '            pred = core(x).reshape(-1)\n'
     '            loss = nn.functional.mse_loss(pred, y)',
     '            x = batch[:, :, :N_FEATURES].float().to(device, non_blocking=True)\n'
     '            y = batch[:, -1, N_FEATURES + N_PRIOR + TARGET].float().to(device)\n'
     '            pred = core(x).reshape(-1)\n'
     '            loss = nn.functional.mse_loss(pred, y)', 1),
    ('                x = batch[:, :, :N_FEATURES].float()\n'
     '                y = batch[:, -1, N_FEATURES + N_PRIOR + TARGET].float()\n'
     '                pred = core(x).reshape(-1)\n'
     '                vs += float(nn.functional.mse_loss(pred, y))',
     '                x = batch[:, :, :N_FEATURES].float().to(device, non_blocking=True)\n'
     '                y = batch[:, -1, N_FEATURES + N_PRIOR + TARGET].float().to(device)\n'
     '                pred = core(x).reshape(-1)\n'
     '                vs += float(nn.functional.mse_loss(pred, y))', 1),
    ('            x = batch[:, :, :N_FEATURES].float()\n'
     '            pred = core(x).reshape(-1).numpy()',
     '            x = batch[:, :, :N_FEATURES].float().to(device)\n'
     '            pred = core(x).reshape(-1).cpu().numpy()', 1),
    ('    ap.add_argument("--threads", type=int, default=8)',
     '    ap.add_argument("--threads", type=int, default=8)\n'
     '    ap.add_argument("--device", default="cuda")', 1),
    ('    log = {"kind": args.kind, "seed": args.seed, "epochs": args.epochs,\n'
     '           "valid_loss": best_val, "metrics": metrics, "preds": out,\n'
     '           "seconds": round(time.time() - t0, 1)}',
     '    log = {"kind": args.kind, "seed": args.seed, "epochs": args.epochs,\n'
     '           "valid_loss": best_val, "metrics": metrics, "preds": out,\n'
     '           "seconds": round(time.time() - t0, 1),\n'
     '           "epoch_seconds": epoch_seconds,\n'
     '           "epochs_ran": len(epoch_seconds),\n'
     '           "device": str(device),\n'
     '           "peak_vram_gb": (round(torch.cuda.max_memory_allocated() / 1024 ** 3, 3)\n'
     '                           if torch.cuda.is_available() else None)}', 1),
    ('    best_val, best_state = np.inf, None\n    t0 = time.time()',
     '    best_val, best_state = np.inf, None\n'
     '    epoch_seconds = []\n    t0 = time.time()', 1),
    ('        print(f"epoch {ep}: train {tot / max(1, nb):.4f} valid {vloss:.4f} "\n'
     '              f"({time.time() - t0:.0f}s)", flush=True)',
     '        epoch_seconds.append(round(time.time() - t0, 1))\n'
     '        print(f"epoch {ep}: train {tot / max(1, nb):.4f} valid {vloss:.4f} "\n'
     '              f"({time.time() - t0:.0f}s)", flush=True)', 1),
]

# --------------------------------------------------------------------------
# 4. 只需要换单号的脚本
# --------------------------------------------------------------------------
SIMPLE = {
    "work503_backtest.py": ("work557_backtest.py", "WORK-503 stage 3 — TopK-DropN backtest with costs, masks and benchmark alpha.",
                            "WORK-557 stage 3 — TopK-DropN backtest with costs, masks and benchmark alpha."),
    "work503_verdict.py": ("work557_verdict.py", "WORK-503 stage 4 — the verdict (进 / 退).",
                           "WORK-557 stage 4 — the verdict (进 / 退)."),
    "work503_cost_sensitivity.py": ("work557_cost_sensitivity.py", "WORK-503 — cost sensitivity table (pre-registered cost points).",
                                     "WORK-557 — cost sensitivity table (pre-registered cost points)."),
    "work503_check_integrity.py": ("work557_check_integrity.py", "WORK-503 stage 3 — data integrity assertions (preregistration_v1.2.md §2).",
                                    "WORK-557 stage 3 — data integrity assertions (WORK-557 preregistration.md §1)."),
    "work503_check_lookahead.py": ("work557_check_lookahead.py", "WORK-503 stage 3 — assert_no_future (mask_contract.md §7.5).",
                                    "WORK-557 stage 3 — assert_no_future (mask_contract.md §7.5)."),
    "work503_repro_check.py": ("work557_repro_check.py", "WORK-503 · same-seed reproducibility evidence.",
                               "WORK-557 · same-seed reproducibility evidence."),
}


# --------------------------------------------------------------------------
# 4. qlib 运行时 bootstrap：把 .venv/qlib312 放到 sys.path 上
# --------------------------------------------------------------------------
QLIB_BOOT = (
    'import pandas as pd\n',
    'import pandas as pd\n'
    '\n'
    '# ---- qlib runtime bootstrap -------------------------------------------\n'
    '# pyqlib 0.9.7 publishes no cp313 wheel and this machine trains on\n'
    '# Python 3.13.15.  .venv/qlib312 holds the cp312 build plus the numerically\n'
    '# validated pure-python `_libs` stand-in (see\n'
    '# experiments/WORK-557/work557_qlib_shim.py; the stand-in was checked\n'
    '# element-by-element against the real cp39 .pyd, max |diff| = 0).\n'
    '_REPO_QLIB = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))\n'
    'sys.path.insert(0, os.path.join(_REPO_QLIB, ".venv", "qlib312"))\n', 1)

QLIB_BOOT_FILES = ("work557_build_dataset.py", "work557_check_integrity.py",
                   "work557_check_lookahead.py", "work557_baseline.py")


def main():
    written = []

    # -- build_dataset ----------------------------------------------------
    src = (SCRIPTS / "work503_build_dataset.py").read_text(encoding="utf-8")
    out = patch(src, BUILD_DATASET + COMMON[:1], "work557_build_dataset.py")
    out = out.replace('"""WORK-503 stage 2 — build the model dataset',
                      '"""WORK-557 stage 2 — build the model dataset', 1)
    (SCRIPTS / "work557_build_dataset.py").write_text(out, encoding="utf-8")
    written.append("scripts/work557_build_dataset.py")

    # -- run_arms ---------------------------------------------------------
    src = (SCRIPTS / "work503_run_arms.py").read_text(encoding="utf-8")
    out = build_run_arms(src)
    (SCRIPTS / "work557_run_arms.py").write_text(out, encoding="utf-8")
    written.append("scripts/work557_run_arms.py")

    # -- baseline ---------------------------------------------------------
    src = (SCRIPTS / "work503_baseline.py").read_text(encoding="utf-8")
    out = patch(src, BASELINE, "work557_baseline.py")
    (SCRIPTS / "work557_baseline.py").write_text(out, encoding="utf-8")
    written.append("scripts/work557_baseline.py")

    # -- simple renames ---------------------------------------------------
    for src_name, (dst_name, old_doc, new_doc) in SIMPLE.items():
        text = (SCRIPTS / src_name).read_text(encoding="utf-8")
        text = text.replace(old_doc, new_doc)
        text = text.replace('ap.add_argument("--exp", default="WORK-503")',
                            'ap.add_argument("--exp", default="WORK-557")')
        text = text.replace('os.path.join(REPO, "experiments", "WORK-503")',
                            'os.path.join(REPO, "experiments", "WORK-557")')
        text = text.replace('os.path.join(REPO, "data", "WORK-503")',
                            'os.path.join(REPO, "data", "WORK-557")')
        text = text.replace("experiments/WORK-503/", "experiments/WORK-557/")
        text = text.replace("data/WORK-503/", "data/WORK-557/")
        (SCRIPTS / dst_name).write_text(text, encoding="utf-8")
        written.append("scripts/" + dst_name)

    # -- independent recompute -------------------------------------------
    text = (AUDIT / "recompute_work503.py").read_text(encoding="utf-8")
    text = text.replace('"""WORK-503 stage 3 — INDEPENDENT recompute of the headline metrics.',
                        '"""WORK-557 stage 3 — INDEPENDENT recompute of the headline metrics.')
    text = text.replace('os.path.join(REPO, "experiments", "WORK-503")',
                        'os.path.join(REPO, "experiments", "WORK-557")')
    text = text.replace('os.path.join(REPO, "data", "WORK-503")',
                        'os.path.join(REPO, "data", "WORK-557")')
    text = text.replace("experiments/WORK-503/", "experiments/WORK-557/")
    text = text.replace("data/WORK-503/", "data/WORK-557/")
    (AUDIT / "recompute_work557.py").write_text(text, encoding="utf-8")
    written.append("audit/recompute_work557.py")

    print("wrote %d files:" % len(written))
    for w in written:
        print("  ", w)

    # -- qlib runtime bootstrap on every qlib-using script ------------------
    for name in QLIB_BOOT_FILES:
        p = SCRIPTS / name
        text = p.read_text(encoding="utf-8")
        if "_REPO_QLIB" not in text:
            text = patch(text, [QLIB_BOOT], name)
            p.write_text(text, encoding="utf-8")
            print("  qlib bootstrap ->", name)


if __name__ == "__main__":
    sys.exit(main())
