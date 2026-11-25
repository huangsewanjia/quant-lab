# -*- coding: utf-8 -*-
"""WORK-566 · S2 表征学习 + S3 冻结表征读出（每种子一遍）。

架构与优化严格按论文附录 C/D（降级项见预注册 §8）：
  上下文编码器 E_θ / 目标编码器 T_θ̄ 同构：40 维 → Linear→d_model → summary token + pos →
  pre-norm Transformer(L 层, H 头, FFN) → summary token → LayerNorm → d_z
  先验投影器 A_φ：Linear(16→d_z) → LayerNorm → GELU → Linear(d_z→d_z)
  修正预测器 Q_ψ：concat(h, sg[A_φ̄(P)]) → MLP d_z→d_z→d_z
  损失：L_anc（只更新 A_φ）、L_rev^impl（只更新 E_θ, Q_ψ）；EMA 0.996 / 0.99
  优化：两个 AdamW，lr 1e-4，wd 0.01，clip 1.0，batch 256，epoch × ≤max_updates

读出（附录 D）：s=上下文标准化均值(40) / P(16) / Ẑ^anc(d_z) / Δ̂(d_z) → MLP 或线性 → 标准化 21 日收益；
SmoothL1；AdamW lr 1e-4 wd 0.05；每个 batch = 一个预测日的样本；6 epoch × ≤max_updates；
按验证集 SmoothL1 选 checkpoint。

产物（data/WORK-566/）：
  rep_seed{S}.npz   u 特征（train/valid/test 子集）+ 预测 + checkpoint 元信息
  ckpt_seed{S}_rep.pt / ckpt_seed{S}_{arm}.pt

运行：
  python scripts/work566_train.py --seeds 42 2024
  python scripts/work566_train.py --bench
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "WORK-566")
OUT = os.path.join(REPO, "experiments", "WORK-566")

CTX, H = 126, 21
N_FEAT = 40
PRIOR_DIM = 16


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# ---------------------------------------------------------------- 模型
class FastEncoderLayer(nn.Module):
    """与 ``nn.TransformerEncoderLayer(norm_first=True)`` 前向**逐位等价**的 CPU 加速实现。

    等价性证据：同一权重下 eval 模式逐元素最大差 4.77e-07（float32 舍入量级）；
    **训练模式**（dropout 全开、同一随机种子）逐元素最大差见
    ``experiments/WORK-566/fast_encoder_selftest.json`` 的 ``train_max_abs_diff``。
    做法：权重沿用官方模块（初始化完全一致），只用
    ``F.scaled_dot_product_attention`` 替代内部 attention 路径。
    本机（i3-12100F，无 AMX）实测 3~11× 加速；bf16 更慢，故仍用 fp32。

    **2026-11-25 独立代码审查修正（N2）**：原实现把 dropout 做了 4 次且位置错位
    （attn 输出 2 次、缺 FF 隐层、FF 输出 2 次），训练期有效 dropout 率 0.19 vs 官方 0.10。
    官方 ``nn.TransformerEncoderLayer`` 训练期共 **4** 次 dropout，顺序固定为
      ① ``_sa_block`` 内 ``F.multi_head_attention_forward(need_weights=False)`` 走的
         ``scaled_dot_product_attention(..., dropout_p=self.dropout.p)``——对**注意力权重**；
      ② ``dropout1``——attn 输出；
      ③ ``_ff_block`` 内 ``self.dropout``——FF 隐层；
      ④ ``dropout2``——FF 输出。
    本实现按同一顺序、同一形状调用同一算子，随机数消费顺序与官方一致。
    """

    def __init__(self, layer: nn.TransformerEncoderLayer):
        super().__init__()
        self.layer = layer
        self.E = layer.self_attn.embed_dim
        self.H = layer.self_attn.num_heads
        self.dh = self.E // self.H
        self.p = layer.dropout.p

    @property
    def self_attn(self):
        return self.layer.self_attn

    def forward(self, x, **_kw):
        L = self.layer
        B, T, E = x.shape
        # ---- 官方路径逐位复刻（torch 2.15）----
        # 官方 TransformerEncoder 不转置 → 层收到的就是 (B, T, E)；
        # 官方 _sa_block → MHA(batch_first=True) 内部 transpose(1,0) → (T, B, E) 再做
        # in_proj / chunk / reshape(T, B*H, dh) / transpose(0,1) / view(B, H, T, dh)。
        # 注意力权重在 CPU 上的 dropout 掩码由「张量形状 + 内存布局 + RNG 偏移」共同决定，
        # 布局必须逐位一致，否则训练期输出不等价（2026-11-25 独立代码审查 N2 的根因）。
        h = L.norm1(x)                                   # (B, T, E)
        qkv = F.linear(h.transpose(0, 1),                # → (T, B, 3E)
                       L.self_attn.in_proj_weight, L.self_attn.in_proj_bias)
        q, k, v = qkv.chunk(3, dim=-1)                   # 各 (T, B, E)

        def _split(t):
            t = t.reshape(T, B * self.H, self.dh).transpose(0, 1)   # (B*H, T, dh)
            return t.view(B, self.H, T, self.dh)                   # (B, H, T, dh)

        q, k, v = _split(q), _split(k), _split(v)
        # 官方 multi_head_attention_forward(need_weights=False) → scaled_dot_product_attention(
        #   ..., dropout_p=self.dropout.p)；训练期对**注意力权重**施加一次 dropout
        a = F.scaled_dot_product_attention(q, k, v,
                                            dropout_p=(self.p if self.training else 0.0))
        a = a.permute(2, 0, 1, 3).reshape(T * B, E)      # 官方一致
        a = L.self_attn.out_proj(a)
        a = a.view(T, B, E).transpose(0, 1)               # 官方 MHA batch_first 返回 (B, T, E)
        a = F.dropout(a, p=self.p, training=self.training)          # 官方 dropout1（attn 输出）
        x = x + a
        h = L.norm2(x)
        y = L.linear1(h)
        y = L.activation(y)
        y = F.dropout(y, p=self.p, training=self.training)          # 官方 _ff_block 隐层 dropout
        y = L.linear2(y)
        y = F.dropout(y, p=self.p, training=self.training)          # 官方 dropout2（FF 输出）
        return x + y


class SeqEncoder(nn.Module):
    """summary-token Transformer 编码器（上下文 126 / 目标 21 共用）"""

    def __init__(self, d_model, n_layers, n_heads, d_ff, max_len, dropout=0.1):
        super().__init__()
        self.proj = nn.Linear(N_FEAT, d_model)
        self.summary = nn.Parameter(torch.zeros(1, 1, d_model))
        nn.init.normal_(self.summary, std=0.02)
        self.pos = nn.Parameter(torch.zeros(1, max_len + 1, d_model))
        nn.init.normal_(self.pos, std=0.02)
        layer = nn.TransformerEncoderLayer(d_model, n_heads, d_ff, dropout=dropout,
                                           activation="gelu", batch_first=True, norm_first=True)
        enc = nn.TransformerEncoder(layer, n_layers)
        # 只替换前向实现，构造方式与权重完全不变（初始化与官方实现逐位一致）
        enc.layers = nn.ModuleList([FastEncoderLayer(l) for l in enc.layers])
        self.enc = enc
        self.ln = nn.LayerNorm(d_model)

    def forward(self, x):
        b, t, _ = x.shape
        h = self.proj(x)
        s = self.summary.expand(b, -1, -1)
        h = torch.cat([s, h], dim=1)
        h = h + self.pos[:, :t + 1]
        h = self.enc(h)
        return self.ln(h[:, 0])


class PriorProjector(nn.Module):
    def __init__(self, d_model):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(PRIOR_DIM, d_model), nn.LayerNorm(d_model),
                                 nn.GELU(), nn.Linear(d_model, d_model))

    def forward(self, p):
        return self.net(p)


class RevisionPredictor(nn.Module):
    def __init__(self, d_model, dropout=0.1):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(2 * d_model, 256), nn.GELU(), nn.Dropout(dropout),
                                 nn.Linear(256, d_model))

    def forward(self, h, anchor):
        return self.net(torch.cat([h, anchor.detach()], dim=1))


class ReadoutMLPStd(nn.Module):
    """论文附录 I 的匹配 MLP：训练集特征标准化 + 无 LayerNorm"""

    def __init__(self, d_in, dropout=0.05):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(d_in, 256), nn.GELU(), nn.Dropout(dropout),
                                 nn.Linear(256, 1))

    def forward(self, x):
        return self.net(x).squeeze(-1)


class ReadoutMLP(nn.Module):
    def __init__(self, d_in, dropout=0.05):
        super().__init__()
        self.net = nn.Sequential(nn.LayerNorm(d_in), nn.Linear(d_in, 256), nn.GELU(),
                                 nn.Dropout(dropout), nn.Linear(256, 1))

    def forward(self, x):
        return self.net(x).squeeze(-1)


def ema_update(online, ema, decay):
    with torch.no_grad():
        for pe, po in zip(ema.parameters(), online.parameters()):
            pe.mul_(decay).add_(po, alpha=1 - decay)
        for be, bo in zip(ema.buffers(), online.buffers()):
            be.copy_(bo)


def make_mask_token(d_model):
    return nn.Parameter(torch.zeros(d_model))


# ---------------------------------------------------------------- 数据
class Panel:
    def __init__(self):
        self.feat = np.load(os.path.join(DATA, "feat.npy"), mmap_mode="r")
        self.tgt = np.load(os.path.join(DATA, "targets.npy"), mmap_mode="r")
        self.prior = json.load(open(os.path.join(DATA, "prior.json"), encoding="utf-8"))
        self.norm = json.load(open(os.path.join(DATA, "norm.json"), encoding="utf-8"))
        s = np.load(os.path.join(DATA, "samples.npz"))
        self.stock = s["stock_idx"]
        self.date = s["date_idx"]
        self.dts = s["dts"]
        self.m_train, self.m_valid, self.m_test = s["m_train"], s["m_valid"], s["m_test"]
        self.mean = np.asarray(self.norm["mean"], dtype=np.float32)
        self.std = np.asarray(self.norm["std"], dtype=np.float32)
        # 块：按样本日期归属
        blocks = self.prior["blocks"]
        names = sorted(blocks.keys())
        self.block_names = names
        self.block_of_date = {}
        for nm in names:
            b = blocks[nm]
            self.block_of_date[nm] = (b["block_start"], b["block_end"])
        self.blocks = blocks

    def block_id(self, dts):
        """样本日期 → 块下标。"""
        out = np.full(len(dts), -1, dtype=np.int16)
        for i, nm in enumerate(self.block_names):
            b = self.blocks[nm]
            lo, hi = b["block_start"], b["block_end"]
            out[(dts >= lo) & (dts <= hi)] = i
        return out

    def features(self, si, di):
        x = np.asarray(self.feat[si, di, :], dtype=np.float32)
        x = (x - self.mean) / self.std
        x[~np.isfinite(x)] = 0.0
        np.clip(x, -8, 8, out=x)
        return x

    def raw_features(self, si, di):
        return np.asarray(self.feat[si, di, :], dtype=np.float32)

    def context(self, si, di):
        """(n, 126, 40) 标准化上下文（整批向量化 gather，避免逐样本 Python 循环）。"""
        idx = di[:, None] - (CTX - 1 - np.arange(CTX)[None, :])
        out = np.asarray(self.feat[si[:, None], idx, :], dtype=np.float32)
        out = (out - self.mean) / self.std
        out[~np.isfinite(out)] = 0.0
        np.clip(out, -8, 8, out=out)
        return out

    def future(self, si, di):
        """(n, 21, 40) 标准化未来窗。"""
        idx = di[:, None] + (1 + np.arange(H)[None, :])
        out = np.asarray(self.feat[si[:, None], idx, :], dtype=np.float32)
        out = (out - self.mean) / self.std
        out[~np.isfinite(out)] = 0.0
        np.clip(out, -8, 8, out=out)
        return out

    def prior_vec(self, si, di, bid):
        """按块参数算 P(16)。"""
        P = np.empty((len(si), PRIOR_DIM), dtype=np.float32)
        for i, nm in enumerate(self.block_names):
            m = bid == i
            if not m.any():
                continue
            b = self.blocks[nm]
            x = self.raw_features(si[m], di[m])
            fmean = np.asarray(b["feat_mean"], dtype=np.float32)
            fstd = np.asarray(b["feat_std"], dtype=np.float32)
            xs = (x - fmean) / fstd
            xs[~np.isfinite(xs)] = 0.0
            np.clip(xs, -8, 8, out=xs)
            W = np.asarray(b["W"], dtype=np.float32)
            c = np.asarray(b["c"], dtype=np.float32)
            raw = c[None, :] + xs @ W
            mtr = np.asarray(b["target_mean"], dtype=np.float32)
            dtr = np.asarray(b["target_std"], dtype=np.float32)
            pri = np.clip((raw - mtr) / dtr, -8, 8)
            sig = np.asarray(b["sigma_res"], dtype=np.float32)
            P[m, :8] = pri
            P[m, 8:] = sig
        return P

    def labels(self, si, di):
        y = np.asarray(self.tgt[si, di, 0], dtype=np.float32)
        return y


# ---------------------------------------------------------------- 训练
def train_representation(panel, args, seed):
    torch.manual_seed(seed)
    np.random.seed(seed)
    rng = np.random.default_rng(seed)
    d_model, n_layers, n_heads, d_ff = args.d_model, args.layers, args.heads, args.ff
    dev = torch.device("cpu")
    torch.set_num_threads(args.threads)

    enc = SeqEncoder(d_model, n_layers, n_heads, d_ff, max(CTX, H)).to(dev)
    tenc = SeqEncoder(d_model, n_layers, n_heads, d_ff, max(CTX, H)).to(dev)
    proj = PriorProjector(d_model).to(dev)
    qpred = RevisionPredictor(d_model).to(dev)
    tenc_ema = SeqEncoder(d_model, n_layers, n_heads, d_ff, max(CTX, H)).to(dev)
    proj_ema = PriorProjector(d_model).to(dev)
    ema_update(tenc, tenc_ema, 0.0)
    ema_update(proj, proj_ema, 0.0)
    for p in list(tenc_ema.parameters()) + list(proj_ema.parameters()):
        p.requires_grad_(False)
    tenc_ema.eval()
    proj_ema.eval()

    opt_a = torch.optim.AdamW(proj.parameters(), lr=1e-4, weight_decay=0.01)
    opt_q = torch.optim.AdamW(list(enc.parameters()) + list(qpred.parameters()),
                               lr=1e-4, weight_decay=0.01)

    tr = np.nonzero(panel.m_train)[0]
    va = np.nonzero(panel.m_valid)[0]
    va_sub = va[::args.val_stride]
    tr_dates = panel.dts[tr]
    tr_block = panel.block_id(tr_dates)
    va_block = panel.block_id(panel.dts[va_sub])

    # 运行统计（修正坐标标准化）
    m_k = torch.zeros(d_model)
    v_k = torch.ones(d_model)
    n_upd = 0
    best = (float("inf"), None)
    ckpt_path = os.path.join(DATA, f"ckpt_seed{seed}_rep.pt")

    def encode_batch(idx):
        si, di = panel.stock[idx], panel.date[idx]
        ctx = torch.from_numpy(panel.context(si, di))
        fut = torch.from_numpy(panel.future(si, di))
        pr = torch.from_numpy(panel.prior_vec(si, di, panel.block_id(panel.dts[idx])))
        return ctx, fut, pr

    def losses_on(idx, train=True):
        ctx, fut, pr = encode_batch(idx)
        with torch.no_grad():
            z_tgt = tenc_ema(fut)
            z_anc_ema = proj_ema(pr)
        z_anc = proj(pr)
        l_anc = ((z_anc - z_tgt) ** 2).sum(dim=1).mean()
        d_trn = (z_tgt - z_anc_ema).detach()
        if train:
            bm = d_trn.mean(dim=0)
            bv = d_trn.var(dim=0, unbiased=False)
            # 用 in-place 更新外层 m_k / v_k（若写成 m_k = ... 会把 m_k 变成本地变量）
            if n_upd < 100:
                m_k.mul_(0.9).add_(bm, alpha=0.1)
                v_k.mul_(0.9).add_(bv, alpha=0.1)
            else:
                m_k.mul_(0.99).add_(bm, alpha=0.01)
                v_k.mul_(0.99).add_(bv, alpha=0.01)
        s_k = torch.clamp(torch.sqrt(v_k + 1e-4), min=1e-3)
        d_norm = torch.clamp((d_trn - m_k) / s_k, -8, 8)
        h = enc(ctx)
        q = qpred(h, z_anc_ema)
        l_rev = ((q - d_norm) ** 2).sum(dim=1).mean()
        return l_anc, l_rev, (m_k, s_k)

    t0 = time.time()
    upd = 0
    for epoch in range(args.epochs):
        order = rng.permutation(len(tr))
        for k in range(args.max_updates):
            sel = order[k * args.batch:(k + 1) * args.batch]
            if len(sel) == 0:
                break
            idx = tr[sel]
            l_anc, l_rev, (m_k, s_k) = losses_on(idx, train=True)
            opt_a.zero_grad(set_to_none=True)
            l_anc.backward()
            nn.utils.clip_grad_norm_(proj.parameters(), 1.0)
            opt_a.step()
            opt_q.zero_grad(set_to_none=True)
            l_rev.backward()
            nn.utils.clip_grad_norm_(list(enc.parameters()) + list(qpred.parameters()), 1.0)
            opt_q.step()
            ema_update(tenc, tenc_ema, 0.996)
            ema_update(proj, proj_ema, 0.99)
            n_upd += 1
            upd += 1
            if upd % args.log_every == 0:
                el = time.time() - t0
                log(f"  seed{seed} epoch{epoch} upd{upd} L_anc={l_anc.item():.4f} "
                    f"L_rev={l_rev.item():.4f} {el:.0f}s ({upd/el:.2f} upd/s)")
            if upd % args.save_every == 0:
                torch.save({"enc": enc.state_dict(), "tenc_ema": tenc_ema.state_dict(),
                            "proj_ema": proj_ema.state_dict(), "proj": proj.state_dict(),
                            "qpred": qpred.state_dict(), "m_k": m_k, "v_k": v_k,
                            "upd": upd}, ckpt_path + ".tmp")
                os.replace(ckpt_path + ".tmp", ckpt_path)

        # 验证损失 → 选 checkpoint
        va_losses = []
        with torch.no_grad():
            for k in range(0, len(va_sub), args.val_batch):
                chunk = va_sub[k:k + args.val_batch]
                la, lr_, _ = losses_on(chunk, train=False)
                va_losses.append(float(la) + float(lr_))
        vloss = float(np.mean(va_losses)) if va_losses else float("inf")
        log(f"  seed{seed} epoch{epoch} 验证损失 {vloss:.4f}（{elapsed_str(t0)}）")
        if vloss < best[0]:
            best = (vloss, {"enc": enc.state_dict(), "tenc_ema": tenc_ema.state_dict(),
                            "proj_ema": proj_ema.state_dict(), "proj": proj.state_dict(),
                            "qpred": qpred.state_dict(), "m_k": m_k, "v_k": v_k, "upd": upd})
            torch.save(best[1], ckpt_path)
    if best[1] is not None:
        torch.save(best[1], ckpt_path)
    log(f"  seed{seed} 表征训练完成：{upd} updates，{time.time()-t0:.0f}s，验证损失 {best[0]:.4f}")
    return ckpt_path


def elapsed_str(t0):
    return f"{time.time()-t0:.0f}s"


def self_test_fast_encoder(d_model=64, n_layers=2, n_heads=4, d_ff=128):
    """自检：FastEncoderLayer 与官方 nn.TransformerEncoderLayer 前向等价。

    两条前向路径共用**同一批 layer 对象**（权重逐位相同），只比较前向实现差异。
    **eval 模式**：验证注意力/FF 计算等价。
    **train 模式**：同一随机种子下重复前向，验证 dropout 的**次数、位置与随机流**与官方一致
    （2026-11-25 独立代码审查 N2 的回归测试）。
    证据随实现一起交付，供独立复算与代码审查核对。
    """
    torch.manual_seed(0)
    enc = nn.TransformerEncoder(
        nn.TransformerEncoderLayer(d_model, n_heads, d_ff, dropout=0.1,
                                   activation="gelu", batch_first=True, norm_first=True),
        n_layers).eval()
    layers = list(enc.layers)
    fast_layers = [FastEncoderLayer(l) for l in layers]
    for fl in fast_layers:
        fl.eval()
    x = torch.randn(32, max(CTX, H) + 1, d_model)
    with torch.no_grad():
        h = x
        for l in layers:
            h = l(h, src_mask=None, src_key_padding_mask=None, is_causal=False)
        a = h[:, 0]
        h2 = x
        for l in fast_layers:
            h2 = l(h2)
        b = h2[:, 0]
    diff = float((a - b).abs().max())
    ok = diff < 1e-5
    print(f"[SELF-TEST] FastEncoderLayer vs nn.TransformerEncoder (eval): max|diff| = {diff:.3e} "
          f"→ {'PASS' if ok else 'FAIL'}")

    # ---- train 模式：dropout 全开，同一随机种子下比较（回归测试 N2）----
    enc.train()
    for fl in fast_layers:
        fl.train()
    with torch.no_grad():
        torch.manual_seed(1234)
        h = x
        for l in layers:
            h = l(h, src_mask=None, src_key_padding_mask=None, is_causal=False)
        a = h[:, 0]
        torch.manual_seed(1234)
        h2 = x
        for l in fast_layers:
            h2 = l(h2)
        b = h2[:, 0]
    train_diff = float((a - b).abs().max())
    train_ok = train_diff < 1e-4
    print(f"[SELF-TEST] FastEncoderLayer vs nn.TransformerEncoder (train, seed=1234): "
          f"max|diff| = {train_diff:.3e} → {'PASS' if train_ok else 'FAIL'}")

    enc.eval()
    for fl in fast_layers:
        fl.eval()
    return {"max_abs_diff": diff, "threshold": 1e-5, "pass": ok,
            "train_max_abs_diff": train_diff, "train_threshold": 1e-4, "train_pass": train_ok,
            "train_seed": 1234,
            "note": ("eval：注意力/FF 计算等价；train：dropout 次数、位置与随机流与官方 "
                     "nn.TransformerEncoderLayer 一致（2026-11-25 独立代码审查 N2 修复后）"),
            "d_model": d_model, "layers": n_layers, "heads": n_heads, "ff": d_ff}


def build_reps(panel, args, seed, ckpt_path):
    """冻结表征 → 计算 u 特征（train/valid/test）与预测。"""
    d_model = args.d_model
    dev = torch.device("cpu")
    torch.set_num_threads(args.threads)
    st = torch.load(ckpt_path, map_location=dev, weights_only=False)
    enc = SeqEncoder(d_model, args.layers, args.heads, args.ff, max(CTX, H)).to(dev)
    proj_ema = PriorProjector(d_model).to(dev)
    qpred = RevisionPredictor(d_model).to(dev)
    enc.load_state_dict(st["enc"])
    proj_ema.load_state_dict(st["proj_ema"])
    qpred.load_state_dict(st["qpred"])
    enc.eval()
    proj_ema.eval()
    qpred.eval()
    m_k = st["m_k"]
    v_k = st["v_k"]
    s_k = torch.clamp(torch.sqrt(v_k + 1e-4), min=1e-3)

    out = {}
    for split, mask in (("train", panel.m_train), ("valid", panel.m_valid), ("test", panel.m_test)):
        idx_all = np.nonzero(mask)[0]
        if split == "valid":
            idx_all = idx_all[::args.val_stride]
        if args.rep_date_cap:
            # 仅供冒烟测试：每个切分只保留前 N 个预测日
            keep_dts = np.unique(panel.dts[idx_all])[:args.rep_date_cap]
            idx_all = idx_all[np.isin(panel.dts[idx_all], keep_dts)]
        if split == "train":
            # 读出训练用「每日期最多 stock_cap 只」的确定性抽样（向量化）
            dts = panel.dts[idx_all]
            order = np.argsort(dts, kind="stable")
            sd = dts[order]
            uniq, starts = np.unique(sd, return_index=True)
            bounds = list(starts) + [len(order)]
            keep = []
            for i in range(len(uniq)):
                seg = order[bounds[i]:bounds[i + 1]]
                keep.append(seg[:args.stock_cap])
            pos = np.concatenate(keep) if keep else np.zeros(0, dtype=np.int64)
            idx_all = idx_all[pos].astype(np.int64)
        si, di = panel.stock[idx_all], panel.date[idx_all]
        bid = panel.block_id(panel.dts[idx_all])
        u_path = os.path.join(DATA, f"u_seed{seed}_{split}.npy")
        u = np.lib.format.open_memmap(u_path, mode="w+", dtype=np.float32,
                                      shape=(len(idx_all), 40 + PRIOR_DIM + 3 * d_model))
        B = args.rep_batch
        with torch.no_grad():
            for k in range(0, len(idx_all), B):
                sl = slice(k, k + B)
                xc = panel.context(si[sl], di[sl])          # 标准化上下文，只取一次
                ctx = torch.from_numpy(xc)
                pr = torch.from_numpy(panel.prior_vec(si[sl], di[sl], bid[sl]))
                h = enc(ctx)
                z_anc = proj_ema(pr)
                q = qpred(h, z_anc)
                delta = m_k + s_k * q
                smean = xc.mean(axis=1)                      # 历史摘要 s
                u[sl] = np.concatenate([smean, pr.numpy(), z_anc.numpy(), delta.numpy(),
                                        h.numpy()], axis=1)
        u.flush()
        y = panel.labels(si, di)
        out[split] = {"idx": idx_all, "u_path": u_path, "u_shape": (len(idx_all), u.shape[1]),
                      "y": y, "stock": si, "date": di, "dts": panel.dts[idx_all]}
        log(f"  seed{seed} {split}: {len(idx_all):,} 样本，u 维度 {u.shape[1]}")
    return out


def train_readout(panel, args, seed, reps, arm):
    """arm ∈ {s1_only, s1_s2, full, s3_only}"""
    dev = torch.device("cpu")
    torch.set_num_threads(args.threads)
    torch.manual_seed(seed + 7)
    d_model = args.d_model
    d_p, d_z = PRIOR_DIM, d_model
    # u 列布局：[s(40) | P(16) | z_anc(d_z) | Δ̂(d_z) | h(d_z)]
    def cols(*blocks):
        idx = []
        for b in blocks:
            if b == "s":
                idx += list(range(0, 40))
            elif b == "P":
                idx += list(range(40, 40 + d_p))
            elif b == "anc":
                idx += list(range(40 + d_p, 40 + d_p + d_z))
            elif b == "rev":
                idx += list(range(40 + d_p + d_z, 40 + d_p + 2 * d_z))
            elif b == "h":
                idx += list(range(40 + d_p + 2 * d_z, 40 + d_p + 3 * d_z))
            else:
                raise ValueError(b)
        return np.asarray(idx, dtype=np.int64)

    SPEC = {
        "s1_only":  (cols("P"), "linear"),
        "s3_only":  (cols("s"), "linear"),
        "s1_s2":    (cols("P", "anc", "rev"), "linear"),
        "full":     (cols("s", "P", "anc", "rev"), "mlp_ln"),
        "r0":       (cols("s", "P"), "mlp_std"),
        "r1":       (cols("s", "P", "anc"), "mlp_std"),
        "r2":       (cols("s", "P", "anc", "rev"), "mlp_std"),
        "r3":       (cols("s", "P", "anc", "h"), "mlp_std"),
    }
    if arm not in SPEC:
        raise ValueError(arm)
    sel_cols, kind = SPEC[arm]
    d_in = len(sel_cols)
    if kind == "linear":
        model = nn.Linear(d_in, 1).to(dev)
    elif kind == "mlp_ln":
        model = ReadoutMLP(d_in).to(dev)
    else:                                   # mlp_std：训练集标准化、无 LayerNorm（论文附录 I）
        model = ReadoutMLPStd(d_in).to(dev)

    tr, va = reps["train"], reps["valid"]
    ytr = tr["y"]
    mu, sd = float(np.mean(ytr)), float(np.std(ytr))
    sd = sd if sd > 1e-9 else 1.0

    def feat(split):
        """按需从 u 的 memmap 取列（不把整个 u 常驻内存）。"""
        return np.load(split["u_path"], mmap_mode="r")

    u_tr = feat(tr)
    u_va = feat(va)
    mu_u = None
    sd_u = None
    if kind == "mlp_std":
        # 训练集统计量标准化（附录 I）：分块累计，避免整个 u 常驻内存
        n = u_tr.shape[0]
        s1 = torch.zeros(len(sel_cols), dtype=torch.float64)
        s2 = torch.zeros(len(sel_cols), dtype=torch.float64)
        for k in range(0, n, 100000):
            blk = torch.from_numpy(np.ascontiguousarray(u_tr[k:k + 100000][:, sel_cols])).double()
            s1 += blk.sum(dim=0)
            s2 += (blk ** 2).sum(dim=0)
        mu_u = (s1 / n).float().view(1, -1)
        sd_u = torch.sqrt((s2 / n - (s1 / n) ** 2).clamp_min(0)).float().view(1, -1)
        # 近常数列（训练集标准差 ≈ 0）不放大：按 sklearn StandardScaler 惯例置 1
        degenerate = sd_u < 1e-8
        if bool(degenerate.any()):
            log(f"  近常数读出坐标 {int(degenerate.sum())} 个（训练集 std < 1e-8），按 StandardScaler 惯例不缩放")
        sd_u = torch.where(degenerate, torch.ones_like(sd_u), sd_u).clamp_min(1e-6)
        standardize = True
    else:
        standardize = False

    def take(u, rows):
        """rows 可以是切片或索引数组；返回 (batch, d_in) 的 float32 tensor。"""
        xb = torch.from_numpy(np.ascontiguousarray(u[rows][:, sel_cols]))
        if standardize:
            xb = (xb - mu_u) / sd_u
            # 训练集标准化后裁剪 [−8,8]：与本单 S1 标准化同口径（预注册 §10.1），
            # 用于抑制重尾列的离群 z 值；只作用于 mlp_std 的 R0–R3 对照臂。
            xb = xb.clamp(-8.0, 8.0)
        return xb

    ytr_t = torch.from_numpy(((tr["y"] - mu) / sd).astype(np.float32))
    yva_t = torch.from_numpy(((va["y"] - mu) / sd).astype(np.float32))

    # 按日期分组（向量化）
    dtr = tr["dts"]
    order = np.argsort(dtr, kind="stable")
    sd_ = dtr[order]
    uniq, starts = np.unique(sd_, return_index=True)
    bounds = list(starts) + [len(order)]
    gkeys = [int(u_) for u_ in uniq]
    gidx = {gkeys[i]: order[bounds[i]:bounds[i + 1]] for i in range(len(gkeys))}
    rng = np.random.default_rng(seed + 11)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-4, weight_decay=0.05)
    lossf = nn.SmoothL1Loss(beta=1.0)
    best = (float("inf"), None)
    for epoch in range(args.readout_epochs):
        order_d = rng.permutation(len(gkeys))
        for k in range(args.readout_max_updates):
            if k >= len(order_d):
                break
            gi = gidx[gkeys[order_d[k]]]
            xb = take(u_tr, gi)
            yb = ytr_t[torch.from_numpy(gi)]
            opt.zero_grad(set_to_none=True)
            pred = model(xb)
            loss = lossf(pred, yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        model.eval()
        with torch.no_grad():
            tot, cnt = 0.0, 0
            for k in range(0, u_va.shape[0], 20000):
                chunk = slice(k, k + 20000)
                xb = take(u_va, chunk)
                nb = xb.shape[0]
                tot += float(lossf(model(xb), yva_t[chunk])) * nb
                cnt += nb
            vloss = tot / max(cnt, 1)
        log(f"  seed{seed} {arm} epoch{epoch} 验证 SmoothL1 {vloss:.6f}")
        if vloss < best[0]:
            best = (vloss, {k: v.clone() for k, v in model.state_dict().items()})
    model.load_state_dict(best[1])
    model.eval()
    return {"arm": arm, "model": model, "val_loss": best[0], "mu": mu, "sd": sd,
            "sel_cols": sel_cols, "kind": kind, "mu_u": mu_u, "sd_u": sd_u,
            "d_in": d_in}


def predict_all_arms(reps, arm_specs, seed, chunk=20000):
    """对所有臂**各切分只读一遍 u**，一次写全部预测。

    逐臂逐切分读 u 会把 u 文件读 8×（本机 u 总量约 4.9GB，memmap 读取是主要瓶颈），
    这里把 8 个臂合并成每切分 1 次遍历，读取量降为 1/8。
    """
    for split in ("train", "valid", "test"):
        u = np.load(reps[split]["u_path"], mmap_mode="r")
        n = u.shape[0]
        pms = {}
        for arm, spec in arm_specs.items():
            p_path = os.path.join(DATA, f"pred_seed{seed}_{arm}_{split}.npy")
            pms[arm] = np.lib.format.open_memmap(p_path, mode="w+", dtype=np.float32, shape=(n,))
        with torch.no_grad():
            for k in range(0, n, chunk):
                sl = slice(k, min(k + chunk, n))
                blk = np.asarray(u[sl])                       # 一次读入该批全部 248 列
                for arm, spec in arm_specs.items():
                    xb = torch.from_numpy(np.ascontiguousarray(blk[:, spec["sel_cols"]]))
                    if spec["kind"] == "mlp_std":
                        xb = (xb - spec["mu_u"]) / spec["sd_u"]
                        # 与训练/验证侧 take() 同口径：标准化后裁剪 [−8,8]（预注册 v1.1 §3）
                        xb = xb.clamp(-8.0, 8.0)
                    out = spec["model"](xb).numpy().reshape(-1)
                    pms[arm][sl] = (spec["mu"] + spec["sd"] * out).astype(np.float32)
        for arm in arm_specs:
            pms[arm].flush()
        log(f"  seed{seed} {split}: {n:,} 样本 × {len(arm_specs)} 臂预测完成")
    return {arm: {s: os.path.join(DATA, f"pred_seed{seed}_{arm}_{s}.npy")
                  for s in ("train", "valid", "test")} for arm in arm_specs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 2024])
    ap.add_argument("--d-model", type=int, default=64)
    ap.add_argument("--layers", type=int, default=2)
    ap.add_argument("--heads", type=int, default=4)
    ap.add_argument("--ff", type=int, default=128)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--max-updates", type=int, default=1000)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--readout-epochs", type=int, default=6)
    ap.add_argument("--readout-max-updates", type=int, default=1000)
    ap.add_argument("--stock-cap", type=int, default=1000, help="读出训练时每个日期最多取多少只股票")
    ap.add_argument("--val-stride", type=int, default=4, help="验证子集抽样步长（算力降级）")
    ap.add_argument("--val-batch", type=int, default=512)
    ap.add_argument("--rep-batch", type=int, default=512)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--log-every", type=int, default=50)
    ap.add_argument("--save-every", type=int, default=100)
    ap.add_argument("--rep-date-cap", type=int, default=0,
                    help=">0 时每个切分只保留前 N 个预测日（仅供冒烟测试，正式跑必须为 0）")
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--bench-samples", type=int, default=64)
    args = ap.parse_args()

    if args.bench:
        torch.set_num_threads(args.threads)
        dev = torch.device("cpu")
        enc = SeqEncoder(args.d_model, args.layers, args.heads, args.ff, max(CTX, H)).to(dev)
        tenc = SeqEncoder(args.d_model, args.layers, args.heads, args.ff, max(CTX, H)).to(dev)
        proj = PriorProjector(args.d_model).to(dev)
        qp = RevisionPredictor(args.d_model).to(dev)
        opt = torch.optim.AdamW(list(enc.parameters()) + list(qp.parameters()), lr=1e-4)
        n = args.bench_samples
        ctx = torch.randn(n, CTX, N_FEAT)
        fut = torch.randn(n, H, N_FEAT)
        pr = torch.randn(n, PRIOR_DIM)
        for _ in range(2):
            z_t = tenc(fut)
            z_a = proj(pr)
            h = enc(ctx)
            q = qp(h, z_a)
            loss = ((z_a - z_t) ** 2).sum(1).mean() + ((q - torch.randn_like(q)) ** 2).sum(1).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
        t = time.time()
        reps = 5
        for _ in range(reps):
            z_t = tenc(fut)
            z_a = proj(pr)
            h = enc(ctx)
            q = qp(h, z_a)
            loss = ((z_a - z_t) ** 2).sum(1).mean() + ((q - torch.randn_like(q)) ** 2).sum(1).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
        dt = (time.time() - t) / reps
        print(json.dumps({"d_model": args.d_model, "layers": args.layers, "heads": args.heads,
                          "ff": args.ff, "batch": n, "sec_per_batch": round(dt, 3),
                          "samples_per_sec": round(n / dt, 1),
                          "est_sec_per_seed_1024M": round(dt * (1024 * 1000) / n / 1e6, 1),
                          "threads": args.threads}, ensure_ascii=False))
        return 0

    panel = Panel()
    log(f"样本：train {panel.m_train.sum():,} / valid {panel.m_valid.sum():,} / test {panel.m_test.sum():,}")
    st = self_test_fast_encoder(args.d_model, args.layers, args.heads, args.ff)
    with open(os.path.join(OUT, "fast_encoder_selftest.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    if not st["pass"]:
        log("[SELF-TEST] 失败，终止")
        return 2
    results = {}
    for seed in args.seeds:
        log(f"===== 种子 {seed} =====")
        ck = train_representation(panel, args, seed)
        reps = build_reps(panel, args, seed, ck)
        arm_specs = {}
        for arm in ("s1_only", "s3_only", "s1_s2", "full", "r0", "r1", "r2", "r3"):
            spec = train_readout(panel, args, seed, reps, arm)
            torch.save({"state": spec["model"].state_dict(), "mu": spec["mu"], "sd": spec["sd"],
                        "arm": arm, "seed": seed}, os.path.join(DATA, f"ckpt_seed{seed}_{arm}.pt"))
            results[f"{arm}|{seed}"] = {"val_loss": spec["val_loss"], "mu": spec["mu"], "sd": spec["sd"]}
            arm_specs[arm] = spec
            log(f"  seed{seed} {arm}: 验证 {spec['val_loss']:.6f}")
        # 所有臂训练完成后，统一按切分各读一遍 u，一次写出全部预测
        pred_paths = predict_all_arms(reps, arm_specs, seed)
        for arm in arm_specs:
            np.savez_compressed(os.path.join(DATA, f"preds_seed{seed}_{arm}.npz"),
                                **{f"pred_{s}": np.asarray(np.load(pred_paths[arm][s], mmap_mode="r"))
                                   for s in ("train", "valid", "test")},
                                **{f"idx_{s}": reps[s]["idx"] for s in ("train", "valid", "test")},
                                **{f"stock_{s}": reps[s]["stock"] for s in ("train", "valid", "test")},
                                **{f"date_{s}": reps[s]["date"] for s in ("train", "valid", "test")},
                                **{f"y_{s}": reps[s]["y"] for s in ("train", "valid", "test")})
        with open(os.path.join(OUT, f"train_meta_seed{seed}.json"), "w", encoding="utf-8") as f:
            json.dump({"seed": seed, "d_model": args.d_model, "layers": args.layers,
                       "heads": args.heads, "ff": args.ff, "epochs": args.epochs,
                       "max_updates": args.max_updates, "batch": args.batch,
                       "readout_epochs": args.readout_epochs, "stock_cap": args.stock_cap,
                       "val_stride": args.val_stride,
                       "fast_encoder_selftest": st}, f, ensure_ascii=False, indent=2)
    log("全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
