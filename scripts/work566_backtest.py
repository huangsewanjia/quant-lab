# -*- coding: utf-8 -*-
"""WORK-566 · 组合回测引擎（论文附录 G 协议 + A 股约束）。

协议（预注册 §7）：
  top-30 等权 long-only；预测基于 t 日收盘、t+1 开盘买入；持有至 t+21 收盘卖出；
  重叠批次共享同一现金/净值账户；批次预算 B_t = min(V_t/H, max(C_t,0)/(1+c))；
  每单位成交名义成本 c（主口径 0.0005）；日终净值 V_d = C_d + Σ q·P_close；
  Net AR = 252×mean(R)、Net SR = √252×mean/sd、MDD 基于日收盘净值、TO_d = A_d/(2V_{d-1})。

成本（预注册 §7.1）：买入与卖出都收 c（含最终清算）；不同批次的反向成交不轧差。

A 股约束（预注册 §7.4）：涨停不可买（顺延补位）、跌停不可卖（顺延）、停牌不可成交（顺延）、
  停牌持仓按**估值日之前最后一个可得收盘价**盯市（不得使用未来 bar）、
  退市在最后 bar 之后的首个交易日按该 bar 收盘价清算并额外扣 1%。

断言（引擎级守卫，预注册 §7.4）：任何成交尝试落在「不可成交价」（当日无 bar / 越出涨跌停带 /
  价格非正）→ 抛 AssertionError 中止（不是静默跳过）。
  --assert-violation 开关会在首笔成交处故意把该笔原始成交价改写出涨跌停带，
  由**引擎自身的守卫**报错并打印原文（任务书要求）。

运行：
  python scripts/work566_backtest.py --arm full --seed 42 --cost 0.0005
  python scripts/work566_backtest.py --assert-violation
"""
import argparse
import json
import os
import sys
import time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "WORK-566")
OUT = os.path.join(REPO, "experiments", "WORK-566")

H_HOLD = 21
K = 30


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


class Panel:
    def __init__(self):
        self.close_hfq = np.load(os.path.join(DATA, "base", "close_hfq.npy"), mmap_mode="r")
        self.open_hfq = np.load(os.path.join(DATA, "base", "open_hfq.npy"), mmap_mode="r")
        self.close_raw = np.load(os.path.join(DATA, "base", "close_raw.npy"), mmap_mode="r")
        self.open_raw = np.load(os.path.join(DATA, "base", "open_raw.npy"), mmap_mode="r")
        self.up_limit = np.load(os.path.join(DATA, "base", "up_limit.npy"), mmap_mode="r")
        self.down_limit = np.load(os.path.join(DATA, "base", "down_limit.npy"), mmap_mode="r")
        self.has_bar = np.load(os.path.join(DATA, "base", "has_bar.npy"), mmap_mode="r")
        s = np.load(os.path.join(DATA, "samples.npz"))
        self.dts = s["dts"]
        self.m_test = s["m_test"]
        # 故意违规注入用的价格覆盖：(kind, stock, day) -> value（仅 --assert-violation 用）
        self.ov = {}

    def price(self, kind, s_idx, d_idx):
        """取价格字段（open_raw/close_raw/up_limit/down_limit）；违规注入优先取覆盖值。"""
        v = self.ov.get((kind, int(s_idx), int(d_idx)))
        if v is not None:
            return v
        return getattr(self, kind)[s_idx, d_idx]

    def last_bar(self, stock_idx, date_idx):
        """该股票在窗口内的最后一个有 bar 的日期下标（无则 -1）。

        **仅用于退市判定**（退市清算需要知道「最后一个有 bar 的日子」）；
        估值盯市必须用「估值日之前最后一个可得 bar」，两者不可混用
        （2026-11-25 独立代码审查 B1：混用会把未来价格带进净值）。
        """
        row = np.asarray(self.has_bar[stock_idx])
        nz = np.nonzero(row)[0]
        return int(nz[-1]) if len(nz) else -1


