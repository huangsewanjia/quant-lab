#!/usr/bin/env bash
# WORK-566 · Stage 3：组合回测 → 主检验与判词 → 独立复算 → 报告 → 渲染 → 快照 manifest
# 前置：Stage 1（先验/样本）与 Stage 2（训练）已产出 preds_seed{seed}_{arm}.npz
# 每一步失败即停（set -e），不做静默重试。
set -euo pipefail
cd "$(dirname "$0")/.."
PY=../.venv/bin/python
SEEDS=${SEEDS:-"42 2024"}
ARMS="s1_only s3_only s1_s2 full r0 r1 r2 r3 fmb"
COSTS="0.0005 0.0015 0.0030"

echo "=== [0/8] 前置检查：训练产物齐备性（缺产物直接停，不空跑 54 次回测）==="
# 失败即停（set -e）：任何种子/臂的 preds npz 缺失就立刻报错退出
for s in $SEEDS; do
  for arm in s1_only s3_only s1_s2 full r0 r1 r2 r3; do
    p="data/WORK-566/preds_seed${s}_${arm}.npz"
    if [ ! -s "$p" ]; then
      echo "缺少训练产物：$p —— Stage 3 不启动" >&2
      exit 3
    fi
  done
done
echo "  训练产物齐备：$(echo $SEEDS | wc -w) 种子 × 8 臂"

# N-R8（2026-11-25 第 3 轮复审）：只验「存在且非空」会放过**冒烟产物**（pred_test 只有 3 个预测日）
# → 这里逐个核对 pred_test 行数是否等于样本统计里的 test 样本数
SEEDS="$SEEDS" $PY - <<'PYEOF'
import json, os, sys
import numpy as np
DATA = "data/WORK-566"
stats = json.load(open(os.path.join("experiments/WORK-566", "sample_stats.json"), encoding="utf-8"))
n_test = int(stats["n_test"])
bad = []
seeds = os.environ.get("SEEDS", "42 2024").split()
for s in seeds:
    for arm in ["s1_only", "s3_only", "s1_s2", "full", "r0", "r1", "r2", "r3"]:
        p = os.path.join(DATA, f"preds_seed{s}_{arm}.npz")
        z = np.load(p)
        nt = len(z["pred_test"])
        if nt != n_test:
            bad.append(f"{os.path.basename(p)}: pred_test {nt} != {n_test}")
        if "idx_test" not in z.files or "date_test" not in z.files:
            bad.append(f"{os.path.basename(p)}: 缺 idx_test/date_test")
if bad:
    print("训练产物内容异常（可能是冒烟产物）：", bad, file=sys.stderr)
    sys.exit(6)
print(f"  训练产物内容校验通过：pred_test 行数 = {n_test:,}（非冒烟规模）")
PYEOF

# FMB 基线臂的产物由 work566_baselines.py 产出（**不是**训练脚本产出）——
# 回测对任意 --arm 都要求 preds_seed{seed}_{arm}.npz，漏掉 fmb 会让回测在
# 跑到 fmb 时才崩（2026-11-25 第 3 轮复审 B4）。缺失就在这里补齐。
for s in $SEEDS; do
  p="data/WORK-566/preds_seed${s}_fmb.npz"
  if [ ! -s "$p" ]; then
    echo "  补齐 FMB 基线产物：$p"
    $PY scripts/work566_baselines.py --seed "$s"
  fi
  if [ ! -s "$p" ]; then
    echo "FMB 基线产物生成失败：$p —— Stage 3 不启动" >&2
    exit 4
  fi
done
echo "  FMB 基线产物齐备"

echo "=== [1/8] 组合回测（各臂 × 种子 × 成本档）==="
# 54 次回测串行约 1 小时（每次 ~65s）；回测是单线程 numpy + 读小面板，
# 用 JOBS 路并行（默认 3 路，产物按臂/种子/成本分文件，互不冲突）
JOBS=${JOBS:-3}
# jobfile 放 data/WORK-566/（gitignored）：/tmp 配额被同机其他 agent 占满，不再依赖 /tmp
mkdir -p data/WORK-566
JOBFILE="data/WORK-566/bt_jobs.txt"
: > "$JOBFILE"     # 先截断：重跑时不要把上次的 job 追加进来（否则任务翻倍）
for arm in $ARMS; do
  for s in $SEEDS; do
    for c in $COSTS; do
      echo "$arm $s $c" >> "$JOBFILE"
    done
  done
