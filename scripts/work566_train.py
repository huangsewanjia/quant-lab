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

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "WORK-566")
OUT = os.path.join(REPO, "experiments", "WORK-566")

CTX, H = 126, 21
N_FEAT = 40
PRIOR_DIM = 16


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# ---------------------------------------------------------------- 模型
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
        self.enc = nn.TransformerEncoder(layer, n_layers)
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
        """(n, 126, 40) 标准化上下文。"""
        idx = di[:, None] - (CTX - 1 - np.arange(CTX)[None, :])
        out = np.empty((len(si), CTX, N_FEAT), dtype=np.float32)
        for k in range(len(si)):
            out[k] = self.feat[si[k], idx[k], :]
        out = (out - self.mean) / self.std
        out[~np.isfinite(out)] = 0.0
        np.clip(out, -8, 8, out=out)
        return out

    def future(self, si, di):
        """(n, 21, 40) 标准化未来窗。"""
        idx = di[:, None] + (1 + np.arange(H)[None, :])
        out = np.empty((len(si), H, N_FEAT), dtype=np.float32)
        for k in range(len(si)):
            out[k] = self.feat[si[k], idx[k], :]
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
            if n_upd < 100:
                m_k = 0.9 * m_k + 0.1 * bm
                v_k = 0.9 * v_k + 0.1 * bv
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
        if split == "train":
            # 读出训练用「每日期最多 stock_cap 只」的确定性抽样
            keep = []
            dts = panel.dts[idx_all]
            order = np.argsort(dts, kind="stable")
            cur, cnt = None, 0
            for j in order:
                d = dts[j]
                if d != cur:
                    cur, cnt = d, 0
                if cnt < args.stock_cap:
                    keep.append(idx_all[j])
                    cnt += 1
            idx_all = np.asarray(keep, dtype=np.int64)
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
        u = np.load(split["u_path"], mmap_mode="r")
        return torch.from_numpy(np.ascontiguousarray(u[:, sel_cols]))

    Xtr_raw, Xva_raw = feat(tr), feat(va)
    if kind == "mlp_std":
        # 训练集统计量标准化（附录 I）：分块累计，避免整个 u 常驻内存
        u_tr = np.load(tr["u_path"], mmap_mode="r")
        n = u_tr.shape[0]
        s1 = torch.zeros(len(sel_cols), dtype=torch.float64)
        s2 = torch.zeros(len(sel_cols), dtype=torch.float64)
        for k in range(0, n, 100000):
            blk = torch.from_numpy(np.ascontiguousarray(u_tr[k:k + 100000][:, sel_cols])).double()
            s1 += blk.sum(dim=0)
            s2 += (blk ** 2).sum(dim=0)
        mu_u = (s1 / n).float().view(1, -1)
        sd_u = torch.sqrt((s2 / n - (s1 / n) ** 2).clamp_min(0)).float().view(1, -1).clamp_min(1e-6)
        Xtr, Xva = Xtr_raw, (Xva_raw - mu_u) / sd_u
    else:
        Xtr, Xva = Xtr_raw, Xva_raw
    ytr_t = torch.from_numpy(((tr["y"] - mu) / sd).astype(np.float32))
    yva_t = torch.from_numpy(((va["y"] - mu) / sd).astype(np.float32))

    # 按日期分组
    dtr = tr["dts"]
    groups = {}
    for i, d in enumerate(dtr):
        groups.setdefault(int(d), []).append(i)
    gkeys = sorted(groups)
    rng = np.random.default_rng(seed + 11)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-4, weight_decay=0.05)
    lossf = nn.SmoothL1Loss(beta=1.0)
    best = (float("inf"), None)
    for epoch in range(args.readout_epochs):
        order = rng.permutation(len(gkeys))
        for k in range(args.readout_max_updates):
            if k >= len(order):
                break
            gi = np.asarray(groups[gkeys[order[k]]], dtype=np.int64)
            xb = Xtr[gi] if kind != "mlp_std" else (Xtr_raw[gi] - mu_u) / sd_u
            yb = ytr_t[gi]
            opt.zero_grad(set_to_none=True)
            pred = model(xb)
            loss = lossf(pred, yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        model.eval()
        with torch.no_grad():
            tot, cnt = 0.0, 0
            for k in range(0, len(Xva), 20000):
                chunk = slice(k, k + 20000)
                xb = Xva[chunk]
                if kind == "mlp_std":
                    xb = (xb - mu_u) / sd_u
                nb = xb.shape[0]
                tot += float(lossf(model(xb), yva_t[chunk])) * nb
                cnt += nb
            vloss = tot / max(cnt, 1)
        log(f"  seed{seed} {arm} epoch{epoch} 验证 SmoothL1 {vloss:.6f}")
        if vloss < best[0]:
            best = (vloss, {k: v.clone() for k, v in model.state_dict().items()})
    model.load_state_dict(best[1])
    model.eval()
    # 预测
    preds = {}
    for split in ("train", "valid", "test"):
        u = np.load(reps[split]["u_path"], mmap_mode="r")
        p_path = os.path.join(DATA, f"pred_seed{seed}_{arm}_{split}.npy")
        pm = np.lib.format.open_memmap(p_path, mode="w+", dtype=np.float32, shape=(u.shape[0],))
        with torch.no_grad():
            for k in range(0, u.shape[0], 20000):
                xb = torch.from_numpy(np.ascontiguousarray(u[k:k + 20000][:, sel_cols]))
                if kind == "mlp_std":
                    xb = (xb - mu_u) / sd_u
                pm[k:k + 20000] = (mu + sd * model(xb).numpy()).astype(np.float32)
        pm.flush()
        preds[split] = p_path
    return preds, best[0], model, (mu, sd)


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
    results = {}
    for seed in args.seeds:
        log(f"===== 种子 {seed} =====")
        ck = train_representation(panel, args, seed)
        reps = build_reps(panel, args, seed, ck)
        for arm in ("s1_only", "s3_only", "s1_s2", "full", "r0", "r1", "r2", "r3"):
            preds, vloss, model, (mu, sd) = train_readout(panel, args, seed, reps, arm)
            torch.save({"state": model.state_dict(), "mu": mu, "sd": sd,
                        "arm": arm, "seed": seed}, os.path.join(DATA, f"ckpt_seed{seed}_{arm}.pt"))
            results[f"{arm}|{seed}"] = {"val_loss": vloss, "mu": mu, "sd": sd}
            np.savez_compressed(os.path.join(DATA, f"preds_seed{seed}_{arm}.npz"),
                                **{f"pred_{s}": np.asarray(np.load(preds[s], mmap_mode="r"))
                                   for s in ("train", "valid", "test")},
                                **{f"idx_{s}": reps[s]["idx"] for s in ("train", "valid", "test")},
                                **{f"stock_{s}": reps[s]["stock"] for s in ("train", "valid", "test")},
                                **{f"date_{s}": reps[s]["date"] for s in ("train", "valid", "test")},
                                **{f"y_{s}": reps[s]["y"] for s in ("train", "valid", "test")})
            log(f"  seed{seed} {arm}: 验证 {vloss:.6f}，已保存预测")
        with open(os.path.join(OUT, f"train_meta_seed{seed}.json"), "w", encoding="utf-8") as f:
            json.dump({"seed": seed, "d_model": args.d_model, "layers": args.layers,
                       "heads": args.heads, "ff": args.ff, "epochs": args.epochs,
                       "max_updates": args.max_updates, "batch": args.batch,
                       "readout_epochs": args.readout_epochs, "stock_cap": args.stock_cap,
                       "val_stride": args.val_stride}, f, ensure_ascii=False, indent=2)
    log("全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