def tradable_at_open(panel, s_idx, d_idx):
    """t+1 开盘能否买入：无停牌、有 bar、不是涨停。"""
    if not bool(panel.has_bar[s_idx, d_idx]):
        return False, "suspended"
    o = float(panel.price("open_raw", s_idx, d_idx))
    if not np.isfinite(o) or o <= 0:
        return False, "no_price"
    ul = float(panel.price("up_limit", s_idx, d_idx))
    if np.isfinite(ul) and ul > 0 and o >= ul * (1 - 1e-6):
        return False, "limit_up"
    return True, "ok"


def tradable_at_close(panel, s_idx, d_idx):
    """收盘能否卖出：不是跌停。"""
    if not bool(panel.has_bar[s_idx, d_idx]):
        return False, "suspended"
    c = float(panel.price("close_raw", s_idx, d_idx))
    if not np.isfinite(c) or c <= 0:
        return False, "no_price"
    dl = float(panel.price("down_limit", s_idx, d_idx))
    if np.isfinite(dl) and dl > 0 and c <= dl * (1 + 1e-6):
        return False, "limit_down"
    return True, "ok"


def assert_fill_legal(panel, s_idx, d_idx, side):
    """引擎级守卫（预注册 §7.4）：成交必须落在「可成交价」上，否则报错中止。

    可成交价 = ① 当日有 bar（未停牌、未退市后无价）
              ② 当日原始成交价为正且有限
              ③ 原始成交价落在当日涨跌停带 [down_limit, up_limit] 内
    违反即抛 AssertionError（不是静默跳过、不是顺延）。
    """
    if not bool(panel.has_bar[s_idx, d_idx]):
        raise AssertionError(
            f"[ASSERT] 不可成交价：成交日无 bar（停牌/退市后无价） stock={s_idx} day={d_idx} side={side}")
    if side == "buy":
        raw = float(panel.price("open_raw", s_idx, d_idx))
        label = "开盘价"
    else:
        raw = float(panel.price("close_raw", s_idx, d_idx))
        label = "收盘价"
    if not np.isfinite(raw) or raw <= 0:
        raise AssertionError(
            f"[ASSERT] 不可成交价：{label} 非正或非有限 stock={s_idx} day={d_idx} side={side} raw={raw}")
    ul = float(panel.price("up_limit", s_idx, d_idx))
    dl = float(panel.price("down_limit", s_idx, d_idx))
    if np.isfinite(ul) and ul > 0 and raw > ul * (1 + 1e-6):
        raise AssertionError(
            f"[ASSERT] 不可成交价：{label} 超出涨停价 stock={s_idx} day={d_idx} side={side} "
            f"raw={raw} up_limit={ul}")
    if np.isfinite(dl) and dl > 0 and raw < dl * (1 - 1e-6):
        raise AssertionError(
            f"[ASSERT] 不可成交价：{label} 低于跌停价 stock={s_idx} day={d_idx} side={side} "
            f"raw={raw} down_limit={dl}")


