"""WORK-557 适配层：可 pickle 的 collate 函数（不改 vendor/PRISM-VQ）。

上游 `vendor/PRISM-VQ/dataset/dataset.py` 的 `init_data_loader` 把
`float_collate_fn` 定义在函数内部（闭包）。Windows 的 DataLoader 用 spawn
起 worker，必须把 collate_fn 序列化给子进程；局部函数不可 pickle，实测报
`Got pickle error when attempting to start a worker Process` / `EOFError`
（2026-11-26 15:37 smoke）。

把同一个函数搬到模块级即可被 pickle（子进程按 `__module__` 自动 import 本文件）。
行为逐行照抄上游：`default_collate` → 若是张量则 `.float()`。
"""
from torch.utils.data.dataloader import default_collate
import torch


def float_collate_fn(batch):
    """上游 `init_data_loader.<locals>.float_collate_fn` 的模块级等价实现。"""
    batch = default_collate(batch)
    if isinstance(batch, torch.Tensor):
        return batch.float()
    return batch
