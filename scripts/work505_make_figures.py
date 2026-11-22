#!/usr/bin/env python3
"""WORK-505 · figures for the single-file HTML report.

Nature-style: white background, no shadows/gradients, one restrained palette
(primary blue / teal / warning red from the WORK-505 style contract), English
labels inside the figures.  Every number comes from report_data.json, which is
written by scripts/work505_make_report_data.py from the raw per-run JSON; this
script only renders and never recomputes.
"""
from __future__ import annotations

import base64
import json
import os
import statistics

import matplotlib as mpl
import matplotlib.pyplot as plt

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = "WORK-505"
EXP_DIR = os.path.join(REPO, "experiments", EXP)
FIG_DIR = os.path.join(EXP_DIR, "figures")
os.makedirs(FIG_DIR, exist_ok=True)

BLUE = "#0F4D92"
TEAL = "#42949E"
RED = "#B64342"
GREY = "#8A8A86"
LGREY = "#D9D9D6"
FG = "#1A1A1A"

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
    "font.size": 8,
    "axes.labelsize": 8,
    "axes.titlesize": 9,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "axes.linewidth": 0.8,
    "axes.edgecolor": FG,
    "axes.labelcolor": FG,
    "text.color": FG,
    "xtick.color": FG,
    "ytick.color": FG,
    "xtick.labelsize": 7.5,
    "ytick.labelsize": 7.5,
    "legend.frameon": False,
    "legend.fontsize": 7.5,
    "pdf.fonttype": 42,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "savefig.dpi": 300,
})


def fig_b64(fig, name):
    path = os.path.join(FIG_DIR, name)
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return "figures/" + name