done
export PY
# 注意：内层 bash -c **不能**以 `echo` 收尾 —— xargs 只看子命令的退出码，
# 尾随 echo 会把 python 的非零退出码吃掉（本机实测 xargs 退出码 = 0，链不会中止）。
xargs -P "$JOBS" -n 3 bash -c '
  arm=$1; s=$2; c=$3
  if ! "$PY" scripts/work566_backtest.py --arm "$arm" --seed "$s" --cost "$c" > /dev/null; then
    echo "  失败 arm=$arm seed=$s cost=$c" >&2
    exit 1
  fi
  echo "  完成 arm=$arm seed=$s cost=$c"
' _ < "$JOBFILE"
rm -f "$JOBFILE"
echo "  回测全部完成"

# 齐备性复核：54 个回测产物必须都在（防止「部分失败被吞」后带着残缺数据进主检验）
# 产物名由 backtest.py 决定：backtest_{arm}_seed{seed}_c{cost}.json（cost 是 float 的 str）
nb=0
for f in $("$PY" -c "
arms = '$ARMS'.split(); seeds = '$SEEDS'.split(); costs = '$COSTS'.split()
for a in arms:
    for s in seeds:
        for c in costs:
            print('experiments/WORK-566/backtest_%s_seed%s_c%s.json' % (a, s, float(c)))
"); do
  [ -s "$f" ] || { echo "回测产物缺失：$f" >&2; exit 5; }
  nb=$((nb+1))
done
echo "  回测产物齐备：$nb 份"

echo "=== [2/8] 主检验量与判词（H1/H2/H3/H4 + 基准调整 alpha + 论文对照表）==="
$PY scripts/work566_primary_test.py --seeds $SEEDS

echo "=== [3/8] 独立复算（audit/，不 import 建模阶段任何函数）==="
$PY audit/recompute_work566.py --seeds $SEEDS

echo "=== [4/8] 单文件 HTML 报告 ==="
$PY scripts/work566_report.py

echo "=== [5/8] 渲染交付件（1440×900 首屏 PNG + A4 打印首页 PDF）==="
$PY scripts/work566_render.py
cp -f figures/WORK-566/first_screen_1440x900.png experiments/WORK-566/preview_first_screen.png
cp -f figures/WORK-566/report_first_page.pdf  experiments/WORK-566/report_print.pdf
cp -f figures/WORK-566/report_full.pdf         experiments/WORK-566/report_print_full.pdf

echo "=== [6/8] 快照 manifest ==="
$PY scripts/work566_snapshot_manifest.py

echo "=== [7/8] 故意违规开关：刷新引擎级断言证据 + 收口文案重生成 ==="
# 用**故意违规开关**触发引擎自身的不可成交价守卫，把告警原文写进交付物。
# 预期行为：断言抛出 → 进程非零退出 → 这里必须显式吞掉退出码（否则 set -e 会中断链）。
# 若反而没抛异常 → 断言失效 → 判失败退出。
SEED0="${SEEDS%% *}"
if ! $PY scripts/work566_backtest.py --arm fmb --seed "$SEED0" --cost 0.0005 --assert-violation \
     > experiments/WORK-566/assert_violation_log.txt 2>&1; then
  if grep -q "\[ASSERT\] 不可成交价" experiments/WORK-566/assert_violation_log.txt; then
    echo "  故意违规断言已触发（原文见 experiments/WORK-566/assert_violation_log.txt）"
  else
    echo "故意违规开关抛了异常但不是不可成交价断言 → 证据无效" >&2
    exit 7
  fi
else
  echo "故意违规开关没有触发断言 → 引擎守卫失效" >&2
  exit 7
fi
# 训练日志入库（可核验证据）：.w566tmp 下由全链守护写入
for s in $SEEDS; do
  src_log="../.w566tmp/w566_s${s}.log"
  if [ -s "$src_log" ]; then
    cp -f "$src_log" "experiments/WORK-566/train_log_seed${s}.log"
    echo "  训练日志入库：train_log_seed${s}.log（$(wc -l < "$src_log") 行）"
  else
    echo "  警告：找不到训练日志 $src_log（交付自检会报缺）" >&2
  fi
done
$PY scripts/work566_registry_row.py
$PY scripts/work566_comment.py
$PY scripts/work566_registry_paste.py

echo "=== [8/8] 交付物自检（齐备性 + 单文件约束 + 判词口径一致性 + checkpoint 快照语义）==="
$PY scripts/work566_delivery_check.py

echo "=== Stage 3 全部完成 ==="
