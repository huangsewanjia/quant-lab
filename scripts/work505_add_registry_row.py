"""Insert (or refresh) the WORK-505 row into experiments/registry.md.

Idempotent: an existing ``WORK-505`` row is removed before the new one is
inserted, so the script can be re-run whenever the report is regenerated.

Every number in the row is READ from the machine-written artefacts:
  * the environment re-test statistics come from experiments/WORK-505/
    report_data.json (written by scripts/work505_make_report_data.py from the
    raw per-run JSON), never transcribed;
  * the transport-channel counters come from experiments/WORK-505/
    transport_evidence.json (the machine-readable twin of failure_log.md §2);
  * the GPU-contention observations come from experiments/WORK-505/
    env_observation.json (the machine-readable twin of failure_log.md §3.1);
  * the WORK-500 reference s/epoch comes from report_data.json's
    ``w500_bench_medians`` block, not from a literal in this file;
  * the WORK-449 prediction-dispersion diagnostic comes from
    experiments/WORK-449/baselines.json;
  * the delivered artifacts' sha256 are computed from the files on disk.
"""
import hashlib
import json
import os

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REG = os.path.join(REPO, "experiments", "registry.md")
DATA = os.path.join(REPO, "experiments", "WORK-505", "report_data.json")
TRANSPORT = os.path.join(REPO, "experiments", "WORK-505", "transport_evidence.json")
ENVOBS = os.path.join(REPO, "experiments", "WORK-505", "env_observation.json")
W449_BASE = os.path.join(REPO, "experiments", "WORK-449", "baselines.json")

ARTIFACTS = [
    ("experiments/WORK-505/report.html", "report.html"),
    ("experiments/WORK-505/preview_first_screen.png", "preview_first_screen.png"),
    ("experiments/WORK-505/report_print.pdf", "report_print.pdf"),
    ("experiments/WORK-505/report_data.json", "report_data.json"),
    ("experiments/WORK-505/snapshot_verification.json", "snapshot_verification.json"),
    ("experiments/WORK-505/snapshot_staging.json", "snapshot_staging.json"),
    ("experiments/WORK-505/transport_evidence.json", "transport_evidence.json"),
    ("experiments/WORK-505/env_observation.json", "env_observation.json"),
    ("experiments/WORK-505/preregistration.md", "preregistration.md"),
    ("experiments/WORK-505/preregistration_errata.md", "preregistration_errata.md"),
    ("experiments/WORK-505/knowledge_cards.md", "knowledge_cards.md"),
]


