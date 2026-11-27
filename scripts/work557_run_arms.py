#!/usr/bin/env python3
"""WORK-557 stage 2 — train one PRISM-VQ ablation arm with one seed.

Reuses the official implementation verbatim:
  vendor/PRISM-VQ/trainer/train_vqvae.py        (FactorVQVAE, stage 1)
  vendor/PRISM-VQ/trainer/train_ypred.py        (GenerateReturn, stage 2 full)
  vendor/PRISM-VQ/trainer/train_ypred_wo_moe.py (GenerateReturn, no_moe)
  vendor/PRISM-VQ/trainer/train_ypred_wo_prior.py (GenerateReturn, no_prior)
  vendor/PRISM-VQ/trainer/train_ypred_wo_stage1.py (GenerateReturn, no_vq)
  vendor/PRISM-VQ/module/**                      (model code)
  vendor/PRISM-VQ/utils/test.py                  (run_inference, RankIC)

Only the data path is ours (preregistration.md §1.1 / CHANGES-vs-upstream.md):
the dataset returns the same (T=20, 158 feature ∥ 6 prior ∥ 10 label) layout
that qlib's TSDataSampler produces, so the official modules are untouched.

Usage:
  python scripts/work503_run_arms.py --arm full --seed 42 --epochs 8
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

# Headless by design.  pytorch_lightning imports matplotlib at import time,
# and upstream draws a codebook histogram with pyplot at every epoch end
# (vendor/PRISM-VQ/trainer/train_vqvae.py:98).  With no backend pinned,
# matplotlib here resolves to QtAgg, and this box's PyQt6 has no Qt6 plugin
# directory (PyQt6/Qt6/plugins is missing), so plt.figure() blocks on a
# modal "This application failed to start because no Qt platform plugin
# could be initialized." dialog and the run never reaches its epoch summary.
# Must be set before matplotlib is first imported; wandb.Image(plt) still
# works with Agg.  Regression test: experiments/WORK-557/_headless_mpl_test.py
if not os.environ.get("MPLBACKEND"):
    os.environ["MPLBACKEND"] = "Agg"

import numpy as np
import pandas as pd
import torch
import pytorch_lightning as pl

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENDOR = os.path.join(REPO, "vendor", "PRISM-VQ")
sys.path.insert(0, VENDOR)

N_FEATURES = 158
N_PRIOR = 6
N_LABEL = 10
T = 20


# --------------------------------------------------------------------------
# dataset
# --------------------------------------------------------------------------
class Work503Dataset:
    """qlib-TSDataSampler-compatible: get_index() + __getitem__ -> (T, 174)."""

    def __init__(self, ds_dir, index_df, use_prior=True, cs_label=True,
                 dropna_label=True):
        self.ds_dir = ds_dir
        self.use_prior = use_prior
        self.cs_label = cs_label
        # The per-instrument arrays are indexed by the GLOBAL trading calendar
        # that work503_build_dataset.py wrote next to them.  Building the
        # position map from the *segment* calendar instead would silently read
        # another day's features (preregistration_v1.2.md §B).
        with open(os.path.join(ds_dir, "calendar.txt"), encoding="utf-8") as fh:
            self.cal = pd.DatetimeIndex(
                [pd.Timestamp(line.strip()) for line in fh if line.strip()])
        self.cal_pos = {d: i for i, d in enumerate(self.cal)}
        self._feat, self._prior, self._label = {}, {}, {}

        df = index_df.reset_index()
        df = df.sort_values(["datetime", "instrument"], kind="mergesort")
        rows = list(zip(df["instrument"].to_numpy(), df["datetime"].to_numpy()))
        if dropna_label:
            keep = []
            for i, (inst, dt) in enumerate(rows):
                lab = self._get_label(inst, dt)
                if lab is not None and not np.isnan(lab).any():
                    keep.append(i)
            rows = [rows[i] for i in keep]
            df = df.iloc[keep]
        self.rows = rows
        self.index = df.set_index(["datetime", "instrument"])

    # -- lazy per-instrument arrays --------------------------------------
    def _load(self, inst):
        if inst not in self._feat:
            # memory-mapped: the feature matrices are ~2 MB per instrument and
            # DataLoader workers would otherwise each hold a private copy.
            self._feat[inst] = np.load(os.path.join(
                self.ds_dir, "feat", f"{inst}.npy"), mmap_mode="r")
            self._prior[inst] = np.load(os.path.join(
                self.ds_dir, "prior", f"{inst}.npy"), mmap_mode="r")
            sub = "label_cs" if self.cs_label else "label"
            self._label[inst] = np.load(os.path.join(
                self.ds_dir, sub, f"{inst}.npy"), mmap_mode="r")
        return self._feat[inst], self._prior[inst], self._label[inst]

    def _get_label(self, inst, dt):
        lab = self._label.get(inst)
        if lab is None:
            _, _, lab = self._load(inst)
        t = self.cal_pos.get(dt)
        if t is None:
            return None
        return np.asarray(lab[t])

    def get_index(self):
        # qlib-compatible: a MultiIndex ordered (datetime, instrument) so that
        # the official DailyBatchSamplerRandom groups by date and
        # run_inference's sortlevel(0) keeps the batch order.
        return self.index.index

    def __len__(self):
        return len(self.rows)

    # ---- pickling ---------------------------------------------------------
    # Windows 的 DataLoader 用 spawn 起 worker，整个对象要 pickle 一遍。
    # __init__ 里为筛 dropna_label 标签，会把全部 911 只的 memmap 装进
    # self._feat/_prior/_label；numpy 对 memmap 的 pickle 是**整份拷贝数据**，
    # 于是每个 worker、每个 epoch 都要搬 2.65 GB（实测 GRU epoch1 = 364 s，
    # epoch0 = 183 s，正是这个搬运成本）。
    # 这三个字典是**惰性缓存**，子进程里按需重开即可，故 pickle 时丢掉它们。
    def __getstate__(self):
        st = dict(self.__dict__)
        st["_feat"] = {}
        st["_prior"] = {}
        st["_label"] = {}
        return st

    def __setstate__(self, st):
        self.__dict__.update(st)

    def __getitem__(self, i):
        inst, dt = self.rows[i]
        F, P, L = self._load(inst)
        t = self.cal_pos[dt]
        lo = max(0, t - T + 1)
        win = np.asarray(F[lo:t + 1], dtype="float32")
        if win.shape[0] < T:
            pad = np.full((T - win.shape[0], win.shape[1]), np.nan, dtype="float32")
            win = np.concatenate([pad, win], axis=0)
        # qlib fillna_type='ffill+bfill' along the time axis
        win = _ffill_bfill(win)
        out = np.zeros((T, N_FEATURES + N_PRIOR + N_LABEL), dtype="float32")
        out[:, :N_FEATURES] = win
        pr = np.asarray(P[t]) if self.use_prior else np.zeros(N_PRIOR, dtype="float32")
        out[T - 1, N_FEATURES:N_FEATURES + N_PRIOR] = pr
        out[T - 1, N_FEATURES + N_PRIOR:] = np.asarray(L[t])
        return out


def _ffill_bfill(x: np.ndarray) -> np.ndarray:
    """逐位等价于「qlib ffill+bfill + nan_to_num」，但去掉两次无效的 fill。

    原实现每样本做两次 take_along_axis + maximum.accumulate，而紧随其后的
    `x[mask] = np.nan` 又把刚填上的值还原成 NaN —— 两次 fill 的净效果为零，
    整段等价于 nan_to_num(...)。但代价是 ~77 µs/窗口，80 万窗口/轮 → 单轮 ~60 s，
    是训练的主瓶颈。

    等价性已用**真实数据**逐位验证（378 个实际窗口 + 全 NaN 列 / ±inf / 全零 3 个
    边界例）：max_abs_diff = 0.0，max|差| 严格为 0；证据
    experiments/WORK-557/ffill_equivalence.json（脚本 work557_ffill_equiv.py）。
    实测单窗口 77.2 µs -> 15.6 µs（4.9x）。
    """
    return np.nan_to_num(x.astype("float64"), nan=0.0, posinf=0.0,
                         neginf=0.0).astype("float32")


def patch_sampler_init():
    """修复上游 `DailyBatchSamplerRandom.__init__` 与 torch>=2.x 的不兼容。

    上游 `vendor/PRISM-VQ/dataset/dataset.py:11` 写着
        class DailyBatchSamplerRandom(Sampler):
            def __init__(self, data_source, shuffle=False):
                super().__init__(data_source)
    但 torch 自 2.x 起把 `Sampler.__init__` 收回到 object（不再接受 data_source），
    于是本机 torch 2.11.0 上每档都在 `init_data_loader` 处
    `TypeError: object.__init__() takes exactly one argument` 秒退（2026-11-25 01:34/
    02:48 共 8 次实测，见 experiments/WORK-557/training_log.txt）。

    处置：在适配层把 `__init__` 原体**逐行照抄**、只去掉 `super().__init__(data_source)`
    这一行。不改 vendor/ 任何文件，不碰模型结构与采样逻辑（daily_count / daily_index /
    dates 三个字段与迭代顺序逐字不变），故 batch 组成与顺序不变，数值口径不变。
    """
    import numpy as _np
    import pandas as _pd
    import dataset.dataset as _ds
    if getattr(_ds, "_work557_sampler_init", False):
        return

    def __init__(self, data_source, shuffle=False):  # noqa: N807
        # torch>=2：Sampler 不再有 __init__(data_source)，故不再调用 super()
        self.data_source = data_source
        self.shuffle = shuffle

        self.index_df = self.data_source.get_index()
        datetime_level = self.index_df.names.index("datetime")
        daily_groups = _pd.Series(self.index_df.values).groupby(
            self.index_df.get_level_values(datetime_level))

        self.daily_count = daily_groups.size().values
        self.daily_index = _np.roll(_np.cumsum(self.daily_count), 1)
        self.daily_index[0] = 0
        self.dates = daily_groups.groups.keys()

    _ds.DailyBatchSamplerRandom.__init__ = __init__
    _ds._work557_sampler_init = True


def patch_data_loader_collate():
    """让上游 `init_data_loader` 在 `num_workers>0` 下可用（Windows spawn）。

    上游把 `float_collate_fn` 定义在 `init_data_loader` **内部**（闭包），而
    DataLoader 在 num_workers>0 时必须把 collate_fn pickle 给子进程 ——
    局部函数不可 pickle，于是实测报
    `Got pickle error when attempting to start a worker Process` → `EOFError`
    （2026-11-26 15:37 smoke，日志 experiments/WORK-557/logs/smoke_full_s42.log）。

    处置：适配层把 `init_data_loader` 原体**逐行照抄**，只把 collate 提升为模块级
    函数（可 pickle）。不碰 vendor/，Sampler / 参数 / 返回值逐字不变。
    """
    import dataset.dataset as _ds
    if getattr(_ds, "_work557_collate", False):
        return
    # 模块级函数：spawn 子进程能按 __module__ 自动 import（scripts/ 在 sys.path）
    from work557_dl_adapter import float_collate_fn as _float_collate_fn

    def init_data_loader(handler, shuffle, num_workers=0, index=False):  # noqa: N807
        sampler = _ds.DailyBatchSamplerRandom(handler, shuffle)
        num_batches_per_epoch = len(sampler)
        data_loader = _ds.DataLoader(handler,
                                     batch_sampler=sampler,
                                     pin_memory=True,
                                     num_workers=num_workers,
                                     drop_last=False,
                                     collate_fn=_float_collate_fn)
        if index is True:
            return data_loader, handler, num_batches_per_epoch
        return data_loader, num_batches_per_epoch

    _ds.init_data_loader = init_data_loader
    _ds._work557_collate = True


def patch_persistent_workers():
    """给上游 dataset.dataset 的 DataLoader 打开 persistent_workers。

    上游 init_data_loader 没开 persistent_workers：Windows 的 DataLoader 用
    spawn 起 worker，默认每个 epoch 重建一遍 —— 每个子进程要重新 import 主模块
    （qlib 约 15–20 s）并接收一份被 pickle 的 handler。配合 Work503Dataset 的
    __getstate__（丢掉惰性 memmap 缓存）后，每次重建只剩一次小 pickle。

    persistent_workers 只改 worker 生命周期：sampler 在主进程迭代，DataLoader 按
    sampler 的顺序还原 batch，**batch 组成与顺序不变**，故数值口径不变。
    """
    import dataset.dataset as _ds
    if getattr(_ds, "_work557_persistent_workers", False):
        return
    _orig = _ds.DataLoader

    def _dl(*a, **kw):
        if kw.get("num_workers", 0) and not kw.get("persistent_workers", False):
            kw["persistent_workers"] = True
        return _orig(*a, **kw)

    _ds.DataLoader = _dl
    _ds._work557_persistent_workers = True




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

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
def build_config(arm: str, seed: int, epochs: int, ckpt_name: str,
                 exp: str = "WORK-557") -> dict:
    import yaml
    with open(os.path.join(VENDOR, "configs", "config.yaml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg["vqvae"]["num_prior_factors"] = N_PRIOR
    cfg["vqvae"]["num_features"] = N_FEATURES
    cfg["vqvae"]["seq_len"] = T
    cfg["vqvae"]["predictor"]["pred_len"] = N_LABEL
    cfg["predictor"]["pred_len"] = 20
    cfg["predictor"]["target_day"] = 5
    cfg["predictor"]["num_features"] = N_FEATURES
    cfg["predictor"]["n_expert"] = 2          # CSI 300 setting (paper §5.1.4)
    cfg["predictor"]["k"] = 1                # half of n_expert (top-1)
    cfg["predictor"]["transformer"]["num_heads"] = 2   # CSI 300
    cfg["predictor"]["aux_weight"] = 0.01    # CSI 300
    cfg["predictor"]["aux_imp"] = 3
    cfg["predictor"]["use_prior"] = True
    cfg["predictor"]["saved_model"] = ckpt_name
    cfg["data"]["universe"] = "csi300"
    cfg["data"]["data_path"] = os.path.join(REPO, "data", exp, "ds")
    cfg["train"]["seed"] = seed
    cfg["train"]["device"] = "cuda"
    cfg["train"]["accelerator"] = "gpu"
    cfg["train"]["gpu_counts"] = 1
    cfg["train"]["num_workers"] = 1
    cfg["train"]["num_epochs"] = epochs
    cfg["train"]["precision"] = 32
    cfg["train"]["gradient_clip_val"] = 1
    cfg["train"]["early_stopping"]["patience"] = 15
    cfg["train"]["early_stopping"]["monitor"] = "val_loss"
    cfg["train"]["save_dir"] = "checkpoints"
    cfg["train"]["save_res"] = "res_work503"
    cfg["train"]["project_name"] = "WORK-557-PRISM-VQ"
    return cfg


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True,
                    choices=["full", "no_vq", "no_moe", "no_prior"])
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--exp", default="WORK-557")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out-suffix", default="",
                    help="suffix for the output file names.  A reproducibility "
                         "re-run must not overwrite the official artefacts, so "
                         "it writes <arm>_s<seed><suffix>.parquet instead. "
                         "Default '' keeps the official names.")
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    os.environ.setdefault("WANDB_MODE", "disabled")
    os.environ.setdefault("HYDRA_FULL_ERROR", "1")

    # The upstream trainers log the codebook utilisation with
    # `wandb.log({"Codebook Utilization": wandb.Image(plt)})` at the end of
    # every epoch (trainer/train_vqvae.py:98).  Upstream runs them under a
    # WandbLogger, which calls wandb.init() implicitly; our runner disables
    # the logger, so the initialisation is done here instead (no network:
    # WANDB_MODE=disabled).  Without it the stage-1 run dies at the end of
    # the first epoch with "You must call wandb.init() before log()".
    import wandb
    try:
        wandb.init(mode="disabled", project="WORK-557-PRISM-VQ",
                   dir=os.path.join(REPO, "data", args.exp, "logs"))
    except Exception as exc:                       # pragma: no cover
        print(f"wandb.init failed ({exc}); stubbing wandb.log")
        wandb.log = lambda *a, **k: None
    os.chdir(VENDOR)          # get_root_dir() / checkpoint paths

    exp_dir = os.path.join(REPO, "experiments", args.exp)
    ds_dir = os.path.join(REPO, "data", args.exp, "ds")
    out_dir = os.path.join(exp_dir, "preds")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(VENDOR, "checkpoints"), exist_ok=True)

    import pytorch_lightning as pl

    # 上游 DataLoader 没开 persistent_workers（Windows 上每轮重建 worker），
    # 这里从外部打补丁，不改 vendor/ 里的任何文件。
    patch_persistent_workers()
    # torch>=2 的 Sampler API 变更（2026-11-25 8 次秒退的根因）
    patch_sampler_init()
    # 闭包 collate_fn 不可 pickle（num_workers>0 时子进程起不来）
    patch_data_loader_collate()

    # 补丁必须在 import 之前打好：`from ... import init_data_loader` 会把当时
    # 已绑定的同名对象固化到本地名字，之后再改模块属性不影响它（2026-11-26 15:39
    # 实测：补丁打了仍报 `Can't get local object 'init_data_loader.<locals>...'`）。
    from dataset.dataset import init_data_loader

    # The seed has to initialise the global RNGs BEFORE the model is built and
    # before the per-day batch order is drawn.  Upstream stage2.py does the
    # same (`seed_everything(cfg.train.seed)` + `pl.seed_everything(...,
    # workers=True)`); without it the "seed" would only label an unseeded
    # random draw and the run could not be reproduced from the seed.
    from utils import seed_everything
    seed_everything(args.seed)
    pl.seed_everything(args.seed, workers=True)

    sample_index = pd.read_parquet(os.path.join(ds_dir, "sample_index.parquet"))
    sample_index["datetime"] = pd.to_datetime(sample_index["datetime"])
    segs = {}
    for seg in ("train", "valid", "test"):
        sub = sample_index[sample_index["segment"] == seg].copy()
        sub = sub.sort_values(["datetime", "instrument"], kind="mergesort")
        segs[seg] = sub.set_index(["datetime", "instrument"], drop=True)
        print(f"segment {seg}: {len(sub)} samples")

    use_prior = args.arm != "no_prior"
    train_ds = Work503Dataset(ds_dir, segs["train"], use_prior, cs_label=True)
    valid_ds = Work503Dataset(ds_dir, segs["valid"], use_prior, cs_label=True)
    test_ds = Work503Dataset(ds_dir, segs["test"], use_prior, cs_label=False,
                              dropna_label=False)

    train_loader, n_batches = init_data_loader(train_ds, shuffle=True,
                                                num_workers=args.workers)
    valid_loader, _ = init_data_loader(valid_ds, shuffle=False, num_workers=0)
    test_loader, _ = init_data_loader(test_ds, shuffle=False, num_workers=0)
    print(f"train batches/epoch: {n_batches}")

    # The stage-1 VQ-VAE is identical for the arms that use it (full /
    # no_moe / no_prior all load the same pre-trained stage-1 checkpoint,
    # exactly as the upstream ablation scripts do), so it is trained once
    # per seed and shared.
    ckpt_name = f"stage1_s{args.seed}.pt"
    ckpt_path = os.path.join(VENDOR, "checkpoints", ckpt_name)
    cfg = build_config(args.arm, args.seed, args.epochs, ckpt_name, args.exp)

    timer = EpochTimer()
    t0 = time.time()
    log = {"arm": args.arm, "seed": args.seed, "epochs": args.epochs,
           "n_train": len(train_ds), "n_valid": len(valid_ds),
           "n_test": len(test_ds), "batches": n_batches}

    # ---- stage 1 -------------------------------------------------------
    if args.arm != "no_vq" and not os.path.exists(ckpt_path):
        from trainer.train_vqvae import FactorVQVAE
        m1 = FactorVQVAE(cfg, T_max=n_batches * args.epochs)
        ck = pl.callbacks.ModelCheckpoint(
            dirpath=os.path.join(VENDOR, "checkpoints"), save_top_k=1,
            monitor="val_loss", mode="min",
            filename=f"stage1_s{args.seed}")
        es = pl.callbacks.EarlyStopping(monitor="val_loss", mode="min",
                                        patience=15, min_delta=1e-5)
        tr = pl.Trainer(max_epochs=args.epochs, accelerator="gpu", devices=1,
                        precision=32, gradient_clip_val=1, logger=False,
                        callbacks=[ck, es, timer], enable_progress_bar=False,
                        enable_model_summary=False, deterministic=True)
        tr.fit(m1, train_dataloaders=train_loader, val_dataloaders=valid_loader)
        best = ck.best_model_path or os.path.join(
            VENDOR, "checkpoints", f"stage1_s{args.seed}.ckpt")
        state = torch.load(best, map_location="cpu")
        torch.save({"state_dict": state["state_dict"] if "state_dict" in state
                    else state}, ckpt_path)
        log["stage1_best"] = str(best)
        log["stage1_seconds"] = round(time.time() - t0, 1)
        log["stage1_epochs_ran"] = len(timer.history)
        log["stage1_early_stopped_epoch"] = (
            int(es.stopped_epoch) + 1 if getattr(es, "stopped_epoch", 0) else None)
        log["stage1_epoch_history"] = list(timer.history)
        print(f"stage1 done in {log['stage1_seconds']}s", flush=True)
    elif args.arm != "no_vq":
        log["stage1_reused"] = ckpt_path
        print(f"stage1 reused: {ckpt_path}")

    # ---- stage 2 -------------------------------------------------------
    if args.arm == "full":
        from trainer.train_ypred import GenerateReturn
    elif args.arm == "no_moe":
        from trainer.train_ypred_wo_moe import GenerateReturn
    elif args.arm == "no_prior":
        from trainer.train_ypred_wo_prior import GenerateReturn
    else:
        from trainer.train_ypred_wo_stage1 import GenerateReturn

    m2 = GenerateReturn(cfg, T_max=n_batches * args.epochs)
    ck2 = pl.callbacks.ModelCheckpoint(
        dirpath=os.path.join(VENDOR, "checkpoints"), save_top_k=1,
        monitor="val_loss", mode="min",
        filename=f"stage2_{args.arm}_s{args.seed}")
    es2 = pl.callbacks.EarlyStopping(monitor="val_loss", mode="min",
                                     patience=15, min_delta=1e-5)
    timer2 = EpochTimer()
    tr2 = pl.Trainer(max_epochs=args.epochs, accelerator="gpu", devices=1,
                     precision=32, gradient_clip_val=1, logger=False,
                     callbacks=[ck2, es2, timer2], enable_progress_bar=False,
                     enable_model_summary=False, deterministic=True)
    tr2.fit(m2, train_dataloaders=train_loader, val_dataloaders=valid_loader)
    best2 = ck2.best_model_path
    if best2:
        m2 = type(m2).load_from_checkpoint(best2, config=cfg,
                                           T_max=n_batches * args.epochs)
    # upstream stage2.py freezes the VQ-VAE before the inference
    if hasattr(m2, "freeze_vqvae"):
        m2.freeze_vqvae()
    m2.eval()
    log["stage2_seconds"] = round(time.time() - t0, 1)
    log["epoch_history"] = list(timer2.history)
    log["epochs_ran"] = len(timer2.history)
    log["early_stopped_epoch"] = (int(es2.stopped_epoch) + 1
                                     if getattr(es2, "stopped_epoch", 0) else None)
    if torch.cuda.is_available():
        log["peak_vram_gb"] = round(torch.cuda.max_memory_allocated() / 1024 ** 3, 3)
    log["device"] = ("cuda:" + torch.cuda.get_device_name(0)) if torch.cuda.is_available() else "cpu"

    # ---- inference ------------------------------------------------------
    from utils.test import run_inference
    df, rankic, icir = run_inference(m2, test_loader, cfg)
    df = df.reset_index()
    df.columns = ["datetime", "instrument", "score", "label"]
    out_path = os.path.join(out_dir, f"{args.arm}_s{args.seed}{args.out_suffix}.parquet")
    df.to_parquet(out_path)
    log["metrics"] = {k: float(v) for k, v in icir.items()}
    log["preds"] = out_path
    log["out_suffix"] = args.out_suffix
    log["total_seconds"] = round(time.time() - t0, 1)
    json.dump(log, open(os.path.join(out_dir,
                                     f"{args.arm}_s{args.seed}{args.out_suffix}.json"),
                        "w"), ensure_ascii=False, indent=1)
    print(json.dumps(log, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
