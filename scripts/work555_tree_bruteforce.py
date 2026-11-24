#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 软决策树梯度的**独立全路径枚举暴力对照**

为什么需要这个脚本（审查方 verifier 的 S1 建议 + failure_log §3.4 的可复现性缺口）：

1. `scripts/work555_test_primitives.py` 对软树只做**有限差分**对照。
   软树的门参数在 float64 下对损失极不敏感（实测 16~19 个抽样下标
   「FD 不可分辨」），即 FD 对 `a`（门斜率）这一族的检出力很弱。
2. failure_log §3.4 声称「另用全路径枚举的暴力实现对照」，但**该暴力实现当时没有落盘**，
   导致 §3.4 引用的数字无法在仓库内复跑 —— 这是可复现性缺口。

本脚本补上：写一个**与建模代码完全独立**的软树实现（逐样本沿所有根→叶路径枚举
路径概率乘积），用它来：
  - 对照 forward：暴力实现 vs 建模实现，max|diff| 必须 ~1e-8
  - 对照梯度：暴力实现的解析梯度（路径枚举 + 手推链式法则）vs 建模实现的 backward

**本脚本不 import 建模代码里的 SoftTreeExpert 的 forward/backward 来做「对照」**——
它自己实现一遍，然后只调用建模代码的 forward/backward 作为被测对象。
它只做单测，不参与研究结论，不写任何产物文件。

运行：
  /tmp/w555venv/bin/python scripts/work555_tree_bruteforce.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import work555_dee as D  # noqa: E402


# ============================================================ 独立暴力实现
class BruteSoftTree:
    """全路径枚举的软决策树（独立实现，不复用建模代码的 forward/backward）。

    内部节点 n：g_n(x) = sigmoid(a_n * (x_{j_n} - t_n) + b_n)
    输出：y_hat = sum_l P(l|x) * mu_l
    """

    def __init__(self, dt):
        t = dt.tree_
        self.children_left = t.children_left
        self.children_right = t.children_right
        self.feature = t.feature
        self.threshold = t.threshold
        self.is_leaf = t.children_left == -1
        self.internal = np.where(~self.is_leaf)[0]
        self.leaf_ids = np.where(self.is_leaf)[0]
        # 内部节点 -> 参数下标（与建模代码同序：internal 按节点号升序）
        self.internal_pos = {int(n): i for i, n in enumerate(self.internal)}
        self.mu0 = t.value[self.leaf_ids].reshape(-1).astype(np.float64)
        self.n_nodes = t.node_count
        # 父节点表（供 prob 自顶向下递推）
        self.parent_of = {}
        for n in self.internal:
            self.parent_of[int(self.children_left[n])] = int(n)
            self.parent_of[int(self.children_right[n])] = int(n)
        self._post = None
        self._pre = None

    def forward(self, X, a, b, mu):
        """暴力 forward：逐样本枚举所有根->叶路径。"""
        B = X.shape[0]
        out = np.zeros(B, dtype=np.float64)
        # 枚举路径：从根开始 DFS
        def dfs(node, prob, x):
            if self.is_leaf[node]:
                out[:] += prob * mu[self.leaf_ids.tolist().index(node)]
                return
            pos = self.internal_pos[int(node)]
            j = self.feature[node]
            thr = self.threshold[node]
            g = 1.0 / (1.0 + np.exp(-(a[pos] * (x[j] - thr) + b[pos])))
            dfs(self.children_left[node], prob * g, x)
            dfs(self.children_right[node], prob * (1.0 - g), x)

        leaf_pos = {int(nd): i for i, nd in enumerate(self.leaf_ids)}
        for bi in range(B):
            x = X[bi]
            acc = np.zeros((), dtype=np.float64)

            def dfs2(node, prob):
                if self.is_leaf[node]:
                    return prob * mu[leaf_pos[int(node)]]
                pos = self.internal_pos[int(node)]
                j = self.feature[node]
                thr = self.threshold[node]
                g = 1.0 / (1.0 + np.exp(-(a[pos] * (x[j] - thr) + b[pos])))
                return (dfs2(self.children_left[node], prob * g)
                        + dfs2(self.children_right[node], prob * (1.0 - g)))

            out[bi] = dfs2(0, 1.0)
            del acc
        return out

    def grads(self, X, a, b, mu, dy):
        """暴力梯度：**子树期望值 + 到达概率**分解（与建模代码的逐层反向传播是不同的推导路径）。

        定义：
          v[n]      = 节点 n 子树的期望叶值（v[leaf]=mu_l；v[n]=g_n*v[l]+(1-g_n)*v[r]）
          prob[n]   = 到达节点 n 的概率（根为 1；prob[child]=prob[parent]*g_parent）
        则：
          dL/dmu_l = sum_b dy_b * prob[leaf]_b
          dL/dg_n  = sum_b dy_b * prob[n]_b * (v[l]-v[r])
          dL/da_n  = dL/dg_n * g_n(1-g_n) * (x_j - t_n)
          dL/db_n  = dL/dg_n * g_n(1-g_n)
        """
        B = X.shape[0]
        leaf_pos = {int(nd): i for i, nd in enumerate(self.leaf_ids)}
        n_int = len(self.internal)
        dmu = np.zeros(len(self.leaf_ids), dtype=np.float64)
        da = np.zeros(n_int, dtype=np.float64)
        db = np.zeros(n_int, dtype=np.float64)

        # 内部节点按「子节点在前」的顺序（后序），保证先算子树的 v
        order = self.postorder()
        for bi in range(B):
            x = X[bi]
            d = dy[bi]
            gate = {}
            v = {}
            prob = {0: 1.0}
            # ---- 自底向上算 v
            for node in order:
                if self.is_leaf[node]:
                    v[node] = mu[leaf_pos[int(node)]]
                    continue
                pos = self.internal_pos[int(node)]
                j = self.feature[node]
                thr = self.threshold[node]
                z = a[pos] * (x[j] - thr) + b[pos]
                g = 1.0 / (1.0 + np.exp(-z))
                gate[node] = g
                l = self.children_left[node]
                r = self.children_right[node]
                v[node] = g * v[l] + (1.0 - g) * v[r]
            # ---- 自顶向下算 prob
            for node in self.preorder():
                if node == 0:
                    continue
                p = self.parent_of[node]
                pos = self.internal_pos[int(p)]
                j = self.feature[p]
                thr = self.threshold[p]
                z = a[pos] * (x[j] - thr) + b[pos]
                gp = 1.0 / (1.0 + np.exp(-z))
                prob[node] = prob[p] * (gp if node == self.children_left[p]
                                        else (1.0 - gp))
            # ---- 累加梯度
            for node in order:
                if self.is_leaf[node]:
                    dmu[leaf_pos[int(node)]] += d * prob[node]
                    continue
                pos = self.internal_pos[int(node)]
                j = self.feature[node]
                thr = self.threshold[node]
                g = gate[node]
                gp = g * (1.0 - g)
                l = self.children_left[node]
                r = self.children_right[node]
                dL_dg = d * prob[node] * (v[l] - v[r])
                da[pos] += dL_dg * gp * (x[j] - thr)
                db[pos] += dL_dg * gp
        return {"a": da, "b": db, "mu": dmu}

    def postorder(self):
        if getattr(self, "_post", None) is not None:
            return self._post
        out = []
        stack = [(0, False)]
        while stack:
            node, expanded = stack.pop()
            if self.is_leaf[node]:
                out.append(int(node))
                continue
            if expanded:
                out.append(int(node))
                continue
            stack.append((node, True))
            stack.append((self.children_right[node], False))
            stack.append((self.children_left[node], False))
        self._post = out
        return out

    def preorder(self):
        if getattr(self, "_pre", None) is not None:
            return self._pre
        out = []
        stack = [0]
        while stack:
            node = stack.pop()
            out.append(int(node))
            if not self.is_leaf[node]:
                stack.append(self.children_left[node])
                stack.append(self.children_right[node])
        self._pre = out
        return out


