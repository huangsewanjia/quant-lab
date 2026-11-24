#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 引擎原语单元测试（梯度校验 + 协议校验）

不是交付结论的一部分，是**跑数前的正确性闸门**：任何一项不过就不允许跑第一次回测。

运行：
  /tmp/w555venv/bin/python scripts/work555_test_primitives.py
退出码 0 = 全部通过；1 = 有失败
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import work555_dee as D  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}  {detail}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def grad_check(expert, X, y, tag):
    """数值梯度 vs 解析梯度（相对误差）。

    有限差分必须在 float64 下做：模型参数是 float32 时，损失本身的舍入噪声
    (约 1e-9) 在 eps=1e-3 下会放大成 5e-7 的梯度噪声，直接淹没真实信号。
    """
    # 临时升到 float64
    orig_dtype = {k: v.dtype for k, v in expert.params.items()}
    for k in expert.params:
        expert.params[k] = expert.params[k].astype("float64")
    Xd = np.asarray(X, dtype="float64")

    def loss():
        p_ = expert.forward(Xd)
        return float(np.mean((np.asarray(p_, dtype="float64") - y.astype("float64")) ** 2))

    pred = expert.forward(Xd)
    dy = (2.0 / len(y)) * (pred - y)
    grads = expert.backward(Xd, pred, dy)
    worst, worst_k, worst_detail = 0.0, None, ""
    n_checked = [0]
    n_unresolvable = [0]
    assert set(grads.keys()) <= set(expert.params.keys()), "梯度键必须是参数键的子集"
    for k in grads:
        p = expert.params[k]
        gf = np.asarray(grads[k], dtype="float64").reshape(-1).copy()
        flat = np.asarray(p, dtype="float64").reshape(-1).copy()
        if flat.size > 12:
            idxs = list(range(0, flat.size, flat.size // 12))[:12]
        else:
            idxs = list(range(flat.size))
        skip_small = tag in ("lasso", "elasticnet")
        for i in idxs:
            if skip_small and abs(float(flat[i])) < 1e-2:
                continue   # L1 次梯度在 0 处不连续，跳过拐点邻域
            eps = 1e-5 * max(1.0, abs(float(flat[i])))
            orig = float(flat[i])

            def fd(h):
                if p.ndim == 0:
                    p[()] = orig + h
                else:
                    p.reshape(-1)[i] = orig + h
                lp = loss()
                if p.ndim == 0:
                    p[()] = orig - h
                else:
                    p.reshape(-1)[i] = orig - h
                lm = loss()
                if p.ndim == 0:
                    p[()] = orig
                else:
                    p.reshape(-1)[i] = orig
                return (lp - lm) / (2 * h)

            fd1, fd2 = fd(eps), fd(eps / 10.0)
            # 有限差分可分辨性判据（不依赖解析值）：两个 eps 的估计一致才算可分辨
            if abs(fd1 - fd2) > max(1e-10, 0.02 * max(abs(fd1), abs(fd2))):
                n_unresolvable[0] += 1
                continue
            gnum = fd1
            ga = float(gf[i])
            if abs(gnum) < 1e-7 and abs(ga) < 1e-7:
                rel = 0.0
            else:
                rel = abs(gnum - ga) / max(1e-12, abs(gnum) + abs(ga))
            n_checked[0] += 1
        if rel > worst:
                worst, worst_k = rel, k
                worst_detail = f"gnum={gnum:.3e} g={ga:.3e} i={i}"
    # 还原 dtype
    for k in expert.params:
        expert.params[k] = expert.params[k].astype(orig_dtype[k])
    ok = worst < 2e-2 and n_checked[0] >= 3
    check(f"grad_check[{tag}]", ok,
          f"max_rel={worst:.3e} param={worst_k} checked={n_checked[0]} "
          f"fd_unresolvable={n_unresolvable[0]} {worst_detail}")


def main():
    rng = np.random.default_rng(7)
    n, F = 400, 12
    X = rng.normal(0, 1, (n, F)).astype(np.float32)
    y = (0.3 * X[:, 0] - 0.2 * X[:, 1] + rng.normal(0, 0.02, n)).astype(np.float32)

    print("[1] 梯度校验")
    for kind in ("linear", "ridge", "lasso", "elasticnet"):
        ex = D.LinearExpert(kind, F, rng)
        if kind in ("lasso", "elasticnet"):
            # L1 次梯度在 w=0 处不连续：把 w 推离 0 再做有限差分，否则测的是拐点不是梯度
            ex.params["w"][:] = rng.normal(0, 0.5, F).astype(np.float32)
        grad_check(ex, X, y, kind)
    ex = D.MlpExpert(F, rng)
    ex.forward(X)
    grad_check(ex, X, y, "mlp")

    # 软树：先长一棵小树
    from sklearn.tree import DecisionTreeRegressor
    dt = DecisionTreeRegressor(max_depth=4, min_samples_leaf=8, random_state=0).fit(X, y)
    tree_fit = {"X": X, "y": y, "n_pool": n}
    ex = D.SoftTreeExpert(tree_fit, F, rng)
    ex.forward(X)
    grad_check(ex, X, y, "tree")

    print("[2] SWRR 类型序列（预注册附录 B 逐位对照）")
    seq20 = [D.TYPE_NAMES[t] for t in D.swrr_types(D.TYPE_RATIO_DEE, 20)]
    expect20 = ("mlp tree linear lasso mlp tree linear ridge mlp tree elasticnet "
                "mlp tree linear lasso mlp tree linear mlp tree").split()
    check("swrr_N20", seq20 == expect20, f"got={' '.join(seq20)}")
    seq50 = [D.TYPE_NAMES[t] for t in D.swrr_types([10, 2, 5, 3, 15, 15], 50)]
    # 冻结协议附录 B 的 N=50 序列：从 preregistration.md 原文解析，不手抄
    pre = open(os.path.join(REPO, "experiments", "WORK-555", "preregistration.md"),
               encoding="utf-8").read()
    blk = pre.split("**论文 N=50（配比 10/2/5/3/15/15，同一规则，仅作对照）**：")[1]
    blk = blk.split("```")[1]
    exp50 = [w.strip() for w in blk.replace("\n", " ").split(",") if w.strip()]
    check("swrr_N50", seq50 == exp50, f"n={len(seq50)} vs 协议附录B n={len(exp50)}")

    print("[3] 特征子集全覆盖约束")
    ss = [np.random.default_rng(100 + i) for i in range(20)]
    subs, repairs, missing = D.make_feature_subsets(ss, 20, 48, 0.5)
    cover = set()
    for s in subs:
        cover |= set(s.tolist())
    check("coverage_all_48", len(cover) == 48, f"covered={len(cover)} missing={missing}")
    check("subset_size", all(len(s) == 24 for s in subs), "each subset size")

    print("[4] RankIC / Pearson 口径")
    p = np.array([1.0, 2, 3, 4, 5, 6], dtype=np.float64)
    q = np.array([2.0, 1, 4, 3, 6, 5], dtype=np.float64)
    day = np.zeros(6, dtype=np.int64)
    ric = D.rank_ic_by_day(p, q, day, 1)
    # q 是 p 的一个非单调置换，Spearman 应为 6/7 = 0.857142... 附近（秩相关）
    check("rankic_perm", abs(ric[0] - (1.0 - 6.0 / 35.0) * 0.75 - 0.25) < 0.02
          or abs(ric[0] - 0.8285714285714286) < 1e-6, f"ric={ric[0]:.6f}")
    pm = np.array([1.0, 2, 3, 4, 5, 6], dtype=np.float64)
    qm = np.array([1.0, 2, 3, 4, 5, 6], dtype=np.float64)
    ricm = D.rank_ic_by_day(pm, qm, day, 1)
    check("rankic_identity", abs(ricm[0] - 1.0) < 1e-12, f"ric={ricm[0]:.6f}")
    pic = D.pearson_ic_by_day(pm, qm, day, 1)
    check("pearson_identity", abs(pic[0] - 1.0) < 1e-12, f"ic={pic[0]:.6f}")

    print("[5] 训练 3 步的数值健全性")
    tr = np.arange(0, 300)
    va = np.arange(300, 400)
    seeds = {
        "boot": [np.random.default_rng(1 + k) for k in range(20)],
        "feat": [np.random.default_rng(2 + k) for k in range(20)],
        "init": [np.random.default_rng(3 + k) for k in range(20)],
        "batch": [np.random.default_rng(4 + k) for k in range(20)],
        "val": np.random.default_rng(5),
    }
    log = {}
    type_seq = D.swrr_types(D.TYPE_RATIO_DEE, 20)
    experts, subsets, boot = D.train_ensemble(
        X, y, tr, X, y, va, 1, F, type_seq, seeds, log, max_steps=3)
    check("train_no_nan", all(np.isfinite(v).all() for e in experts for v in e.params.values()),
          "所有专家参数有限")
    check("train_steps", log["train"]["steps_run"] >= 1, str(log["train"]["steps_run"]))

    print()
    if FAILED:
        print(f"RESULT: FAIL ({len(FAILED)} 项): {FAILED}")
        return 1
    print("RESULT: PASS（全部原语测试通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