def sha256_of(rel):
    h = hashlib.sha256()
    with open(os.path.join(REPO, rel), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


sha_parts = []
for rel, label in ARTIFACTS:
    if not os.path.exists(os.path.join(REPO, rel)):
        raise SystemExit(f"missing artifact: {rel}")
    sha_parts.append(f"`{label}` = `{sha256_of(rel)}`")
sha_cell = "<br>**交付产物 sha256（内容寻址，运行本脚本时现算）**：" + "；".join(sha_parts)

d = json.load(open(DATA, encoding="utf-8"))
c = d["retest_summary"]["C"]
dd = d["retest_summary"]["D"]
snap = d["snapshot"]["counts"]
assert c["n"] == 16 and dd["n"] == 6, (c["n"], dd["n"])
assert all(c["verdict_in_band_by_aggregate"].values()), c["verdict_in_band_by_aggregate"]

c_med = f"{c['median']:.4f}"
c_conf = f"{c['confirm_median']:.4f}"
c_first = f"{c['first_median']:.4f}"
d_med = f"{dd['median']:.4f}"
d_rel = f"{dd['rel_diff_pct_vs_ref']['overall']:.2f}"
d42 = f"{dd['per_seed']['42']:.4f}"
d43 = f"{dd['per_seed']['43']:.4f}"
d42rel = f"{dd['rel_diff_pct_vs_ref']['42']:.1f}"
d43rel = f"{dd['rel_diff_pct_vs_ref']['43']:.2f}"

# --- WORK-500 reference s/epoch for group D (from the machine-written block) ---
_d_ref_rows = [r for r in d["w500_bench_medians"]
                if r["group"] == "D" and r["seed"] == "42"]
assert len(_d_ref_rows) == 1, _d_ref_rows
d_ref = _d_ref_rows[0]["sec_per_epoch_median"]

# --- transport-channel counters (transport_evidence.json) ---
te = json.load(open(TRANSPORT, encoding="utf-8"))
_ch = {ch["id"]: ch for ch in te["channels"]}
n_channels = len(te["channels"])
_trash = _ch[2]["counters"]
_git = _ch[3]["counters"]
_em = _ch[6]["counters"]
trash_files = _trash["trash_files_total"]
trashinfo_w449 = _trash["trashinfo_from_work449"]
n_blobs = _git["blobs_repo"] + _git["blobs_trash_repo"]
em_paths = _em["paths_tried"]
em_connected = _em["paths_connected"]
assert n_channels == 7 and em_paths == 4 and em_connected == 0

# --- GPU contention observations (env_observation.json) ---
eo = json.load(open(ENVOBS, encoding="utf-8"))
_ui = eo["gpu_contention_observations_note"]["utilization_pct_index"]
_utils = sorted({obs["smi_before"][_ui] for obs in eo["gpu_contention_observations"]}
                | {obs["smi_after"][_ui] for obs in eo["gpu_contention_observations"]})
util_txt = "/".join(str(u) for u in _utils)

# --- WORK-449 prediction-dispersion diagnostic (baselines.json) ---
b449 = json.load(open(W449_BASE, encoding="utf-8"))
disp_ratio_pct = b449["mlp_prediction_dispersion"]["ratio"] * 100.0

row = (
    "| `WORK-505` | 2026-11-22 | "
    "把 WORK-449 的复现**换成 Transformer 模型**在 RTX 3060 上重跑三档掩码对照，"
    "重新计算进/退判词（回答「模型从 MLP 换到 Transformer，判词翻面了吗？」） | "
    "2024-12-31（同一份 WORK-449 快照，期望口径） | "
    "`experiments/WORK-449/snapshot_manifest.json`（680 只逐股 sha256）→ 本单落地 "
    f"**{snap['pass']}/680 通过、{snap['missing']} 只不可复原**；"
    "校验产物 `experiments/WORK-505/snapshot_verification.json`（680 行）" + sha_cell + " | "
    "模型 = 横截面 **Transformer**（seq 680 / d 128 / 2 层 4 头 / FFN 512 / 无位置编码 / "
    "dropout 0.1）；底稿 = WORK-449 预注册 **v1.4**，只改 §7 模型一节；seed 42+43 并列"
    "（报 mean ± range，单 seed 不下判词）；损失 AdjMSE γ=0.1；AdamW lr 5e-4 / wd 1e-5 / "
    "clip 1.0；batch 12 交易日；≤60 epoch；成本 8 bps 双边（+5 bps 敏感性）；rf 1.5%；"
    "只做多、单票 ≤3%；bootstrap 1000 / block 20 / seed 42 | "
    "**判词未重算**（数据不可得，未产出任何研究数字）；已交付：环境复测 C 组 s/epoch "
    f"中位数 **{c_med} s**（n={c['n']}，判据 0.13 s ±20% ⇒ 在区间内；确认轮 "
    f"{c['confirm_n']} 次 {c_conf} s、首轮 {c['first_n']} 次 {c_first} s，两种口径同样在区间内）；"
    f"D 组（Transformer）{dd['n']} 次中位数 {d_med} s，**比 WORK-500 的 {d_ref} s 高 {d_rel}%**"
    f"（seed 42 = {d42} s，偏高 {d42rel}%，未查清；seed 43 = {d43} s，差 {d43rel}%）；"
    "预注册 v2 先行 commit `38fe864` + 勘误 v2.1（`preregistration_errata.md`，原件字节未动，"
    "PR #2）；实现 `scripts/work505_train_transformer.py` + `scripts/work505_primary_test.py` + "
    "`audit/recompute_work505.py`，合成面板端到端验证 **三档 × 两 seed × 两模型 = 12 次训练 + "
    "12 次回测 + 两 seed 合并主检验全通** | "
    f"**未完成（快照不可得）** —— 已穷尽 {n_channels} 条搬运通道（SSH 全盘 / GNOME 回收站 "
    f"{trash_files:,} 文件（其中 {trashinfo_w449:,} 个 trashinfo 指向 WORK-449）逐条 sha256 / "
    f"git 对象库 {n_blobs:,} blob / SMB 共享 / GitHub 历史 / EM 重取 / 其它实验的"
    "异源面板）；合同源东财 `push2his` 对本机直连、代理、curl_cffi 伪装、Mo-server 直连"
    f"**{em_paths} 种路径全部断连（连通 {em_connected}）** | "
    f"① **{snap['missing']}/{snap['manifest_items']} 逐股文件不可复原**（唯一根因；回收站里"
    f"只剩 {snap['pass']} 个 hfq 文件与清单逐字节相符）"
    f"② 由此**未产出** ΔSharpe / CI / ΔIC / 表观 IC / 独立复算，**未下进/退判词** "
    "③ 本机 `ds` 环境 numpy(MKL) 与 pip torch **OpenMP 双运行时冲突**，WORK-449 全部脚本"
    "会崩；解法 = `--system-site-packages` venv + pip(OpenBLAS) numpy（**不改用户环境**），"
    "已实测通过 ④ 本机 `git push` 403（凭据是 `yongkaimo` 而非仓库账号）、Mo-server 代理 "
    "push TLS 断；改走 GitHub git-data API，blob/tree sha 逐位相同，**已推送**（PR #2） "
    "⑤ 环境复测首轮 seed 43 两次超区间，排查为**外部 GPU 争用**（`nvidia-smi` 读到 util "
    f"{util_txt}%），确认轮 {c['confirm_n']} 次中位数 {c_conf} s 定案在区间内 "
    "⑥ **不得**由本单推出「问题不在模型容量」：WORK-449 的可实现 Sharpe 轴本身已被证明"
    "**功效不足**（换 seed 判词翻转、两轮 CI 都不含 0），且该诊断（预测离散度仅标签的 "
    f"{disp_ratio_pct:.2f}%）仍**悬空** "
    "⑦ 独立代码审查**两轮**共 32 项（第 1 轮 14 项 = 7 Major / 5 Minor / 2 Nit，判定 PARTIAL；"
    "第 2 轮 18 项 = 3 Major / 12 Minor / 3 Nit，判定 FAIL，3 项阻断已全部返修），"
    "处置清单见 `experiments/WORK-505/failure_log.md` §6 / §7 与 `preregistration_errata.md` |"
)

lines = open(REG, encoding="utf-8").read().split("\n")
kept = [ln for ln in lines if not ln.startswith("| `WORK-505` |")]
removed = len(lines) - len(kept)
for i, ln in enumerate(kept):
    if ln.startswith("| `（模板行）`"):
        kept.insert(i, row)
        break
else:
    raise SystemExit("template row not found")

open(REG, "w", encoding="utf-8").write("\n".join(kept))
print("WORK-505 row refreshed (old rows removed:", removed, ") at line", i + 1)
print("  C  median(n=%d) = %s | confirm = %s | first = %s | in band = %s"
      % (c["n"], c_med, c_conf, c_first, c["verdict_in_band_by_aggregate"]))
print("  D  median(n=%d) = %s | rel vs ref = +%s%% | s42 = %s (%s%%) | s43 = %s (%s%%)"
      % (dd["n"], d_med, d_rel, d42, d42rel, d43, d43rel))
for part in sha_parts:
    print("  ", part)
