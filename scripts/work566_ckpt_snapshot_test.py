#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WORK-566 · B3 回归测试：checkpoint「快照」必须与活参数**不共享存储**。

背景（2026-11-25 第 3 轮独立复审 B3，实测坐实）：
`Module.state_dict()` 返回的张量与活参数**共享存储**；优化器更新与 `ema_update`
（`pe.mul_(decay).add_(po, alpha=1-decay)`）都是 **in-place** →
若把 `state_dict()` 直接存进 `best`，训练继续推进时 `best[1]` 跟着变，
训练结束后 `torch.save(best[1])` 落盘的其实是**最后一轮**权重，
而不是预注册 §10.2 冻结的「验证子集上 L_anc + L^impl_rev 最小者」。

本测试用**同款结构**（Linear + EMA + AdamW + in-place EMA）复现旧写法并断言其**会失败**，
再断言修后的 `snapshot_state` 通过 —— 即「先红后绿」的回归防线。

运行：python scripts/work566_ckpt_snapshot_test.py
"""
import os
import sys

import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from work566_train import snapshot_state  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" —— {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


class Tiny(nn.Module):
    def __init__(self):
        super().__init__()
        self.lin = nn.Linear(8, 8)
        self.ema = nn.Linear(8, 8)
        for p in self.ema.parameters():
            p.requires_grad_(False)

    def ema_update(self, online, ema, decay):
        with torch.no_grad():
            for pe, po in zip(ema.parameters(), online.parameters()):
                pe.mul_(decay).add_(po, alpha=1 - decay)


def main():
    torch.manual_seed(0)
    print("=== 第 1 段：旧写法（红）——必须能复现「快照被训练推进改写」===")
    t = Tiny()
    opt = torch.optim.AdamW(t.lin.parameters(), lr=1e-2)
    old_snapshot = {"lin": t.lin.state_dict(), "ema": t.ema.state_dict()}
    m_k = torch.zeros(8)
    old_snapshot["m_k"] = m_k                     # 旧写法：直接存张量对象
    before_w = float(old_snapshot["lin"]["weight"][0, 0])
    before_mk = float(old_snapshot["m_k"][0])
    for _ in range(3):                            # 训练推进：in-place 更新
        opt.zero_grad()
        t.lin(torch.randn(4, 8)).sum().backward()
        opt.step()
        t.ema_update(t.lin, t.ema, 0.9)
        m_k.mul_(0.9).add_(torch.ones(8), alpha=0.1)
    check("旧写法的权重快照被训练推进改写（= B3 的病根）",
          float(old_snapshot["lin"]["weight"][0, 0]) != before_w)
    check("旧写法的 m_k 句柄被 in-place 更新改写",
          float(old_snapshot["m_k"][0]) != before_mk)

    print("=== 第 2 段：新写法（绿）——snapshot_state 必须给出独立副本 ===")
    t2 = Tiny()
    opt2 = torch.optim.AdamW(t2.lin.parameters(), lr=1e-2)
    m_k2 = torch.zeros(8)
    v_k2 = torch.ones(8)
    snap = snapshot_state(t2.lin, t2.ema, t2.lin, t2.ema, t2.lin,
                          m_k2, v_k2, 100, 1.234)
    w_before = float(snap["enc"]["weight"][0, 0])
    mk_before = float(snap["m_k"][0])
    for _ in range(3):
        opt2.zero_grad()
        t2.lin(torch.randn(4, 8)).sum().backward()
        opt2.step()
        t2.ema_update(t2.lin, t2.ema, 0.9)
        m_k2.mul_(0.9).add_(torch.ones(8), alpha=0.1)
        v_k2.mul_(0.99).add_(torch.ones(8), alpha=0.01)
    check("新写法的权重快照不随训练推进改变",
          float(snap["enc"]["weight"][0, 0]) == w_before)
    check("新写法的 m_k 不随 in-place 更新改变",
          float(snap["m_k"][0]) == mk_before)
    check("新写法的 v_k 不随 in-place 更新改变", float(snap["v_k"][0]) == 1.0)
    check("快照带 selected=best_vloss 标记（供 --resume-rep 校验）",
          snap.get("selected") == "best_vloss")
    check("快照记录 vloss 与 upd（可回读自检）",
          snap.get("vloss") == 1.234 and snap.get("upd") == 100)

    print("=== 第 3 段：落盘 → 回读，确认存下来的是快照本身 ===")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "ck.pt")
        torch.save(snap, p)
        back = torch.load(p, map_location="cpu", weights_only=False)
        check("回读的权重与快照逐位相同",
              float(back["enc"]["weight"][0, 0]) == w_before)
        check("回读的 m_k 与快照逐位相同", float(back["m_k"][0]) == mk_before)
        check("回读后 selected 标记仍在", back.get("selected") == "best_vloss")

    print()
    if FAIL:
        print(f"测试失败：{len(FAIL)} 项未通过 → {FAIL}")
        return 1
    print("B3 回归测试全部通过（旧写法确实会坏，新写法确实独立）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