def run_backtest(panel, pred_stock, pred_val, pred_date, dates_sorted, cost, assert_violation=False):
    """返回每日净值/收益/换手与汇总指标。

    pred_* 为逐样本的 (stock_idx, date_idx) 与预测值；dates_sorted 为测试期交易日下标（升序）。
    """
    n_days = len(dates_sorted)
    date_pos = {int(d): i for i, d in enumerate(dates_sorted)}
    # 每个预测日的候选（按预测值降序；并列时保留「预测文件中的资产顺序」——论文原话
    # "Ties are resolved by asset order in the prediction file"，用稳定排序实现）
    buckets = {}
    for k in range(len(pred_stock)):
        d = int(pred_date[k])
        if d not in date_pos:
            continue
        buckets.setdefault(d, []).append((float(pred_val[k]), int(pred_stock[k])))
    for d in buckets:
        buckets[d].sort(key=lambda x: -x[0])      # 稳定排序 → 并列保持文件顺序

    cash = 1.0
    nav = np.full(n_days, np.nan)
    ret = np.full(n_days, np.nan)
    turnover = np.full(n_days, np.nan)
    traded_notional = np.zeros(n_days)
    n_batches = 0
    n_skipped_limitup = 0
    n_skipped_suspend = 0
    deferred_exits = 0
    delist_liquidations = 0
    batches = []            # {entry_day, exit_day, pos:{stock: units}}
    pending_entry = []      # (day, batch)
    last_bar_cache = {}
    injected = False

    # 估值盯市用「估值日（含）之前最后一个有 bar 的日子」（预注册 §7.4「按最后可得收盘价」）
    # —— 不得使用未来 bar（2026-11-25 独立代码审查 B1）。
    td = np.asarray(dates_sorted, dtype=np.int64)
    hb = np.asarray(panel.has_bar[:, td])                      # (S, n_days)
    bar_idx = np.where(hb, td[None, :], -1)                    # 该格点当天有 bar → 当天下标
    last_before = np.maximum.accumulate(bar_idx, axis=1)       # (S, n_days) 之前最后有 bar 的下标

    def last_bar_of(s):
        if s not in last_bar_cache:
            last_bar_cache[s] = panel.last_bar(s, 0)
        return last_bar_cache[s]

    cand = []
    for i, d in enumerate(dates_sorted):
        day = int(d)
        # ---------------- 1. 开盘：执行挂起的买入
        still_pending = []
        for (pd, b) in pending_entry:
            if pd != day:
                still_pending.append((pd, b))
                continue
            # 逐只执行：不可成交的（被顺延剔除）不再建仓
            bought = {}
            for s, units_plan in b["plan"].items():
                ok, why = tradable_at_open(panel, s, day)
                if not ok:
                    if why == "limit_up":
                        n_skipped_limitup += 1
                    elif why == "suspended":
                        n_skipped_suspend += 1
                    continue
                if assert_violation and not injected:
                    # 故意违规：把该笔成交的**原始成交价**改写出涨跌停带 →
                    # 由引擎自身的 assert_fill_legal 守卫报错中止（不是注入块自己 raise）
                    ul = float(panel.price("up_limit", s, day))
                    if np.isfinite(ul) and ul > 0:
                        injected = True
                        panel.ov[("open_raw", s, day)] = ul * 1.5
                        log(f"[ASSERT-TEST] 注入违规：stock={s} day={day} 原始开盘价改写为涨停价 1.5 倍")
                # 引擎级守卫：成交价必须可成交，否则报错中止（预注册 §7.4 断言）
                assert_fill_legal(panel, s, day, "buy")
                p = float(panel.open_hfq[s, day])
                if not np.isfinite(p) or p <= 0:
                    raise AssertionError(f"[ASSERT] 不可成交价：开盘价非正 stock={s} day={day}")
                bought[s] = units_plan
            if bought:
                # 按实际买入金额等比例缩放（预算 B 覆盖买入额与成本）
                notional = 0.0
                for s, up in bought.items():
                    p = float(panel.open_hfq[s, day])
                    notional += up * p
                scale = 1.0
                if notional > 0:
                    budget = b["budget"]
                    max_notional = budget / (1.0 + cost)
                    if notional > max_notional and max_notional > 0:
                        scale = max_notional / notional
                cost_amt = 0.0
                for s, up in bought.items():
                    p = float(panel.open_hfq[s, day])
                    u = up * scale
                    cash -= u * p
                    cost_amt += abs(u) * p
                    b["pos"][s] = b["pos"].get(s, 0.0) + u
                cash -= cost_amt * cost
                traded_notional[i] += cost_amt
            b["entry_day"] = day
            batches.append(b)
        pending_entry = still_pending

        # ---------------- 2. 收盘：执行到期卖出（不可成交则顺延）
        for b in list(batches):
            if b["exit_day"] is None or day < b["exit_day"]:
                continue
            # 退市检查：该股最后一个 bar 已过 → 清算
            for s in list(b["pos"].keys()):
                lb = last_bar_of(s)
                if lb >= 0 and day > lb:
                    p = float(panel.close_hfq[s, lb])
                    u = b["pos"][s]
                    cash += u * p * 0.99        # 退市清算折价 1%
                    fee = abs(u) * p
                    cash -= fee * cost          # 最终清算同样计成本（预注册 §7.1「含最终清算」）
                    traded_notional[i] += fee
                    del b["pos"][s]
                    delist_liquidations += 1
                    continue
                ok, why = tradable_at_close(panel, s, day)
                if not ok:
                    deferred_exits += 1
                    continue
                assert_fill_legal(panel, s, day, "sell")
                p = float(panel.close_hfq[s, day])
                u = b["pos"].pop(s)
                cash += u * p
                fee = abs(u) * p
                cash -= fee * cost              # 卖出端成本（预注册 §7.1「买入与卖出都收」）
                traded_notional[i] += fee
            if not b["pos"]:
                # 按身份删除（不能用 list.remove：两个批次字典可能相等）
                for _bi in range(len(batches)):
                    if batches[_bi] is b:
                        batches.pop(_bi)
                        break
                b["exit_day"] = None

        # ---------------- 3. 估值
        v = cash
        for b in batches:
            for s, u in b["pos"].items():
                p = float(panel.close_hfq[s, day])
                if not np.isfinite(p) or p <= 0:
                    # 停牌/无当日价 → 用「估值日之前最后一个可得 bar」的收盘价盯市
                    # （预注册 §7.4；不得用全局最后一个 bar——那是未来价格，见审查 B1）
                    lb_past = int(last_before[s, i])
                    if lb_past < 0:
                        raise AssertionError(
                            f"[ASSERT] 无可得估值价：stock={s} day={day} 之前没有任何有 bar 的交易日")
                    p = float(panel.close_hfq[s, lb_past])
                v += u * p
        nav[i] = v
        # 终止条件（预注册 §7.1）：任一估值 NAV ≤ 0 → 终止并报账户失败
        if not np.isfinite(v) or v <= 0:
            raise AssertionError(
                f"[ASSERT] 账户失败：NAV ≤ 0 day={day} nav={v} cash={cash}")
        ret[i] = v / nav[i - 1] - 1 if i > 0 and np.isfinite(nav[i - 1]) else np.nan
        turnover[i] = traded_notional[i] / (2 * nav[i - 1]) if i > 0 and np.isfinite(nav[i - 1]) else np.nan

        # ---------------- 4. 用 t 日预测决定 t+1 的新批次
        cand = buckets.get(day, [])
        if cand and i + 1 < n_days:
            picks = []
            for _, s in cand:
                if len(picks) >= K:
                    break
                ok, why = tradable_at_open(panel, s, int(dates_sorted[i + 1]))
                if not ok:
                    if why == "limit_up":
                        n_skipped_limitup += 1
                    elif why == "suspended":
                        n_skipped_suspend += 1
                    continue
                picks.append(s)
            if len(picks) >= K and i + H_HOLD < n_days:
                budget = min(v / H_HOLD, max(cash, 0.0) / (1.0 + cost))
                if budget > 0:
                    plan = {}
                    for s in picks:
                        p = float(panel.open_hfq[s, int(dates_sorted[i + 1])])
                        plan[s] = budget * (1.0 / K) / p
                    # 论文：批次维持固定持仓直到 t+H 收盘（H=21）→ 退出日 = 位置 i+H 的日历下标
                    b = {"plan": plan, "pos": {}, "budget": budget,
                         "entry_day": None, "exit_day": int(dates_sorted[i + H_HOLD])}
                    pending_entry.append((int(dates_sorted[i + 1]), b))
                    n_batches += 1

    # ---------------- 强制违规注入（任务书：证明断言真的在跑）
    if assert_violation and not injected:
        # 全程没有成交笔（无可注入处）→ 对首个候选股用**引擎自身的守卫**做一次等价验证
        s = int(cand[0][1]) if cand else 0
        d = int(dates_sorted[min(1, n_days - 1)])
        ul = float(panel.price("up_limit", s, d))
        if np.isfinite(ul) and ul > 0:
            panel.ov[("open_raw", s, d)] = ul * 1.5
            log(f"[ASSERT-TEST] 注入违规：stock={s} day={d} 原始开盘价改写为涨停价 1.5 倍")
            assert_fill_legal(panel, s, d, "buy")

    return {
        "nav": nav, "ret": ret, "turnover": turnover,
        "n_batches": n_batches, "n_skipped_limitup": n_skipped_limitup,
        "n_skipped_suspend": n_skipped_suspend, "deferred_exits": deferred_exits,
        "delist_liquidations": delist_liquidations,
        "n_days": n_days,
    }


