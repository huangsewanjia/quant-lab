#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · DEE 引擎（量化研究员阶段）

严格照抄 experiments/WORK-555/preregistration.md v1.0 的 §4 / §6.1：
  - 三重随机化：bootstrap 池 (beta=0.8789) / 特征子集 (gamma=0.5, 全覆盖约束) / SWRR 类型轮转
  - 同步训练循环（论文 Algorithm 1）：每个专家用自己的 bootstrap 池抽 mini-batch 做一次梯度更新
  - 早停监控**集成级**验证损失（patience 50 步, 每 20 步评估一次）
  - IC 过滤：验证段逐专家日频截面 RankIC 均值, 保留 IC >= mean(IC) - kappa*sigma (kappa=1.5)
  - 聚合：激活专家的**原始预测等权平均**（不做事后标准化）
  - 三档对照臂：A=DEE(六型, N=20) / B=MLP-only(N=20, 同随机化同聚合) / C=XGBoost

输出：
  experiments/WORK-555/preds_<arm>_s<seed>.npz   测试段逐样本预测 + 专家级统计
  experiments/WORK-555/dee_run_log.json            全部随机源与运行诊断

运行：
  /tmp/w555venv/bin/python scripts/work555_dee.py --arm dee --seed 42
"""
import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
OUT = os.path.join(REPO, "experiments", EXP)

# ---------------------------------------------------------------- 预注册冻结常量
BETA = 0.8789              # §4.1 bootstrap 池比例
GAMMA = 0.5                # §4.1 特征子集比例
KAPPA = 1.5                # §4.1 IC 过滤容差
ALPHA = 1e-3               # §4.1 惩罚线性变体强度
LR = 1e-3                  # §4.1 Adam 学习率
WEIGHT_DECAY = 1e-4        # §4.1 Adam weight decay
BATCH = 8192               # §4.1 batch size
S_MAX = 8000               # 论文 §6.1 "for up to 8000 steps"
EVAL_EVERY = 20            # §4.1 每 20 步评估一次
PATIENCE = 50              # §4.1 patience 50（次评估）
VAL_SUBSAMPLE = 20000      # §4.3 固定验证子样本行数
MLP_HIDDEN = 32            # §4.1 MLP 隐层宽度
TREE_DEPTH = 12            # §4.1 soft tree 深度
TREE_MAX_LEAVES = 256      # 附录 D6：目标 <= 256 叶
N_EXPERTS = 20             # 附录 D1：N=50 -> 20
TYPE_RATIO_DEE = [4, 1, 2, 1, 6, 6]        # linear/ridge/lasso/elasticnet/mlp/tree
TYPE_NAMES = ["linear", "ridge", "lasso", "elasticnet", "mlp", "tree"]

ADAM_B1, ADAM_B2, ADAM_EPS = 0.9, 0.999, 1e-8
BN_MOMENTUM = 0.1
TREE_GATE_A0, TREE_GATE_B0 = 1.0, 0.0

def _mse_metric(y_true, y_pred):
    """预注册 §4.6 冻结的 eval_metric = mse。

    xgboost 3.x 移除了 "mse" 指标别名（只剩 rmse），但 rmse = sqrt(mse) 是单调变换，
    早停的「无改善」判定方向不变；这里直接用自定义指标把冻结口径写成字面的 MSE，
    不依赖别名是否还在。
    """
    yt = np.asarray(y_true, dtype=np.float64)
    yp = np.asarray(y_pred, dtype=np.float64)
    return float(np.mean((yt - yp) ** 2))


XGB_PARAMS = dict(
    n_estimators=2000, learning_rate=0.05, max_depth=6, subsample=0.8,
    colsample_bylevel=0.8, colsample_bytree=0.8, reg_lambda=1,
    objective="reg:squarederror", eval_metric=_mse_metric, early_stopping_rounds=50,
    tree_method="hist",
)


# ================================================================ 通用小工具
def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z, dtype=np.float64))


class Adam:
    """逐参数 Adam（论文 §6.1：Adam, lr 1e-3, weight decay 1e-4）。"""

    def __init__(self, params, lr=LR, no_decay=()):
        self.p = params
        self.m = {k: np.zeros_like(v, dtype=np.float32) for k, v in params.items()}
        self.v = {k: np.zeros_like(v, dtype=np.float32) for k, v in params.items()}
        self.lr, self.t = lr, 0
        self.no_decay = set(no_decay)

    def step(self, grads, weight_decay=0.0):
        self.t += 1
        bc1 = 1.0 - ADAM_B1 ** self.t
        bc2 = 1.0 - ADAM_B2 ** self.t
        for k, g in grads.items():
            p = self.p[k]
            if weight_decay and k not in self.no_decay:
                g = g + weight_decay * p
            m, v = self.m[k], self.v[k]
            m *= ADAM_B1
            m += (1.0 - ADAM_B1) * g
            v *= ADAM_B2
            v += (1.0 - ADAM_B2) * (g * g)
            p -= (self.lr / bc1) * (m / (np.sqrt(v / bc2) + ADAM_EPS)).astype(np.float32)

    def snapshot(self):
        return {k: v.copy() for k, v in self.p.items()}

    def restore(self, snap):
        for k, v in snap.items():
            self.p[k][...] = v


# ================================================================ 专家模型
class LinearExpert:
    """仿射族：linear / ridge / lasso / elasticnet（§4.5）。偏置不参与惩罚。"""

    def __init__(self, kind, n_feat, rng):
        self.kind, self.n_feat = kind, n_feat
        w = rng.normal(0.0, 0.01, n_feat).astype(np.float32)
        self.params = {"w": w, "b": np.array(0.0, dtype=np.float32)}
        self.opt = Adam(self.params, no_decay=("b",))
        self.no_decay_opt = kind in ("ridge", "lasso", "elasticnet")

    def forward(self, X, training=True):
        return X @ self.params["w"] + self.params["b"]

    def backward(self, X, pred, dy):
        g = {"w": (X.T @ dy).astype(np.float32), "b": np.array(dy.sum(), dtype=np.float32)}
        w = self.params["w"]
        if self.kind == "ridge":
            g["w"] += (2.0 * ALPHA * w).astype(np.float32)
        elif self.kind == "lasso":
            g["w"] += (ALPHA * np.sign(w)).astype(np.float32)
        elif self.kind == "elasticnet":
            g["w"] += (ALPHA * (0.5 * np.sign(w) + w)).astype(np.float32)
        return g

    def opt_weight_decay(self):
        return 0.0 if self.no_decay_opt else WEIGHT_DECAY

    def predict(self, X):
        return self.forward(X)


class MlpExpert:
    """1 隐层 MLP，宽度 32，带 batch normalization，ReLU，线性输出，无 dropout（§4.5）。"""

    def __init__(self, n_feat, rng):
        self.n_feat = n_feat
        s1 = np.sqrt(2.0 / n_feat)
        s2 = np.sqrt(2.0 / MLP_HIDDEN)
        self.params = {
            "w1": rng.normal(0, s1, (n_feat, MLP_HIDDEN)).astype(np.float32),
            "b1": np.zeros(MLP_HIDDEN, np.float32),
            "gamma1": np.ones(MLP_HIDDEN, np.float32),
            "beta1": np.zeros(MLP_HIDDEN, np.float32),
            "rmean": np.zeros(MLP_HIDDEN, np.float32),
            "rvar": np.ones(MLP_HIDDEN, np.float32),
            "w2": rng.normal(0, s2, (MLP_HIDDEN, 1)).astype(np.float32),
            "b2": np.array(0.0, dtype=np.float32),
        }
        self.opt = Adam(self.params, no_decay=("b1", "b2", "beta1", "gamma1", "rmean", "rvar"))
        self._cache = None

    def forward(self, X, training=True):
        w1 = self.params["w1"]
        z1 = X @ w1 + self.params["b1"]
        if training:
            mu = z1.mean(axis=0)
            var = z1.var(axis=0)
            self.params["rmean"] *= (1 - BN_MOMENTUM)
            self.params["rmean"] += BN_MOMENTUM * mu
            self.params["rvar"] *= (1 - BN_MOMENTUM)
            self.params["rvar"] += BN_MOMENTUM * var
        else:
            mu = self.params["rmean"]
            var = self.params["rvar"]
        inv = 1.0 / np.sqrt(var + 1e-5)
        xh = (z1 - mu) * inv * self.params["gamma1"] + self.params["beta1"]
        a1 = np.maximum(xh, 0.0)
        out = a1 @ self.params["w2"] + self.params["b2"]
        if training:
            self._cache = (X, z1, mu, var, inv, xh, a1)
        return out[:, 0]

    def backward(self, X, pred, dy):
        _, z1, mu, var, inv, xh, a1 = self._cache
        B = X.shape[0]
        w2 = self.params["w2"]
        dz2 = dy[:, None].astype(np.float32)
        ga1 = (dz2 @ w2.T).astype(np.float32)                 # (B,H)
        gxh_raw = ga1 * (a1 > 0)                              # dL/dxh（gamma/beta 之前）
        xhat = (z1 - mu) * inv
        gxh_norm = gxh_raw * self.params["gamma1"]             # dL/dxhat_norm
        gz1 = _bn_backward(gxh_norm, z1, mu, var, inv)
        gw1 = (X.T @ gz1).astype(np.float32)
        gb1 = gz1.sum(axis=0).astype(np.float32)
        gw2 = (a1.T @ dz2).astype(np.float32)
        gb2 = np.array(dz2.sum(), dtype=np.float32)
        ggamma = (gxh_raw * xhat).sum(axis=0).astype(np.float32)
        gbeta = gxh_raw.sum(axis=0).astype(np.float32)
        return {"w1": gw1, "b1": gb1, "gamma1": ggamma, "beta1": gbeta,
                "w2": gw2, "b2": gb2}

    def opt_weight_decay(self):
        return WEIGHT_DECAY

    def predict(self, X):
        return self.forward(X, training=False)


def _bn_backward(gout, z, mu, var, inv):
    """BatchNorm 反向（对 batch 统计量求导）。"""
    B = gout.shape[0]
    # 标准形式：dL/dz_j = (1/sigma)*[ g_j - mean(g) - xhat_j * mean(g * xhat) ]
    # 注意没有额外的 1/B（B 只在 mean() 里出现一次）
    xhat = (z - mu) * inv
    gz = gout * inv
    mean_gz = gz.mean(axis=0)
    mean_xhat_gz = (xhat * gz).mean(axis=0)
    gz = gz - mean_gz - xhat * mean_xhat_gz
    return gz.astype(np.float32)


class SoftTreeExpert:
    """软决策树（§4.5）：CART 预生长冻结 (j, t)，可学习门参数 (a, b) 与叶值 mu。

    内部节点 n 的门：g_n(x) = sigmoid(a_n * (x_j - t_n) + b_n)
    输出：y_hat = sum_l P(l|x) * mu_l（路径概率加权）
    """

    def __init__(self, tree_fit, n_feat, rng):
        from sklearn.tree import DecisionTreeRegressor
        # CART 结构（在专家自己的 bootstrap 池上预生长）
        best = None
        min_leaf = max(1, tree_fit["n_pool"] // 512)
        for _ in range(8):
            dt = DecisionTreeRegressor(max_depth=TREE_DEPTH, min_samples_leaf=min_leaf,
                                       random_state=int(rng.integers(1 << 30)))
            dt.fit(tree_fit["X"], tree_fit["y"])
            nl = dt.get_n_leaves()
            if nl <= TREE_MAX_LEAVES:
                best = dt
                break
            min_leaf *= 2
        if best is None:
            best = dt
        self.dt = best
        t = best.tree_
        self.n_nodes = t.node_count
        self.children_left = t.children_left
        self.children_right = t.children_right
        self.feature = t.feature
        self.threshold = t.threshold
        self.is_leaf = self.children_left == -1
        self.internal = np.where(~self.is_leaf)[0]
        self.leaf_ids = np.where(self.is_leaf)[0]
        # 叶值初始化 = CART 叶均值
        self.mu0 = t.value[self.leaf_ids].reshape(-1).astype(np.float32)
        # BFS 分层（内部节点），并预先算好每层的特征/阈值/子节点/参数下标
        levels, cur = [], [0]
        while cur:
            nxt = []
            for n in cur:
                if not self.is_leaf[n]:
                    nxt.append(self.children_left[n])
                    nxt.append(self.children_right[n])
            nxt = [n for n in nxt if not self.is_leaf[n]]
            levels.append(np.array(cur, dtype=np.int64))
            cur = nxt
        self.levels = levels
        self.n_leaves = len(self.leaf_ids)
        self.n_feat = n_feat
        self.params = {
            "a": np.full(len(self.internal), TREE_GATE_A0, np.float32),
            "b": np.full(len(self.internal), TREE_GATE_B0, np.float32),
            "mu": self.mu0.copy(),
        }
        self.opt = Adam(self.params, no_decay=("b",))
        self._cache = None
        # 内部节点 -> 参数下标
        self.internal_pos = {int(n): i for i, n in enumerate(self.internal)}
        self.lvl = []
        for lv in levels:
            self.lvl.append({
                "ids": lv,
                "feat": self.feature[lv].astype(np.int64),
                "thr": self.threshold[lv].astype(np.float32),
                "left": self.children_left[lv].astype(np.int64),
                "right": self.children_right[lv].astype(np.int64),
                "pos": np.array([self.internal_pos[int(n)] for n in lv], dtype=np.int64),
            })

    def _level_arrays(self, i):
        d = self.lvl[i]
        return d["feat"], d["thr"], d["left"], d["right"], d["pos"]

    def forward(self, X, training=True):
        B = X.shape[0]
        prob = np.zeros((B, self.n_nodes), dtype=np.float32)
        prob[:, 0] = 1.0
        gs = []
        for li in range(len(self.lvl)):
            feat, thr, left, right, pos = self._level_arrays(li)
            a = self.params["a"][pos]
            b = self.params["b"][pos]
            g = sigmoid((a * (X[:, feat] - thr) + b).astype(np.float64)).astype(np.float32)
            par = prob[:, self.lvl[li]["ids"]]
            prob[:, left] = par * g
            prob[:, right] = par * (1.0 - g)
            gs.append((li, g))
        out = prob[:, self.leaf_ids] @ self.params["mu"]
        if training:
            self._cache = (prob, gs)
        return out

    def backward(self, X, pred, dy):
        prob, gs = self._cache
        B = X.shape[0]
        dprob = np.zeros((B, self.n_nodes), dtype=np.float32)
        # out_b = sum_l P(b,l)*mu_l  ->  dL/dP(b,l) = dy_b * mu_l （dy 必须带进来）
        dprob[:, self.leaf_ids] = dy[:, None] * self.params["mu"][None, :]
        da = np.zeros(len(self.internal), np.float32)
        db = np.zeros(len(self.internal), np.float32)
        for li, g in reversed(gs):
            feat, thr, left, right, pos = self._level_arrays(li)
            par = prob[:, self.lvl[li]["ids"]]
            dl = dprob[:, left]
            dr = dprob[:, right]
            dg = par * (dl - dr)
            # prob_left = par*g, prob_right = par*(1-g) -> dprob_par += dl*g + dr*(1-g)
            dprob[:, self.lvl[li]["ids"]] += dl * g + dr * (1.0 - g)
            gp = g * (1.0 - g)
            z = X[:, feat] - thr
            da[pos] = (dg * gp * z).sum(axis=0).astype(np.float32)
            db[pos] = (dg * gp).sum(axis=0).astype(np.float32)
        # dL/dmu_l = sum_b P(b,l) * dy_b
        dmu = (prob[:, self.leaf_ids].T @ dy).astype(np.float32)
        return {"a": da, "b": db, "mu": dmu}

    def opt_weight_decay(self):
        return WEIGHT_DECAY

    def predict(self, X):
        return self.forward(X, training=False)


def make_expert(kind, n_feat, rng, tree_fit=None):
    if kind in ("linear", "ridge", "lasso", "elasticnet"):
        return LinearExpert(kind, n_feat, rng)
    if kind == "mlp":
        return MlpExpert(n_feat, rng)
    if kind == "tree":
        return SoftTreeExpert(tree_fit, n_feat, rng)
    raise ValueError(kind)


# ================================================================ 随机化协议
def swrr_types(ratios, n):
    """平滑加权轮转（预注册 §4.2 第 3 条 + 附录 B）。"""
    q = np.asarray(ratios, dtype=np.float64)
    credit = np.zeros(len(q), dtype=np.float64)
    order = []
    for _ in range(n):
        credit = credit + q
        k = int(np.argmax(credit))
        order.append(k)
        credit[k] -= n
    return order


def make_feature_subsets(rngs, n, n_feat, gamma):
    """随机特征子集 + 全覆盖约束（§4.2 第 2 条）。"""
    k = int(np.floor(gamma * n_feat))
    subsets = [np.sort(rng.choice(n_feat, size=k, replace=False)) for rng in rngs]
    cover = np.zeros(n_feat, dtype=np.int64)
    for s in subsets:
        cover[s] += 1
    missing = [f for f in range(n_feat) if cover[f] == 0]
    repairs = []
    mi = 0
    guard = 0
    while missing and guard < 10000:
        guard += 1
        f = missing[0]
        k_exp = mi % n
        mi += 1
        cand = [j for j in range(len(subsets[k_exp])) if cover[subsets[k_exp][j]] > 1]
        if not cand:
            cand = list(range(len(subsets[k_exp])))
        j = int(rngs[k_exp].integers(len(cand)))
        j = cand[j]
        removed = int(subsets[k_exp][j])
        cover[removed] -= 1
        cover[f] += 1
        subsets[k_exp][j] = f
        repairs.append({"expert": k_exp, "added": f, "removed": removed})
        missing = [x for x in range(n_feat) if cover[x] == 0]
    return [np.sort(s) for s in subsets], repairs, missing


def rank_ic_by_day(pred, y, day_idx, n_days):
    """逐日截面 Spearman（用秩的 Pearson），返回逐日 IC 数组（样本不足则为 NaN）。"""
    out = np.full(n_days, np.nan)
    order = np.argsort(day_idx, kind="stable")
    d_sorted = day_idx[order]
    bounds = np.searchsorted(d_sorted, np.arange(n_days + 1))
    for d in range(n_days):
        a, b = bounds[d], bounds[d + 1]
        if b - a < 3:
            continue
        p = pred[order[a:b]]
        t = y[order[a:b]]
        rp = _rank(p)
        rt = _rank(t)
        sp = rp.std()
        st = rt.std()
        if sp <= 0 or st <= 0:
            continue
        out[d] = float(np.mean((rp - rp.mean()) * (rt - rt.mean())) / (sp * st))
    return out


def _rank(a):
    order = np.argsort(a, kind="stable")
    ranks = np.empty(len(a), dtype=np.float64)
    ranks[order] = np.arange(len(a), dtype=np.float64)
    # 平均秩（处理并列）
    s = a[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return ranks


def pearson_ic_by_day(pred, y, day_idx, n_days):
    out = np.full(n_days, np.nan)
    order = np.argsort(day_idx, kind="stable")
    d_sorted = day_idx[order]
    bounds = np.searchsorted(d_sorted, np.arange(n_days + 1))
    for d in range(n_days):
        a, b = bounds[d], bounds[d + 1]
        if b - a < 3:
            continue
        p = pred[order[a:b]]
        t = y[order[a:b]]
        if p.std() <= 0 or t.std() <= 0:
            continue
        out[d] = float(np.corrcoef(p, t)[0, 1])
    return out


# ================================================================ 训练
def train_ensemble(Xtr, ytr, tr_idx, Xva, yva, va_idx, n_val_days, n_feat,
                   type_seq, seeds, log, max_steps=S_MAX):
    """同步训练循环（论文 Algorithm 1）。返回 (experts, subsets, best_step, history)。"""
    n = len(type_seq)
    T = len(tr_idx)
    pool_size = int(np.floor(BETA * T))

    # ---- 三重随机化
    boot = [seeds["boot"][k].integers(0, T, size=pool_size) for k in range(n)]
    subsets, repairs, missing = make_feature_subsets(
        [seeds["feat"][k] for k in range(n)], n, n_feat, GAMMA)
    log["coverage_repairs"] = repairs
    log["coverage_missing_after"] = missing

    # ---- 建专家
    experts = []
    for k in range(n):
        kind = TYPE_NAMES[type_seq[k]]
        nk = len(subsets[k])
        tree_fit = None
        if kind == "tree":
            # boot[k] 存的是**训练段内**的行号（0..T-1），不是全局行号
            sel = boot[k]
            tree_fit = {"X": Xtr[sel][:, subsets[k]],
                        "y": ytr[sel], "n_pool": pool_size}
        ex = make_expert(kind, nk, seeds["init"][k], tree_fit)
        experts.append(ex)

    # ---- 固定验证子样本
    val_sub = seeds["val"].choice(len(Xva), size=min(VAL_SUBSAMPLE, len(Xva)), replace=False)
    Xvs = Xva[val_sub]
    yvs = yva[val_sub]

    # ---- 同步循环
    best_loss, best_step, best_snap, bad = np.inf, 0, None, 0
    history = []
    t0 = time.time()
    for step in range(1, max_steps + 1):
        for k in range(n):
            ex = experts[k]
            sel = seeds["batch"][k].integers(0, pool_size, size=BATCH)
            rows = boot[k][sel]          # 训练段内行号（Xtr/ytr 按此索引）
            Xb = Xtr[rows][:, subsets[k]]
            yb = ytr[rows]
            pred = ex.forward(Xb)
            dy = (2.0 / len(yb)) * (pred - yb)
            grads = ex.backward(Xb, pred, dy)
            ex.opt.step(grads, ex.opt_weight_decay())
        if step % EVAL_EVERY == 0:
            tot, cnt = 0.0, 0
            for k in range(n):
                p = experts[k].forward(Xvs[:, subsets[k]], training=False)
                tot += float(np.mean((p - yvs) ** 2))
                cnt += 1
            vloss = tot / cnt
            history.append({"step": step, "ens_val_mse": vloss})
            if vloss < best_loss - 1e-12:
                best_loss, best_step, bad = vloss, step, 0
                best_snap = [ex.opt.snapshot() for ex in experts]
            else:
                bad += 1
                if bad >= PATIENCE:
                    break
    for k, ex in enumerate(experts):
        if best_snap is not None:
            ex.opt.restore(best_snap[k])
    log["train"] = {
        "pool_size": pool_size, "n_train_rows": int(T), "steps_run": step,
        "best_step": best_step, "best_ens_val_mse": best_loss,
        "early_stopped": bool(bad >= PATIENCE), "elapsed_sec": round(time.time() - t0, 1),
        "eval_count": len(history),
    }
    log["val_loss_tail"] = history[-5:]
    return experts, subsets, boot


def aggregate_and_filter(experts, subsets, Xva, yva, va_idx, n_val_days, log):
    """验证段逐专家日频 RankIC -> IC 过滤 -> 等权聚合（§4.4）。"""
    ics = []
    for k, ex in enumerate(experts):
        p = ex.predict(Xva[:, subsets[k]])
        daily = rank_ic_by_day(p, yva, va_idx, n_val_days)
        ics.append(float(np.nanmean(daily)))
    ics = np.array(ics, dtype=np.float64)
    thr = float(np.mean(ics) - KAPPA * np.std(ics, ddof=0))
    active = np.where(ics >= thr)[0]
    fallback = False
    if len(active) == 0:
        active = np.arange(len(experts))
        fallback = True
    log["ic_filter"] = {
        "per_expert_valid_rankic": [round(float(v), 6) for v in ics],
        "threshold": round(thr, 6),
        "active_experts": [int(a) for a in active],
        "n_active": int(len(active)),
        "degenerate_filter_fallback": fallback,
        "single_expert_degenerate": bool(len(active) == 1),
    }
    return ics, active


# ================================================================ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["dee", "mlp_only", "xgb"])
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--max-steps", type=int, default=S_MAX)
    ap.add_argument("--bench", type=int, default=0, help="只跑前 N 步做计时")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    d = np.load(os.path.join(OUT, "sample.npz"))
    X, y = d["X"], d["y"]
    date_idx, code_idx, split = d["date_idx"], d["code_idx"], d["split"]
    bt_row_id = d["bt_row_id"]
    with open(os.path.join(OUT, "sample_meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    n_feat = X.shape[1]
    dates = np.array(meta["dates"])
    n_dates = len(dates)

    tr = np.where(split == 0)[0]
    va = np.where(split == 1)[0]
    te = np.where(split == 2)[0]
    va_days = np.unique(date_idx[va])
    va_day_remap = np.searchsorted(va_days, date_idx[va])
    te_days = np.unique(date_idx[te])
    te_day_remap = np.searchsorted(te_days, date_idx[te])

    log = {"arm": args.arm, "seed": args.seed, "n_feat": n_feat,
           "n_train": int(len(tr)), "n_valid": int(len(va)), "n_test": int(len(te)),
           "prereg": "experiments/WORK-555/preregistration.md v1.0 (commit 9a85409)"}

    # ---- 随机源派生（numpy SeedSequence 固定 spawn 键）
    ss = np.random.SeedSequence(args.seed)
    child = ss.spawn(6)

    if args.arm == "xgb":
        import xgboost as xgb_
        rs = np.random.RandomState(args.seed)
        model = xgb_.XGBRegressor(random_state=args.seed, n_jobs=4, **XGB_PARAMS)
        model.fit(X[tr], y[tr], eval_set=[(X[va], y[va])], verbose=False)
        pred_te = model.predict(X[te]).astype(np.float64)
        pred_va = model.predict(X[va]).astype(np.float64)
        daily = rank_ic_by_day(pred_te, y[te], te_day_remap, len(te_days))
        ic_d = pearson_ic_by_day(pred_te, y[te], te_day_remap, len(te_days))
        log["xgb"] = {"best_iteration": int(getattr(model, "best_iteration", -1)),
                      "n_estimators_used": int(getattr(model, "best_iteration", -1)) + 1}
        np.savez_compressed(os.path.join(OUT, f"preds_xgb_s{args.seed}.npz"),
                            pred=pred_te.astype(np.float32),
                            row=bt_row_id[te].astype(np.int64),
                            date_idx=te_day_remap.astype(np.int32))
        log["test_rankic_mean"] = float(np.nanmean(daily))
        log["test_ic_mean"] = float(np.nanmean(ic_d))
        log["elapsed_sec"] = round(time.time() - t0, 1)
        with open(os.path.join(OUT, f"run_xgb_s{args.seed}.json"), "w", encoding="utf-8") as fh:
            json.dump(log, fh, ensure_ascii=False, indent=2)
        print(json.dumps(log, ensure_ascii=False, indent=2))
        return 0

    # ---- 类型序列
    if args.arm == "dee":
        type_seq = swrr_types(TYPE_RATIO_DEE, N_EXPERTS)
    else:
        type_seq = swrr_types([0, 0, 0, 0, N_EXPERTS, 0], N_EXPERTS)
    log["type_seq"] = [TYPE_NAMES[t] for t in type_seq]

    # ---- 每专家子随机源
    def substream(key):
        # spawn_key 必须是扁平 uint32 序列，不能嵌套
        k = key if isinstance(key, (tuple, list)) else (key,)
        return np.random.default_rng(np.random.SeedSequence(entropy=args.seed,
                                                             spawn_key=tuple(int(x) for x in k)))
    seeds = {
        "boot": [substream((1, k)) for k in range(N_EXPERTS)],
        "feat": [substream((2, k)) for k in range(N_EXPERTS)],
        "init": [substream((3, k)) for k in range(N_EXPERTS)],
        "batch": [substream((4, k)) for k in range(N_EXPERTS)],
        "val": substream((5, 0)),
    }

    Xtr, ytr, tr_idx = X[tr], y[tr], tr
    Xva, yva = X[va], y[va]

    max_steps = args.max_steps if args.bench else args.max_steps
    if args.bench:
        max_steps = min(args.bench, args.max_steps)

    experts, subsets, boot = train_ensemble(
        Xtr, ytr, tr_idx, Xva, yva, va_day_remap, len(va_days), n_feat,
        type_seq, seeds, log, max_steps=max_steps)
    if args.bench:
        log["bench"] = True
        with open(os.path.join(OUT, f"bench_{args.arm}_s{args.seed}.json"), "w", encoding="utf-8") as fh:
            json.dump(log, fh, ensure_ascii=False, indent=2)
        print(json.dumps(log["train"], ensure_ascii=False, indent=2))
        return 0

    # ---- IC 过滤 + 聚合（§4.4）
    ics, active = aggregate_and_filter(experts, subsets, Xva, yva, va_day_remap,
                                       len(va_days), log)

    # ---- 测试段预测
    pred_te = np.zeros(len(te), dtype=np.float64)
    for k in active:
        pred_te += experts[k].predict(X[te][:, subsets[k]])
    pred_te /= len(active)

    daily = rank_ic_by_day(pred_te, y[te], te_day_remap, len(te_days))
    ic_d = pearson_ic_by_day(pred_te, y[te], te_day_remap, len(te_days))
    log["test_rankic_mean"] = float(np.nanmean(daily))
    log["test_ic_mean"] = float(np.nanmean(ic_d))
    log["test_rankic_daily_n"] = int(np.sum(~np.isnan(daily)))

    # ---- 附加免费对照：从同一批专家按族切分（§4.6）
    fam_subsets = {}
    for fam, kinds in (("linear_family", {"linear", "ridge", "lasso", "elasticnet"}),
                       ("mlp_family", {"mlp"}), ("tree_family", {"tree"}),
                       ("full_dee", None)):
        if kinds is None:
            idx = [k for k in range(len(experts))
                   if TYPE_NAMES[type_seq[k]] in ("linear", "ridge", "lasso", "elasticnet", "mlp", "tree")]
        else:
            idx = [k for k in range(len(experts)) if TYPE_NAMES[type_seq[k]] in kinds]
        if not idx:
            continue
        p = np.zeros(len(te))
        for k in idx:
            p += experts[k].predict(X[te][:, subsets[k]])
        p /= len(idx)
        fam_subsets[fam] = {
            "n_experts": len(idx),
            "rankic": float(np.nanmean(rank_ic_by_day(p, y[te], te_day_remap, len(te_days)))),
            "ic": float(np.nanmean(pearson_ic_by_day(p, y[te], te_day_remap, len(te_days)))),
        }
    log["family_subsets"] = fam_subsets

    np.savez_compressed(os.path.join(OUT, f"preds_{args.arm}_s{args.seed}.npz"),
                        pred=pred_te.astype(np.float32),
                        row=bt_row_id[te].astype(np.int64),
                        date_idx=te_day_remap.astype(np.int32),
                        y=y[te].astype(np.float32))
    log["elapsed_sec"] = round(time.time() - t0, 1)
    with open(os.path.join(OUT, f"run_{args.arm}_s{args.seed}.json"), "w", encoding="utf-8") as fh:
        json.dump(log, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in log.items()
                      if k in ("arm", "seed", "type_seq", "test_rankic_mean",
                               "test_ic_mean", "elapsed_sec")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