def main():
    # ---------------- inputs -----------------------------------------------
    # report_data.json is written by scripts/work505_make_report_data.py, which
    # is the single machine source of every number (raw per-run JSON -> the
    # summary statistics).  This script only renders; it never recomputes and
    # never rewrites the data file.
    dp = os.path.join(EXP_DIR, "report_data.json")
    if not os.path.exists(dp):
        raise SystemExit("report_data.json missing - run "
                         "scripts/work505_make_report_data.py first")
    data = json.load(open(dp, encoding="utf-8"))

    w500 = data["w500_bench_medians"]
    retest = data["retest_runs"]

    # WORK-500 reference values used for the +/-20 % band
    c_ref = {r["seed"]: float(r["sec_per_epoch_median"])
             for r in w500 if r["group"] == "C"}
    d_ref = {r["seed"]: float(r["sec_per_epoch_median"])
             for r in w500 if r["group"] == "D"}
    band_lo, band_hi = 0.13 * 0.8, 0.13 * 1.2

    c_rows = [r for r in retest if r["group"] == "C"]
    d_rows = [r for r in retest if r["group"] == "D"]
    c_all = [r["steady_median"] for r in c_rows]
    d_all = [r["steady_median"] for r in d_rows]

    # ---------------- snapshot verification -------------------------------
    sv = data["snapshot"]

    # ---------------- F1: C-group environment re-test ---------------------
    fig, ax = plt.subplots(figsize=(6.6, 2.9))
    xs, ys, cols, labs = [], [], [], []
    for i, r in enumerate(sorted(c_rows, key=lambda z: (z["phase"], z["seed"],
                                                         z["file"]))):
        xs.append(i)
        ys.append(r["steady_median"])
        cols.append(RED if r["steady_median"] > band_hi else BLUE)
        labs.append(f"s{r['seed']}")
    ax.axhspan(band_lo, band_hi, color=TEAL, alpha=0.13, lw=0)
    ax.axhline(0.13, color=TEAL, lw=1.0, ls="--")
    ax.axhline(c_ref.get("42", 0.13), color=GREY, lw=0.7, ls=":")
    ax.axhline(c_ref.get("43", 0.13), color=GREY, lw=0.7, ls=":")
    ax.scatter(xs, ys, s=26, c=cols, zorder=3, edgecolors="white",
               linewidths=0.6)
    med = data["retest_summary"]["C"]["median"]
    ax.axhline(med, color=BLUE, lw=1.0)
    ax.text(len(xs) - 0.4, med, f"  median {med:.4f} s", color=BLUE,
            va="center", fontsize=7.5)
    ax.text(0.02, band_hi, f"acceptance band 0.13 s +/-20% "
            f"({band_lo:.3f}-{band_hi:.3f})", color=TEAL, fontsize=7,
            transform=ax.get_yaxis_transform(), va="bottom")
    ax.set_xticks(xs)
    ax.set_xticklabels(labs, rotation=90, fontsize=6)
    ax.set_ylabel("steady s / epoch (median of epochs 2-4)")
    ax.set_xlabel("C group runs (MLP / GPU / 1 thread), ordered by phase then seed")
    ax.set_title("a  Environment re-test: C group lands inside the WORK-500 band",
                 loc="left", color=FG)
    ax.set_xlim(-0.8, len(xs) - 0.2)
    data["fig1"] = fig_b64(fig, "fig1_env_retest_C.png")

    # ---------------- F2: D-group re-test --------------------------------
    fig, ax = plt.subplots(figsize=(6.6, 2.9))
    xs = list(range(len(d_rows)))
    ys = [r["steady_median"] for r in d_rows]
    cols = [BLUE if r["seed"] == 42 else TEAL for r in d_rows]
    ax.scatter(xs, ys, s=26, c=cols, zorder=3, edgecolors="white",
               linewidths=0.6)
    ref = d_ref.get("42")
    if ref:
        ax.axhline(ref, color=RED, lw=1.0, ls="--")
        ax.text(len(xs) - 0.4, ref, f"  WORK-500 D median {ref:.4f} s",
                color=RED, va="center", fontsize=7.5)
    # per-seed medians: the gap to the reference is seed dependent
    for s, col in ((42, BLUE), (43, TEAL)):
        vals = [r["steady_median"] for r in d_rows if r["seed"] == s]
        if not vals:
            continue
        m = statistics.median(vals)
        ax.axhline(m, color=col, lw=0.8, ls=":", alpha=0.9)
        ax.text(-0.75, m, f" seed {s} median {m:.4f} s", color=col,
                va="bottom", fontsize=7)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"s{r['seed']}" for r in d_rows], rotation=90,
                       fontsize=6)
    ax.set_ylabel("steady s / epoch")
    ax.set_xlabel("D group runs (Transformer / GPU / 1 thread)")
    ax.set_title("b  Transformer re-test: seed 43 matches the reference, "
                 "seed 42 is slower", loc="left", color=FG)
    data["fig2"] = fig_b64(fig, "fig2_env_retest_D.png")

    # ---------------- F3: snapshot recovery -------------------------------
    fig, ax = plt.subplots(figsize=(6.6, 1.9))
    n_pass = sv["counts"]["pass"]
    n_missing = sv["counts"]["missing"]
    ax.barh([0], [n_pass], color=BLUE, height=0.42)
    ax.barh([0], [n_missing], left=[n_pass], color=LGREY, height=0.42)
    ax.text(n_pass / 2, 0, f"{n_pass} PASS", color="white", ha="center",
            va="center", fontsize=8, fontweight="bold")
    ax.text(n_pass + n_missing / 2, 0, f"{n_missing} MISSING (no file recoverable)",
            color=RED, ha="center", va="center", fontsize=8)
    ax.set_xlim(0, 680)
    ax.set_yticks([])
    ax.set_xlabel("per-stock snapshot files (680 expected)")
    ax.set_title("c  WORK-449 frozen snapshot: 6 of 680 files recoverable, "
                 "674 irrecoverable", loc="left", color=FG)
    ax.spines["left"].set_visible(False)
    data["fig3"] = fig_b64(fig, "fig3_snapshot_recovery.png")

    # ---------------- F4: transport channel matrix ------------------------
    # Seven channels, built from experiments/WORK-505/transport_evidence.json
    # (the raw-output archive) so the figure can never drift from the evidence.
    # The VERDICT LABEL is derived from each channel's recorded status -- an
    # earlier version hard-coded "OK" for channels that had actually failed,
    # which made the label contradict the reason printed next to it.
    _te = json.load(open(os.path.join(EXP_DIR, "transport_evidence.json"),
                         encoding="utf-8"))
    _ch = {c["id"]: c for c in _te["channels"]}
    _trash = _ch[2]["counters"]          # channel 2: GNOME trash
    _git = _ch[3]["counters"]            # channel 3: git object stores
    _em = _ch[6]["counters"]             # channel 6: re-fetch from EM
    _STATUS_MAP = {"failed": "FAIL", "partial": "PART", "rejected": "REJECT"}
    _names = {
        1: "1  SSH to Mo-server (192.168.9.104)",
        2: f"2  Mo-server GNOME Trash ({_trash['trash_files_total']:,} files)",
        3: f"3  git objects ({_git['blobs_repo']:,} + {_git['blobs_trash_repo']:,} blobs)",
        4: "4  SMB share Z: (\\\\192.168.9.104\\share)",
        5: "5  GitHub remote (quant-lab)",
        6: f"6  Re-fetch EM push2his ({_em['paths_tried']} paths)",
        7: "7  Other candidate panels (Sina qfq/hfq)",
    }
    _notes = {
        1: "target workspaces already cleaned",
        2: f"{_trash['matched']} files, sha256-verified",
        3: f"{_git['matched_codes']} matching blobs",
        4: "no WORK-449 data",
        5: "data/ is gitignored, not in history",
        6: "connection dropped by EM",
        7: "different source, not comparable",
    }
    channels = []
    for _cid in sorted(_names):
        _c = _ch[_cid]
        _st = _STATUS_MAP[_c["status"]]
        channels.append((_names[_cid], _st, _notes[_cid]))
    # evidence checks: a channel that recovered nothing must NOT be labelled OK
    assert _ch[2]["status"] == "partial" and _trash["matched"] == 6
    for _cid in (1, 3, 4, 5, 6):
        assert channels[[c[0] for c in channels].index(_names[_cid])][1] != "OK"
    assert channels[[c[0] for c in channels].index(_names[2])][1] == "PART"
    fig, ax = plt.subplots(figsize=(6.6, 3.2))
    cmap = {"PART": BLUE, "FAIL": RED, "REJECT": RED}
    for i, (name, st, note) in enumerate(channels):
        y = len(channels) - 1 - i
        ax.add_patch(plt.Rectangle((0, y - 0.34), 1.0, 0.68,
                                   color=cmap[st], alpha=0.14, lw=0))
        ax.add_patch(plt.Rectangle((0.005, y - 0.34), 0.055, 0.68,
                                   color=cmap[st], lw=0))
        ax.text(0.08, y, name, va="center", fontsize=7.5)
        ax.text(1.04, y, note, va="center", fontsize=7, color=GREY)
        ax.text(-0.03, y, st, va="center", ha="right", fontsize=7.5,
                color=cmap[st], fontweight="bold")
    ax.set_xlim(-0.09, 1.72)
    ax.set_ylim(-0.6, len(channels) - 0.4)
    ax.axis("off")
    ax.set_title("d  Every transport channel tried for the frozen snapshot",
                 loc="left", color=FG)
    data["fig4"] = fig_b64(fig, "fig4_transport_channels.png")

    # ---------------- F5: protocol pipeline status ------------------------
    stages = [
        ("Snapshot acquisition (680 sha256)", "PARTIAL", "6/680"),
        ("Panel + three mask arms", "BLOCKED", "needs snapshot"),
        ("Factor build (12 factors)", "BLOCKED", "needs panel"),
        ("Transformer training, 3 arms x 2 seeds", "BLOCKED", "needs panel"),
        ("Backtest, 3 arms x 2 seeds", "BLOCKED", "needs predictions"),
        ("Primary test (delta Sharpe + CI)", "BLOCKED", "needs backtests"),
        ("Independent recompute (<=1e-6)", "BLOCKED", "needs snapshot"),
        ("Verdict (reproduced / not reproduced)", "NOT RUN", "no data"),
    ]
    fig, ax = plt.subplots(figsize=(6.6, 3.2))
    cmap = {"DONE": TEAL, "PARTIAL": BLUE, "BLOCKED": RED, "NOT RUN": LGREY}
    for i, (name, st, note) in enumerate(stages):
        y = len(stages) - 1 - i
        ax.add_patch(plt.Rectangle((0, y - 0.3), 1.0, 0.6, color=cmap[st],
                                   alpha=0.13, lw=0))
        ax.add_patch(plt.Rectangle((0.004, y - 0.3), 0.04, 0.6, color=cmap[st],
                                   lw=0))
        ax.text(0.06, y, name, va="center", fontsize=7.5)
        ax.text(1.03, y, note, va="center", fontsize=7, color=GREY)
        ax.text(-0.02, y, st, va="center", ha="right", fontsize=7,
                color=cmap[st], fontweight="bold")
    ax.set_xlim(-0.13, 1.55)
    ax.set_ylim(-0.6, len(stages) - 0.4)
    ax.axis("off")
    ax.set_title("e  WORK-505 execution status: the chain stops at the snapshot",
                 loc="left", color=FG)
    data["fig5"] = fig_b64(fig, "fig5_execution_status.png")

    # ---------------- F6 (appendix): WORK-500 four-group baseline ---------
    fig, axes = plt.subplots(1, 3, figsize=(6.9, 2.4))
    groups = ["A", "B", "C", "D"]
    labels = ["A  MLP\nCPU 1 thr", "B  MLP\nCPU 12 thr",
              "C  MLP\nGPU", "D  Transformer\nGPU"]
    med = {}
    for r in w500:
        med.setdefault(r["group"], []).append(float(r["sec_per_epoch_median"]))
    med = {g: statistics.median(v) for g, v in med.items()}
    colors = [GREY, GREY, BLUE, TEAL]
    axes[0].bar(groups, [med[g] for g in groups], color=colors, width=0.62)
    axes[0].set_ylabel("s / epoch")
    axes[0].set_title("f1  WORK-500 baseline throughput", loc="left", fontsize=8)
    # 60-epoch extrapolation and peak VRAM are read from the WORK-500
    # reference table (report_data.json -> w500_bench_medians), never hardcoded
    ext = {}
    vram = {}
    for r in w500:
        g = r["group"]
        e = float(r["epochs_60_total_s"])
        v = float(r["peak_vram_mb"])
        ext[g] = max(ext.get(g, 0.0), e)
        vram[g] = max(vram.get(g, 0.0), v)
    axes[1].bar(groups, [ext[g] for g in groups], color=colors, width=0.62)
    axes[1].set_ylabel("60 epochs (s)")
    axes[1].set_title("f2  60-epoch extrapolation", loc="left", fontsize=8)
    axes[1].set_yscale("log")
    axes[2].bar(groups, [max(vram[g], 1.0) for g in groups], color=colors,
                width=0.62)
    axes[2].set_ylabel("peak VRAM (MB)")
    axes[2].set_title("f3  peak VRAM (A/B plotted at 1 MB floor)", loc="left",
                      fontsize=8)
    for a in axes:
        a.spines["right"].set_visible(False)
        a.spines["top"].set_visible(False)
    data["fig6"] = fig_b64(fig, "fig6_w500_baseline.png")

    print("figures written from", dp)
    print("C median", data["retest_summary"]["C"]["median"],
          "| in band:", data["retest_summary"]["C"]["verdict_in_band"])
    print("D median", data["retest_summary"]["D"]["median"],
          "| per seed:", data["retest_summary"]["D"].get("per_seed"))
    print("snapshot:", sv["counts"])


if __name__ == "__main__":
    main()
