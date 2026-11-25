# -*- coding: utf-8 -*-
"""WORK-566 · 样本构造 + S1 先验（块内扩张窗口岭回归）。

产出（data/WORK-566/）：
  samples.npz      全量合法样本（stock_idx, date_idx）+ 各切分掩码
  prior.json       各预测块的岭回归系数、标准化矩、残差尺度、目标矩
  norm.json        40 维描述符的全局标准化统计（仅用训练切分估计）

样本合法性（预注册 §6）：
  连续段 [t-125, t+21] 共 147 个交易日**每天都要有真实 bar**（停牌缺口不算连续）。
  这条写成断言：断言统计的格数写进产出，供数据质量报告引用。

S1（预注册 §10.1）：
  块 b 的拟合池 = 「完整 21 日结果窗结束于块开始之前」的样本；
  特征用块内历史矩标准化（非有限→0，裁剪 [−8,8]）；多输出岭回归 α=20，截距不惩罚；
  拟合池 >3,000,000 时按样本 ID 确定性抽稀。

运行：python scripts/work566_prior.py
"""
import json
import os
import sys
import time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "WORK-566")
OUT = os.path.join(REPO, "experiments", "WORK-566")
os.makedirs(OUT, exist_ok=True)

CTX = 126          # 上下文长度（交易日）
H = 21             # 目标窗长度（交易日）
ALPHA = 20.0       # 岭回归 α（论文附录 B）
FIT_POOL_MAX = 3_000_000   # 论文：超过 3 百万观测则确定性抽稀

