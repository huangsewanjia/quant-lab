#!/usr/bin/env python3
"""WORK-637 / phase 2b — render the 20-day K-line image library.

Renders every ``chartable`` (stock, week) into a 140x140 RGB PNG under
``imagelib/seed<seed>/<year>/<YYYYMMDD>_<ts_code>.png`` and writes the index
parquet + the manifest (per-year file counts, manifest-level sha256).

Chart spec is preregistration §3.1: qfq OHLC normalised to first close = 1,
MA20 line, volume scaled to the window max, A-share red-up/green-down, no
axes/ticks/text, y-padding = PAD_BY_SEED[seed].

    ../.venv/bin/python -u scripts/work637_render.py --seed 42 [--jobs 8]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work637_common as C

_G = {}


def _worker_init(panel, pad, seed, force=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.dpi": 100, "savefig.dpi": 100, "path.simplify": False,
        "agg.path.chunksize": 0, "figure.max_open_warning": 0,
    })
    fig = plt.figure(figsize=(C.IMG_PX / 100, C.IMG_PX / 100), dpi=100)
    fig.patch.set_facecolor("#ffffff")
    ax = fig.add_axes([0.0, 0.28, 1.0, 0.72])
    axv = fig.add_axes([0.0, 0.0, 1.0, 0.20])
    _G.update(panel=panel, pad=pad, seed=seed, fig=fig, ax=ax, axv=axv, plt=plt, force=force)


def _render(task):
    w, j, path = task
    if not _G.get("force") and os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    p, ax, axv, plt = _G["panel"], _G["ax"], _G["axv"], _G["plt"]
    i = p["week_idx"][w]
    s = slice(i - C.MA_WIN - C.LOOKBACK + 2, i + 1)
    o = p["o"][s, j] / p["c"][i - C.LOOKBACK + 1, j]
    h = p["h"][s, j] / p["c"][i - C.LOOKBACK + 1, j]
    lo = p["lo"][s, j] / p["c"][i - C.LOOKBACK + 1, j]
    c = p["c"][s, j] / p["c"][i - C.LOOKBACK + 1, j]
    ma = p["ma"][s, j] / p["c"][i - C.LOOKBACK + 1, j]
    v = p["v"][s, j]
    o, h, lo, c, ma, v = o[-C.LOOKBACK:], h[-C.LOOKBACK:], lo[-C.LOOKBACK:], c[-C.LOOKBACK:], ma[-C.LOOKBACK:], v[-C.LOOKBACK:]
    if not (np.isfinite(o).all() and np.isfinite(h).all() and np.isfinite(lo).all()
            and np.isfinite(c).all() and np.isfinite(ma).all() and np.isfinite(v).all()):
        raise RuntimeError(f"incomplete window at week {w} stock {p['codes'][j]}")

    x = np.arange(C.LOOKBACK, dtype=float)
    up = c >= o
    colors = np.where(up, C.UP_COLOR, C.DOWN_COLOR)
    ax.cla(); axv.cla()
    ax.vlines(x, lo, h, color=colors, lw=0.6)
    body_lo = np.minimum(o, c)
    body_h = np.abs(c - o)
    body_h = np.maximum(body_h, 1e-4)
    ax.bar(x, body_h, bottom=body_lo, width=0.62, color=colors, linewidth=0)
    ax.plot(x, ma, color=C.MA_COLOR, lw=0.7)
    axv.bar(x, v / v.max(), width=0.62, color=colors, linewidth=0)
    span = h.max() - lo.min()
    pad = _G["pad"] * span
    ax.set_xlim(-0.6, C.LOOKBACK - 0.4)
    ax.set_ylim(lo.min() - pad, h.max() + pad)
    axv.set_xlim(-0.6, C.LOOKBACK - 0.4)
    axv.set_ylim(0, 1.0)
    for a in (ax, axv):
        a.set_xticks([]); a.set_yticks([])
        for sp in a.spines.values():
            sp.set_visible(False)
        a.set_facecolor("#ffffff")
    fig = _G["fig"]
    fig.canvas.draw()
    buf = np.asarray(fig.canvas.buffer_rgba())[:, :, :3]
    from PIL import Image
    Image.fromarray(buf).save(path, format="PNG", compress_level=1)
    return path


def build_tasks(seed: int):
    panel = C.load_panel_cached()
    elig = C.eligibility(panel)
    chartable = elig["chartable"]
    weeks = pd.to_datetime(panel["days"][panel["week_idx"]])
    root = C.imagelib_dir(seed)
    tasks, rows = [], []
    nid = 0
    for w in range(chartable.shape[0]):
        d = weeks[w]
        ydir = os.path.join(root, f"{d.year}")
        for j in np.nonzero(chartable[w])[0]:
            code = panel["codes"][j]
            fn = f"{d.strftime('%Y%m%d')}_{code}.png"
            tasks.append((w, j, os.path.join(ydir, fn)))
            rows.append((nid, code, d.strftime("%Y-%m-%d"), ydir, fn))
            nid += 1
    idx = pd.DataFrame(rows, columns=["id", "ts_code", "date", "dir", "file"])
    return panel, idx, tasks


def manifest(root: str) -> dict:
    per_year, files = {}, []
    total_bytes = 0
    for yd in sorted(os.listdir(root)):
        yp = os.path.join(root, yd)
        if not os.path.isdir(yp):
            continue
        names = sorted(os.listdir(yp))
        per_year[yd] = len(names)
        for n in names:
            fp = os.path.join(yp, n)
            total_bytes += os.path.getsize(fp)
        files.extend(os.path.join(yd, n) for n in names)
    h = C.sha256_bytes("\n".join(files).encode("utf-8"))
    return {"dir": root, "file_count": len(files), "bytes": total_bytes,
            "per_year": per_year, "manifest_sha256": h,
            "manifest_sha256_note": "sha256 of the sorted, newline-joined relative path list",
            "rebuild": "…/.venv/bin/python -u scripts/work637_render.py --seed <seed>"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True, choices=list(C.SEEDS))
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--force", action="store_true",
                    help="re-render even when the PNG already exists (default: resume)")
    args = ap.parse_args()
    root = C.imagelib_dir(args.seed)
    os.makedirs(root, exist_ok=True)
    panel, idx, tasks = build_tasks(args.seed)
    for yd in sorted({d[:4] for d in idx["date"]}):
        os.makedirs(os.path.join(root, yd), exist_ok=True)
    os.makedirs(C.DATA, exist_ok=True)
    idx["seed"] = args.seed
    idx.to_parquet(os.path.join(C.DATA, f"index_seed{args.seed}.parquet"), index=False)
    print(f"seed {args.seed}: {len(tasks)} charts, {len(idx['dir'].unique())} dirs")
    t0 = time.time()
    pad = C.PAD_BY_SEED[args.seed]
    with Pool(args.jobs, initializer=_worker_init, initargs=(panel, pad, args.seed, args.force)) as pool:
        done = 0
        for _ in pool.imap_unordered(_render, tasks, chunksize=400):
            done += 1
            if done % 20000 == 0:
                el = time.time() - t0
                print(f"  {done}/{len(tasks)} {el:.0f}s ({done/el:.0f}/s)", flush=True)
    el = time.time() - t0
    m = manifest(root)
    alts = [d for d in (root, C.IMAGELIB_ROOT,
                        "/srv/share/quantdb/code/WORK-637/imagelib/seed%d" % args.seed)
            if os.path.isdir(d)]
    m["mirror_dirs"] = [d for d in alts if os.path.abspath(d) != os.path.abspath(root)]
    m["mirror_note"] = ("copies on other volumes are byte-identical; the manifest sha covers the "
                        "sorted relative path list, not PNG bytes")
    m.update(seed=args.seed, px=C.IMG_PX, pad=pad, seconds=round(el, 1),
             charts=len(tasks), charts_per_s=round(len(tasks) / el, 1))
    mp = os.path.join(C.EXP, f"imagelib_manifest_seed{args.seed}.json")
    with open(mp, "w") as f:
        json.dump(m, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: m[k] for k in ("file_count", "bytes", "manifest_sha256", "charts_per_s")},
                     indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