def summarize(res, cost):
    r = res["ret"]
    r = r[np.isfinite(r)]
    nav = res["nav"]
    nav = nav[np.isfinite(nav)]
    if len(r) == 0:
        return {}
    net_ar = 252.0 * float(np.mean(r))
    sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    net_sr = float(np.sqrt(252.0) * np.mean(r) / sd) if sd > 0 else float("nan")
    peak = np.maximum.accumulate(nav)
    mdd = float(np.min(nav / peak - 1.0))
    to = res["turnover"][np.isfinite(res["turnover"])]
    return {
        "n_days": int(len(r)),
        "net_ar": net_ar, "net_sr": net_sr, "mdd": mdd,
        "avg_turnover": float(np.mean(to)) if len(to) else None,
        "total_return": float(nav[-1] / nav[0] - 1.0) if len(nav) > 1 else None,
        "n_batches": res["n_batches"],
        "n_skipped_limitup": res["n_skipped_limitup"],
        "n_skipped_suspend": res["n_skipped_suspend"],
        "deferred_exits": res["deferred_exits"],
        "delist_liquidations": res["delist_liquidations"],
        "cost_bps_per_side": cost * 1e4,
    }


def rank_ic(pred, y):
    """逐样本配对的 RankIC 需要按日期分组，这里给单日版本。"""
    def rank(a):
        order = np.argsort(a, kind="stable")
        ranks = np.empty(len(a), dtype=np.float64)
        ranks[order] = np.arange(len(a), dtype=np.float64)
        # 平均秩（并列）
        sa = a[order]
        i = 0
        while i < len(sa):
            j = i
            while j + 1 < len(sa) and sa[j + 1] == sa[i]:
                j += 1
            ranks[order[i:j + 1]] = (i + j) / 2.0
            i = j + 1
        return ranks
    if len(pred) < 3:
        return None
    rp, ry = rank(pred), rank(y)
    if np.std(rp) == 0 or np.std(ry) == 0:
        return None
    return float(np.corrcoef(rp, ry)[0, 1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="full")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cost", type=float, default=0.0005)
    ap.add_argument("--assert-violation", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    panel = Panel()
    z = np.load(os.path.join(DATA, f"preds_seed{args.seed}_{args.arm}.npz"))
    pred = z["pred_test"]
    idx = z["idx_test"]
    s = np.load(os.path.join(DATA, "samples.npz"))
    stock, date = s["stock_idx"], s["date_idx"]
    ps, pd_, pv = stock[idx], date[idx], pred
    dates_sorted = np.unique(pd_)
    log(f"{args.arm} seed{args.seed}: 测试样本 {len(ps):,}，预测日 {len(dates_sorted)} 天")

    res = run_backtest(panel, ps, pv, pd_, dates_sorted, args.cost, args.assert_violation)
    summ = summarize(res, args.cost)
    summ.update({"arm": args.arm, "seed": args.seed, "cost": args.cost})

    # 逐日 RankIC
    ic = []
    for d in np.unique(pd_):
        m = pd_ == d
        v = rank_ic(pv[m], np.asarray(z["y_test"][m]))
        if v is not None:
            ic.append((int(d), v))
    if ic:
        arr = np.asarray([x[1] for x in ic])
        summ["rankic_mean_pct"] = float(arr.mean() * 100)
        summ["rankic_std_pct"] = float(arr.std(ddof=1) * 100) if len(arr) > 1 else None
        summ["rankicir"] = float(arr.mean() / arr.std(ddof=1) * np.sqrt(252)) if len(arr) > 1 and arr.std(ddof=1) > 0 else None
        summ["n_ic_days"] = int(len(arr))

    outp = args.out or os.path.join(OUT, f"backtest_{args.arm}_seed{args.seed}_c{args.cost}.json")
    np.savez_compressed(outp.replace(".json", "_series.npz"), nav=res["nav"], ret=res["ret"],
                        turnover=res["turnover"], ic_dates=np.asarray([x[0] for x in ic]),
                        ic_vals=np.asarray([x[1] for x in ic]))
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(summ, f, ensure_ascii=False, indent=2)
    print(json.dumps(summ, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