# 预测块：(块名, 起始日, 结束日)；块起点之前的完整历史为拟合池
BLOCKS = [
    ("b2016", 20160101, 20161231),
    ("b2017", 20170101, 20171231),
    ("b2018", 20180101, 20181231),
    ("b2019", 20190101, 20191231),
    ("b2020", 20200101, 20201231),
    ("b2021", 20210101, 20211231),
    ("bvalid", 20220101, 20231231),
    ("btest", 20240102, 20260626),
]
TRAIN_LO, TRAIN_HI = 20160101, 20211231
FIT_LO, FIT_HI = 20140101, 20211231      # 论文的「prepared training split」= 2014-2021
VALID_LO, VALID_HI = 20220101, 20231231
TEST_LO, TEST_HI = 20240102, 20260626


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main():
    t0 = time.time()
    meta = json.load(open(os.path.join(DATA, "meta.json"), encoding="utf-8"))
    dates = meta["date_list"] if "date_list" in meta else None
    # 日期列表从 meta 重建：build_panel 未存日期数组，这里从日历重取
    import duckdb
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    cal = con.execute(
        "select distinct cast(strftime(cal_date, '%Y%m%d') as int) as cd from "
        "read_parquet('/srv/share/quantdb/raw/trade_cal/**/*.parquet', hive_partitioning=true, "
        "union_by_name=true) where exchange='SSE' and is_open=1 "
        f"and cal_date between date '2013-01-01' and date '2026-06-26' order by cd"
    ).df()["cd"].to_numpy(dtype=np.int64)
    n_dates = len(cal)
    date_index = {int(d): i for i, d in enumerate(cal)}

    n_stocks = meta["shape"]["stocks"]
    log(f"日历 {n_dates} 天，股票 {n_stocks} 只")

    has_bar = np.load(os.path.join(DATA, "base", "has_bar.npy"), mmap_mode="r")
    feat = np.load(os.path.join(DATA, "feat.npy"), mmap_mode="r")
    tgt = np.load(os.path.join(DATA, "targets.npy"), mmap_mode="r")

    # ---------------------------------------------------------------- 连续性断言
    log("连续性断言：窗口 [t-125, t+21] 每天都要有 bar …")
    csum = np.zeros((n_stocks, n_dates + 1), dtype=np.int32)
    acc = np.zeros((n_stocks, n_dates), dtype=np.int32)
    CH = 500
    for s0 in range(0, n_stocks, CH):
        s1 = min(s0 + CH, n_stocks)
        acc[s0:s1] = np.asarray(has_bar[s0:s1]).view(np.int8).astype(np.int32)
    csum[:, 1:] = np.cumsum(acc, axis=1)
    span = CTX + H          # 147
    # 窗口 [t-125, t+21] 的 bar 数 = csum[t+22] - csum[t-125]
    idx = np.arange(n_dates)
    lo_i = np.clip(idx - (CTX - 1), 0, n_dates)
    hi_i = np.clip(idx + H + 1, 0, n_dates)     # csum 的右端点（含 t+21）
    cnt = csum[:, hi_i] - csum[:, lo_i]
    valid = (cnt == span)
    # 左边界：t-125 必须落在窗口内（不能靠窗口左端补 0）
    valid[:] &= (idx[None, :] >= (CTX - 1))
    del csum, acc
    log(f"连续样本格数 {valid.sum():,} / 总格数 {valid.size:,} "
        f"({valid.sum() / valid.size:.3%})；被连续性剔除 {(~valid).sum():,}")

    stock_idx, date_idx = np.nonzero(valid)
    del valid
    dts = cal[date_idx]
    log(f"合法样本 {len(stock_idx):,} 条")

    # 标签/目标必须有限（连续性保证，但保险起见再筛一次）
    y = np.asarray(tgt[:, :, 0])  # y21 —— 只为筛选时取有限值
    fin = np.isfinite(y[stock_idx, date_idx])
    stock_idx, date_idx = stock_idx[fin], date_idx[fin]
    del y
    log(f"标签有限后样本 {len(stock_idx):,} 条")

    def sel(lo, hi):
        m = (dts >= lo) & (dts <= hi)
        return m

    m_train, m_valid, m_test = sel(TRAIN_LO, TRAIN_HI), sel(VALID_LO, VALID_HI), sel(TEST_LO, TEST_HI)
    m_fit = sel(FIT_LO, FIT_HI)

    # 论文 Table 3：'Samples whose target windows cross split boundaries are excluded.'
    # → 样本 t 的 21 日结果窗必须整落在本切分内（t + 21 < 下一切分首日）。
    def purge(m, next_lo):
        b = int(np.searchsorted(cal, next_lo))
        keep = (date_idx + H) < b
        dropped = int((m & ~keep).sum())
        log(f"  边界清洗：目标窗跨入 {next_lo} 之后切分的样本剔除 {dropped:,} 条")
        return m & keep

    n_raw = (m_train.sum(), m_valid.sum(), m_test.sum())
    m_train = purge(m_train, VALID_LO)
    m_valid = purge(m_valid, TEST_LO)
    log(f"train {m_train.sum():,} / valid {m_valid.sum():,} / test {m_test.sum():,} / fit14_21 {m_fit.sum():,}"
        f"（清洗前 {n_raw[0]:,} / {n_raw[1]:,} / {n_raw[2]:,}）")

    # ---------------------------------------------------------------- 全局标准化（仅训练切分）
    log("计算 40 维全局标准化统计（仅用训练切分 2014-2021）…")
    n_feat = feat.shape[2]
    s1 = np.zeros(n_feat, dtype=np.float64)
    s2 = np.zeros(n_feat, dtype=np.float64)
    cnt_f = np.zeros(n_feat, dtype=np.int64)
    fit_idx = np.nonzero(m_fit)[0]
    step = max(1, len(fit_idx) // 20000)
    sub = fit_idx[::step]
    for k in range(0, len(sub), 20000):
        chunk = sub[k:k + 20000]
        blk = np.asarray(feat[stock_idx[chunk], date_idx[chunk], :], dtype=np.float64)
        ok = np.isfinite(blk)
        s1 += np.where(ok, blk, 0.0).sum(axis=0)
        s2 += np.where(ok, blk ** 2, 0.0).sum(axis=0)
        cnt_f += ok.sum(axis=0)
    mean = s1 / np.maximum(cnt_f, 1)
    var = np.maximum(s2 / np.maximum(cnt_f, 1) - mean ** 2, 0.0)
    std = np.sqrt(var)
    std[std < 1e-8] = 1.0
    log(f"标准化统计完成（每维有效样本 {cnt_f.min():,} ~ {cnt_f.max():,}）")

    # ---------------------------------------------------------------- 各块先验
    log("按块拟合 S1 先验（扩张窗口岭回归，α=20）…")
    y_idx = {n: i for i, n in enumerate(meta["target_names"])}

    def gather(idx):
        """取样本的特征 x(40) 与目标 y(8)。"""
        X = np.asarray(feat[stock_idx[idx], date_idx[idx], :], dtype=np.float32)
        Y = np.asarray(tgt[stock_idx[idx], date_idx[idx], :], dtype=np.float32)[:, 1:9]
        return X, Y

    # 目标矩 m^tr / d^tr：训练切分（2014-2021）全体样本
    tr_sub = fit_idx[::max(1, len(fit_idx) // 200000)]
    y_tr = []
    for k in range(0, len(tr_sub), 20000):
        chunk = tr_sub[k:k + 20000]
        y_tr.append(np.asarray(tgt[stock_idx[chunk], date_idx[chunk], 1:9], dtype=np.float64))
    y_tr = np.concatenate(y_tr, axis=0)
    m_tr = np.nanmean(y_tr, axis=0)
    d_tr = np.nanstd(y_tr, axis=0)
    d_tr[d_tr < 1e-6] = 1e-6
    del y_tr

    blocks_out = {}
    for name, blo, bhi in BLOCKS:
        # 块起点用「不早于 blo 的第一个交易日」定位（1 月 1 日等非交易日须顺延）
        bstart = int(np.searchsorted(cal, blo))
        if bstart >= n_dates or cal[bstart] > bhi:
            log(f"  块 {name}: 起点 {blo} 无对应交易日，跳过")
            continue
        # 拟合池：结果窗完整落在块开始之前的样本（t + 21 < 块起点索引），
        # 且样本日不早于 FIT_LO（论文的先验初装切分 = 2014-2021「prepared training split」，
        # 避免把 2013 年的额外历史混进先验池）
        pool = np.nonzero((date_idx + H < bstart) & (dts >= FIT_LO))[0]
        # 只保留「目标窗完整」（连续性已保证）
        if len(pool) == 0:
            blocks_out[name] = {"ok": False, "error": f"拟合池为空（块起点 {blo}）"}
            log(f"  块 {name}: 拟合池为空")
            continue
        thinned = False
        if len(pool) > FIT_POOL_MAX:
            stride = int(np.ceil(len(pool) / FIT_POOL_MAX))
            pool = pool[::stride]
            thinned = True
        # 拟合池特征/目标一次性读入内存（池 ≤3M × 40 float32 ≈ 480MB），
        # 后续三趟（矩 / XtX / 残差）都在缓存上做，面板磁盘只读一遍。
        n_pool = len(pool)
        Xc = np.empty((n_pool, n_feat), dtype=np.float32)
        Yc_all = np.empty((n_pool, 8), dtype=np.float32)
        for k in range(0, n_pool, 20000):
            chunk = pool[k:k + 20000]
            xb, yb = gather(chunk)
            Xc[k:k + len(chunk)] = xb
            Yc_all[k:k + len(chunk)] = yb
        del pool

        # 标准化矩（块内拟合池）
        blk64 = Xc.astype(np.float64)
        ok = np.isfinite(blk64)
        sx = np.where(ok, blk64, 0.0).sum(axis=0)
        sxx = np.where(ok, blk64 ** 2, 0.0).sum(axis=0)
        n_ok = ok.sum(axis=0)
        del ok
        fmean = sx / np.maximum(n_ok, 1)
        fvar = np.maximum(sxx / np.maximum(n_ok, 1) - fmean ** 2, 0.0)
        fstd = np.sqrt(fvar)
        fstd[fstd < 1e-8] = 1.0
        del sx, sxx

        # 标准化 + 裁剪 [−8,8]（内存内，存 float32 以省内存）
        Xs = ((blk64 - fmean) / fstd).astype(np.float32)
        Xs[~np.isfinite(Xs)] = 0.0
        np.clip(Xs, -8, 8, out=Xs)
        del blk64, Xc

        # 累加 X'X, X'Y（标准化后）
        XtX = np.zeros((n_feat, n_feat), dtype=np.float64)
        XtY = np.zeros((n_feat, 8), dtype=np.float64)
        Ysum = np.zeros(8, dtype=np.float64)
        Xsum = np.zeros(n_feat, dtype=np.float64)
        n_used = 0
        Yfin = np.isfinite(Yc_all).all(axis=1)
        if not Yfin.all():
            Xs = Xs[Yfin]
            Yc_all = Yc_all[Yfin]
        for k in range(0, len(Xs), 50000):
            xb = Xs[k:k + 50000].astype(np.float64)
            yb = Yc_all[k:k + 50000].astype(np.float64)
            XtX += xb.T @ xb
            XtY += xb.T @ yb
            Ysum += yb.sum(axis=0)
            Xsum += xb.sum(axis=0)
            n_used += len(xb)
        if n_used == 0:
            blocks_out[name] = {"ok": False, "error": "拟合池中无有效样本"}
            del Xs, Yc_all
            continue
        # 联立求解「截距不惩罚」的岭回归：
        #   (XtX + aI) W + Xsum c = XtY
        #   Xsum^T W + n c = Ysum
        # 特征已按块内矩标准化（列和≈0），此式给出与「先解 W 再补截距」完全一致的闭式解。
        A = XtX + ALPHA * np.eye(n_feat)
        Ainv_XtY = np.linalg.solve(A, XtY)
        Ainv_Xsum = np.linalg.solve(A, Xsum)
        denom = n_used - float(Xsum @ Ainv_Xsum)
        if abs(denom) < 1e-8:
            denom = 1e-8
        c = (Ysum - Xsum @ Ainv_XtY) / denom
        W = Ainv_XtY - np.outer(Ainv_Xsum, c)
        # 残差尺度（第三趟：此时 W 已定，仍在内存缓存上做）
        resid_sum = np.zeros(8, dtype=np.float64)
        resid_sq = np.zeros(8, dtype=np.float64)
        for k in range(0, len(Xs), 50000):
            xb = Xs[k:k + 50000].astype(np.float64)
            yb = Yc_all[k:k + 50000].astype(np.float64)
            pred = c[None, :] + xb @ W
            e = yb - pred
            resid_sum += e.sum(axis=0)
            resid_sq += (e ** 2).sum(axis=0)
        del Xs, Yc_all
        ebar = resid_sum / n_used
        sres = np.sqrt(np.maximum(resid_sq / n_used - ebar ** 2, 1e-8))
        sig_res = np.log(np.maximum(sres / d_tr, 1e-4))
        blocks_out[name] = {
            "ok": True, "block_start": blo, "block_end": bhi, "n_fit": int(n_used),
            "thinned": thinned, "alpha": ALPHA,
            "feat_mean": fmean.tolist(), "feat_std": fstd.tolist(),
            "W": W.tolist(), "c": c.tolist(),
            "s_res": sres.tolist(), "sigma_res": sig_res.tolist(),
            "target_mean": m_tr.tolist(), "target_std": d_tr.tolist(),
        }
        log(f"  块 {name}: 拟合样本 {n_used:,}（{'抽稀' if thinned else '全量'}）"
            f" R²≈{1 - (sres ** 2).mean() / max((d_tr ** 2).mean(), 1e-12):.3f}")

    prior = {
        "experiment": "WORK-566",
        "alpha": ALPHA,
        "ctx": CTX, "h": H,
        "fit_pool_max": FIT_POOL_MAX,
        "target_order": ["R5", "V5", "M5", "E5", "R21", "V21", "M21", "E21"],
        "blocks": blocks_out,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with open(os.path.join(DATA, "prior.json"), "w", encoding="utf-8") as f:
        json.dump(prior, f, ensure_ascii=False, indent=2)

    norm = {"mean": mean.tolist(), "std": std.tolist(), "n_feat": int(n_feat),
            "fit_samples": int(len(fit_idx)), "source": "训练切分 2014-2021 样本"}
    with open(os.path.join(DATA, "norm.json"), "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)

    np.savez_compressed(
        os.path.join(DATA, "samples.npz"),
        stock_idx=stock_idx.astype(np.int32), date_idx=date_idx.astype(np.int32),
        dts=dts.astype(np.int32),
        m_train=m_train, m_valid=m_valid, m_test=m_test, m_fit=m_fit,
    )
    summary = {
        "n_samples_total": int(len(stock_idx)),
        "n_train": int(m_train.sum()), "n_valid": int(m_valid.sum()), "n_test": int(m_test.sum()),
        "n_fit_2014_2021": int(m_fit.sum()),
        "continuity_cells_total": int(n_stocks * n_dates),
        "continuity_cells_valid": int(len(stock_idx)),
        "blocks": {k: {"ok": v.get("ok"), "n_fit": v.get("n_fit"), "thinned": v.get("thinned")}
                   for k, v in blocks_out.items()},
        "elapsed_s": round(time.time() - t0, 1),
    }
    with open(os.path.join(OUT, "sample_stats.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log(f"完成，用时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