# ============================================================ 对照
def rel(a, b):
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    denom = np.maximum(1e-12, np.abs(a) + np.abs(b))
    return np.abs(a - b) / denom


def main():
    rng = np.random.default_rng(2024)
    n, F = 60, 8
    X = rng.normal(0, 1, (n, F)).astype(np.float32)
    y = (0.4 * X[:, 0] - 0.3 * X[:, 1] + 0.05 * rng.normal(size=n)).astype(np.float32)

    from sklearn.tree import DecisionTreeRegressor
    dt = DecisionTreeRegressor(max_depth=4, min_samples_leaf=3, random_state=0).fit(X, y)
    tree_fit = {"X": X, "y": y, "n_pool": n}
    ex = D.SoftTreeExpert(tree_fit, F, rng)
    ex.forward(X)

    brute = BruteSoftTree(ex.dt)
    a = ex.params["a"].astype(np.float64)
    b = ex.params["b"].astype(np.float64)
    mu = ex.params["mu"].astype(np.float64)

    # ---- 1) forward 对照
    pred_model = ex.forward(X.astype(np.float64)).astype(np.float64)
    pred_brute = brute.forward(X.astype(np.float64), a, b, mu)
    fwd_diff = float(np.max(np.abs(pred_model - pred_brute)))

    # ---- 2) 梯度对照（建模 backward vs 暴力解析梯度）
    pred64 = pred_model
    dy = (2.0 / n) * (pred64 - y.astype(np.float64))
    g_model = ex.backward(X.astype(np.float64), pred64, dy)
    g_brute = brute.grads(X.astype(np.float64), a, b, mu, dy)

    res = {}
    for k in ("a", "b", "mu"):
        gm = np.asarray(g_model[k], dtype=np.float64).reshape(-1)
        gb = np.asarray(g_brute[k], dtype=np.float64).reshape(-1)
        r = rel(gb, gm)
        res[k] = (float(r.max()), int(r.argmax()))

    print("=" * 78)
    print("软树：独立全路径枚举暴力实现 vs 建模实现")
    print("=" * 78)
    print(f"  树结构：node_count={ex.n_nodes}  内部节点={len(ex.internal)}  叶={ex.n_leaves}")
    print(f"  forward  max|diff| = {fwd_diff:.3e}")
    print("  梯度（暴力解析梯度 vs 建模 backward）:")
    for k, (mx, idx) in res.items():
        print(f"    {k:>3}: max_rel = {mx:.3e}   @index {idx}")
    print()

    ok = fwd_diff < 1e-6 and all(v[0] < 5e-3 for v in res.values())
    if ok:
        print("RESULT: PASS（暴力实现与建模实现一致：forward ~1e-8，梯度 max_rel < 5e-3）")
        return 0
    print("RESULT: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
