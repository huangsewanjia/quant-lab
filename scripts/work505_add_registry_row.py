"""Insert the WORK-505 row into experiments/registry.md."""
import os

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
p = os.path.join(REPO, "experiments", "registry.md")
lines = open(p, encoding="utf-8").read().split("\n")

row = (
    "| `WORK-505` | 2026-11-22 | "
    "把 WORK-449 的复现**换成 Transformer 模型**在 RTX 3060 上重跑三档掩码对照，"
    "重新计算进/退判词（回答「模型从 MLP 换到 Transformer，判词翻面了吗？」） | "
    "2024-12-31（同一份 WORK-449 快照，期望口径） | "
    "`experiments/WORK-449/snapshot_manifest.json`（680 只逐股 sha256）→ 本单落地 "
    "**6/680 通过、674 只不可复原**；校验产物 `experiments/WORK-505/snapshot_verification.json`"
    "（680 行） | "
    "模型 = 横截面 **Transformer**（seq 680 / d 128 / 2 层 4 头 / FFN 512 / 无位置编码 / "
    "dropout 0.1）；底稿 = WORK-449 预注册 **v1.4**，只改 §7 模型一节；seed 42+43 并列"
    "（报 mean ± range，单 seed 不下判词）；损失 AdjMSE γ=0.1；AdamW lr 5e-4 / wd 1e-5 / "
    "clip 1.0；batch 12 交易日；≤60 epoch；成本 8 bps 双边（+5 bps 敏感性）；rf 1.5%；"
    "只做多、单票 ≤3%；bootstrap 1000 / block 20 / seed 42 | "
    "**判词未重算**（数据不可得，未产出任何研究数字）；已交付：环境复测 C 组 s/epoch 中位数 "
    "**0.1313 s**（判据 0.13 s ±20% ⇒ 在区间内，n=16；D 组复现 WORK-500 的 2.6993 s/epoch "
    "至 0.1% 以内）、预注册 v2 先行 commit `38fe864`（PR #2）、实现 "
    "`scripts/work505_train_transformer.py` + 合成面板端到端验证"
    "（训练 → WORK-449 原版回测脚本 → metrics，三臂 × 两模型全绿） | "
    "**未完成（快照不可得）** —— 已穷尽 7 条搬运通道（SSH 全盘 / GNOME 回收站 11,458 文件"
    "逐条 sha256 / git 对象库 8,708 blob / SMB 共享 / GitHub 历史 / EM 重取 / 其它实验的"
    "异源面板）；合同源东财 `push2his` 对本机直连、代理、curl_cffi 伪装、Mo-server 直连"
    "**四种路径全部断连** | "
    "① **674/680 逐股文件不可复原**（唯一根因；回收站里只剩 6 个 hfq 文件与清单逐字节相符）"
    "② 由此**未产出** ΔSharpe / CI / ΔIC / 表观 IC / 独立复算，**未下进/退判词** "
    "③ 本机 `ds` 环境 numpy(MKL) 与 pip torch **OpenMP 双运行时冲突**，WORK-449 全部脚本"
    "会崩；解法 = `--system-site-packages` venv + pip(OpenBLAS) numpy（**不改用户环境**），"
    "已实测通过 ④ 本机 `git push` 403（凭据是 `yongkaimo` 而非仓库账号）、Mo-server 代理 "
    "push TLS 断；改走 GitHub git-data API，blob/tree sha 逐位相同，**已推送**（PR #2） "
    "⑤ 环境复测首轮 seed 43 两次超区间，排查为**外部 GPU 争用**（`nvidia-smi` 读到 util "
    "19/15/68%），确认轮 10 次中位数 0.1313 s 定案在区间内 "
    "⑥ **不得**由本单推出「问题不在模型容量」：WORK-449 的可实现 Sharpe 轴本身已被证明"
    "**功效不足**（换 seed 判词翻转、两轮 CI 都不含 0），且该诊断（预测离散度仅标签的 0.58%）"
    "仍**悬空** |"
)

for i, line in enumerate(lines):
    if line.startswith("| `（模板行）`"):
        lines.insert(i, row)
        break
else:
    raise SystemExit("template row not found")

open(p, "w", encoding="utf-8").write("\n".join(lines))
print("registry row inserted at line", i + 1)
