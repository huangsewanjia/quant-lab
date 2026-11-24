#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 梯度单测漏检自证（修复 S1 后的变异测试）

审查发现（verifier）：`scripts/work555_test_primitives.py` 的 `grad_check()` 里
`if rel > worst:` 缩进在 `for i in idxs:` 循环**之外** -> 每个参数族只比较了
**最后一个**抽样下标，其余下标上的梯度错误可被完全漏检。

本脚本是**自证**，分两条腿：

腿 1（必须抓到）：把解析梯度在某个 **FD 可分辨** 的抽样下标上人为污染（×3 + 0.01），
  要求 grad_check 报 FAIL，且**报出的最差下标就是被污染的那个下标**
  （只看 ok=False 是不够的 —— 覆盖度门也可能让 ok=False，那是另一回事）。

腿 2（已知局限，必须如实记录）：污染一个 **FD 不可分辨** 的下标，
  有限差分本来就分辨不出该处梯度，grad_check **不应**声称抓到了它。
  这一腿用来证明「单测的检出力有边界」，而不是假装全覆盖。

**注意**：本脚本只做自证，不参与研究结论，不写任何产物文件。
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import work555_test_primitives as T  # noqa: E402
import work555_dee as D  # noqa: E402


def build(kind, rng, X, y, n, F):
    if kind == "linear":
        return D.LinearExpert("linear", F, rng)
    if kind == "tree":
        from sklearn.tree import DecisionTreeRegressor
        dt = DecisionTreeRegressor(max_depth=4, min_samples_leaf=8, random_state=0).fit(X, y)
        tf = {"X": X, "y": y, "n_pool": n}
        return D.SoftTreeExpert(tf, F, rng)
    raise ValueError(kind)


def sampled_idxs(expert):
    out = {}
    for k in expert.params:
        flat = np.asarray(expert.params[k], dtype="float64").reshape(-1)
        if flat.size > 12:
            idxs = list(range(0, flat.size, flat.size // 12))[:12]
        else:
            idxs = list(range(flat.size))
        out[k] = idxs
    return out


def fd_resolvable(expert, X, y, param, i):
    """复现 grad_check 的有限差分可分辨性判据，判断下标 i 是否可分辨。"""
    orig_dtype = {k: v.dtype for k, v in expert.params.items()}
    for k in expert.params:
        expert.params[k] = expert.params[k].astype("float64")
    Xd = np.asarray(X, dtype="float64")
    yd = y.astype("float64")

    def loss():
        p = expert.forward(Xd)
        return float(np.mean((np.asarray(p, dtype="float64") - yd) ** 2))

    p = expert.params[param]
    flat = np.asarray(p, dtype="float64").reshape(-1).copy()
    orig = float(flat[i])
    eps = 1e-5 * max(1.0, abs(orig))

    def fd(h):
        p.reshape(-1)[i] = orig + h
        lp = loss()
        p.reshape(-1)[i] = orig - h
        lm = loss()
        p.reshape(-1)[i] = orig
        return (lp - lm) / (2 * h)

    fd1, fd2 = fd(eps), fd(eps / 10.0)
    unresolvable = abs(fd1 - fd2) > max(1e-10, 0.02 * max(abs(fd1), abs(fd2)))
    for k in expert.params:
        expert.params[k] = expert.params[k].astype(orig_dtype[k])
    return (not unresolvable)


def run_case(kind, param, idx_pos):
    """污染一个抽样下标上的解析梯度，返回 (是否被**误差比较**抓到, 详情)。"""
    rng = np.random.default_rng(7)
    n, F = 400, 12
    X = rng.normal(0, 1, (n, F)).astype(np.float32)
    y = (0.3 * X[:, 0] - 0.2 * X[:, 1] + rng.normal(0, 0.02, n)).astype(np.float32)
    ex = build(kind, rng, X, y, n, F)
    ex.forward(X)

    idxs = sampled_idxs(ex)[param]
    if idx_pos >= len(idxs):
        return None, None, "idx out of range"
    target_i = idxs[idx_pos]

    pred = ex.forward(X.astype("float64"))
    dy = (2.0 / len(y)) * (pred - y.astype("float64"))
    orig_grads = {k: np.array(v, copy=True) for k, v in ex.backward(X.astype("float64"), pred, dy).items()}
    g = orig_grads[param]
    fg = g.reshape(-1)
    fg[target_i] = fg[target_i] * 3.0 + 0.01
    orig_grads[param] = fg.reshape(g.shape)

    resolvable = fd_resolvable(ex, X, y, param, target_i)

    ex.backward = lambda X_, pred_, dy_: orig_grads

    captured = []
    real_check = T.check
    T.check = lambda name, ok, detail: captured.append((name, ok, detail))
    try:
        info = T.grad_check(ex, X, y, kind)
    finally:
        T.check = real_check
    if not captured:
        return None, resolvable, "no check"
    name, ok, detail = captured[0]
    # 只有当「最差参数 == 被污染的参数」且「最差下标 == 被污染的下标」时，
    # 才算被**误差比较**抓到（不看字符串，避免把别的族的 detail 误配过来）
    caught_by_rel = (not ok) and (info["worst_param"] == param) and \
        (info["worst_index"] == target_i)
    return caught_by_rel, resolvable, f"{name} ok={ok} {detail}"


def main():
    print("=" * 78)
    print("S1 变异测试：污染解析梯度后，grad_check 是否在**被污染的下标**上报错")
    print("=" * 78)
    leg1 = []
    leg2 = []
    for kind, param in (("linear", "w"), ("tree", "a")):
        for pos in range(12):
            caught, resolvable, detail = run_case(kind, param, pos)
            if caught is None:
                print(f"  SKIP  {kind}.{param} idx_pos={pos}: {detail}")
                continue
            tag = f"{kind}.{param} idx_pos={pos:2d} (sampled i={sampled_pos(kind, param, pos)})"
            if resolvable:
                verdict = "CAUGHT " if caught else "MISSED "
                print(f"  {verdict} [可分辨] {tag}: {detail}")
                leg1.append((tag, caught))
            else:
                verdict = "caught" if caught else "not-caught(预期)"
                print(f"  {verdict} [不可分辨] {tag}: {detail}")
                leg2.append((tag, caught))

    print()
    n1 = sum(1 for _, c in leg1 if c)
    print(f"腿1（FD 可分辨下标，必须全部抓到）: {n1}/{len(leg1)}")
    n2 = sum(1 for _, c in leg2 if c)
    print(f"腿2（FD 不可分辨下标，有限差分本就分辨不出 -> 不应声称抓到）: "
          f"声称抓到 {n2}/{len(leg2)}")
    ok = (n1 == len(leg1)) and len(leg1) > 0 and n2 == 0
    print()
    if ok:
        print("RESULT: PASS（修复后：可分辨下标的错误 100% 被抓到；不可分辨下标如实不声称抓到）")
        return 0
    print("RESULT: FAIL")
    return 1


_POS_CACHE = {}


def sampled_pos(kind, param, pos):
    key = (kind, param)
    if key not in _POS_CACHE:
        rng = np.random.default_rng(7)
        n, F = 400, 12
        X = rng.normal(0, 1, (n, F)).astype(np.float32)
        y = (0.3 * X[:, 0] - 0.2 * X[:, 1] + rng.normal(0, 0.02, n)).astype(np.float32)
        ex = build(kind, rng, X, y, n, F)
        _POS_CACHE[key] = sampled_idxs(ex)[param]
    return _POS_CACHE[key][pos]


if __name__ == "__main__":
    sys.exit(main())
