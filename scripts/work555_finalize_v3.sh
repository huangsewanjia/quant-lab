#!/usr/bin/env bash
# WORK-555 · v3 收口流水线（独立审查修复后重跑）
#
# 与 work555_finalize.sh 的差别：
#   1) 回测覆盖**全部 4 臂 × 2 种子**（旧版只重跑 dee）——
#      S3 把回测引擎改成现金账本口径，xgb / xgbfull 的 preds 没变但回测产物必须重算；
#   2) 追加**组合权重不变量**检查（Σw ≤ 1、Σw + cash ≡ 1）；
#   3) 追加独立复算闸门（原样调用 audit/recompute_work555.py，含新增的
#      weight_invariant::* 项）；
#   4) 追加「快照 6 份 sha256 逐份 MATCH」检查；
#   5) 追加交付件外链 / 内嵌图检查。
#
# 顺序固定：回测 → 断言演示 → 独立复算 → 配图 → HTML → PNG/PDF → 自检
# 用法：bash scripts/work555_finalize_v3.sh
set -u
cd /srv/share/quantdb/code/quant-lab
PY=/tmp/w555venv/bin/python
LOG=/tmp/w555logs
mkdir -p "$LOG"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1

echo "=== [1/9] 组合回测（全部臂 × 两种子；现金账本口径） $(date -Is)"
for arm in dee mlp_only xgb xgbfull; do
  for seed in 42 43; do
    if [ -f "experiments/WORK-555/preds_${arm}_s${seed}.npz" ]; then
      "$PY" scripts/work555_backtest.py --arm "$arm" --seed "$seed" > "$LOG/bt_${arm}_s${seed}.log" 2>&1
      rc=$?
      echo "  arm=$arm seed=$seed rc=$rc  $(grep -o '"n_guard_violations": [0-9]*' "$LOG/bt_${arm}_s${seed}.log" | head -1)"
    else
      echo "  arm=$arm seed=$seed SKIP (no preds)"
    fi
  done
done

echo "=== [2/9] 断言演示：刻意关闭掩码，必须报 UNTRADABLE_PRICE_READ $(date -Is)"
"$PY" scripts/work555_backtest.py --arm dee --seed 42 --no-mask > "$LOG/nomask_demo.log" 2>&1
NOMASK_RC=$?
echo "  no-mask exit code = $NOMASK_RC（应非 0）"
grep -m 1 "UNTRADABLE_PRICE_READ" "$LOG/nomask_demo.log" || echo "  !! 未捕获 UNTRADABLE_PRICE_READ"

echo "=== [3/9] 组合权重不变量（Σw ≤ 1、Σw + cash ≡ 1） $(date -Is)"
"$PY" - <<'PYEOF'
import glob, os
import numpy as np, pandas as pd
worst_max, worst_dev, worst_file = 0.0, 0.0, None
for p in sorted(glob.glob("experiments/WORK-555/bt_daily_*.csv")):
    d = pd.read_csv(p)
    sw = d["sum_w"].to_numpy(dtype="float64")
    ca = d["cash"].to_numpy(dtype="float64")
    mx = float(np.max(sw)); dev = float(np.max(np.abs(sw + ca - 1.0)))
    over = int(np.sum(sw > 1.0 + 1e-9))
    if mx > worst_max:
        worst_max, worst_file = mx, os.path.basename(p)
    worst_dev = max(worst_dev, dev)
    if over or dev > 1e-9:
        print(f"  !! {os.path.basename(p)} max_sum_w={mx:.9f} over1_days={over} ledger_dev={dev:.3e}")
print(f"  max Σw over all bt_daily csv = {worst_max:.9f} (file={worst_file})  |  max |Σw+cash-1| = {worst_dev:.3e}")
PYEOF

echo "=== [3b/9] 产物清单（把审计的直接输入也纳入 sha256 覆盖） $(date -Is)"
"$PY" scripts/work555_artifact_manifest.py --build 2>&1 | tail -8

echo "=== [4/9] 独立复算（不 import 建模函数） $(date -Is)"
"$PY" audit/recompute_work555.py > "$LOG/audit_v3.log" 2>&1
AUDIT_RC=$?
tail -14 "$LOG/audit_v3.log"
echo "  audit exit=$AUDIT_RC"

echo "=== [4b/9] 对外数字唯一真源：重生成 failure_log §12.2/§12.3/§12.4 + registry WORK-555 行 $(date -Is)"
"$PY" scripts/work555_final_numbers.py 2>&1 | tail -20

echo "=== [5/9] 配图（系统 python3，matplotlib 只装在那里） $(date -Is)"
OMP_NUM_THREADS=2 python3 scripts/work555_figures.py 2>&1 | tail -6

echo "=== [6/9] 单文件 HTML 报告 $(date -Is)"
"$PY" scripts/work555_report.py 2>&1 | tail -4

echo "=== [7/9] 首屏 PNG + 打印 PDF $(date -Is)"
bash scripts/work555_screenshot.sh 2>&1 | tail -5

echo "=== [8/9] 交付件自检 $(date -Is)"
HTML=experiments/WORK-555/report.html
echo "--- 外链检查（应为 0）---"
echo "  img src=http : $(grep -o 'src="http[^"]*"' "$HTML" | wc -l)"
echo "  script src=http: $(grep -o '<script[^>]*src="http[^"]*"' "$HTML" | wc -l)"
echo "  link href=http.css: $(grep -o 'href="http[^"]*\.css[^"]*"' "$HTML" | wc -l)"
echo "--- 内嵌 base64 图数（应为 4）---"
echo "  $(grep -o 'data:image/png;base64,' "$HTML" | wc -l)"
echo "--- 交付件大小与 sha256 ---"
for f in "$HTML" experiments/WORK-555/report_print.pdf experiments/WORK-555/report_firstscreen_1440x900.png; do
  [ -f "$f" ] && printf "  %-58s %10d B  %s\n" "$f" "$(stat -c%s "$f")" "$(sha256sum "$f" | cut -c1-16)"
done

echo "=== [8b/9] 自证清单（8 项一次跑完；含审计新鲜度与 n_blocking=0） $(date -Is)"
bash scripts/work555_selfcheck.sh 2>&1 | tail -22
echo "  selfcheck exit=${PIPESTATUS[0]}"

echo "=== [9/9] 快照 sha256 复核（6 份应逐份 MATCH） $(date -Is)"
"$PY" - <<'PYEOF'
import hashlib, json, os
man = json.load(open("experiments/WORK-555/snapshot_manifest.json", encoding="utf-8"))
for it in man["items"]:
    p = it["path"]
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    print(f"  {'MATCH  ' if h == it['sha256'] else 'DRIFT  '} {it['name']:20s} {h[:16]}")
PYEOF

echo "=== [9b/9] 对外数字陈旧自检（--check，有陈旧即非 0） $(date -Is)"
"$PY" scripts/work555_final_numbers.py --check
echo "  final_numbers --check exit=$?"
echo "=== [9c/9] 产物清单复核（--check，漂移即非 0） $(date -Is)"
"$PY" scripts/work555_artifact_manifest.py --check
echo "  artifact_manifest --check exit=$?"

echo "=== 主检验量与判词（脚本直算） $(date -Is)"
"$PY" - <<'PYEOF'
import json, os, statistics
OUT = "experiments/WORK-555"
runs = {f[4:-5]: json.load(open(os.path.join(OUT, f), encoding="utf-8"))
        for f in sorted(os.listdir(OUT)) if f.startswith("run_") and f.endswith(".json")}
bts = {f[9:-5]: json.load(open(os.path.join(OUT, f), encoding="utf-8"))
       for f in sorted(os.listdir(OUT)) if f.startswith("backtest_") and f.endswith(".json")}
ds, ric_d, ric_m, sh = [], [], [], []
for s in (42, 43):
    d, m = runs.get(f"dee_s{s}"), runs.get(f"mlp_only_s{s}")
    if not (d and m):
        continue
    rd, rb = d["test_rankic_mean"], m["test_rankic_mean"]
    ds.append((rd - rb) / rb); ric_d.append(rd); ric_m.append(rb)
    b = bts.get(f"dee_s{s}", {}).get("metrics", {}).get("main_8bps", {})
    sh.append(b.get("sharpe_rf0"))
    print(f"  seed {s}: RankIC_DEE={rd:+.6f}  RankIC_MLP={rb:+.6f}  Δ_ic={(rd-rb)/rb*100:+.2f}%"
          f"  DEE_sharpe8={b.get('sharpe_rf0', float('nan')):+.4f}"
          f"  ann8={b.get('annualized_return', float('nan'))*100:+.2f}%"
          f"  mdd8={b.get('max_drawdown', float('nan'))*100:+.2f}%"
          f"  turn={b.get('turnover_daily_mean', float('nan')):.3f}")
if ds:
    print(f"  Δ_ic mean={statistics.mean(ds)*100:+.2f}%  range={(max(ds)-min(ds))*100:+.2f}pp")
    print(f"  DEE RankIC 极差={max(ric_d)-min(ric_d):.6f}  MLP 基线 RankIC 极差={max(ric_m)-min(ric_m):.6f} (阈值 0.01)")
    print(f"  DEE 净 Sharpe(8bps) mean={statistics.mean(sh):+.4f}  -> {'直接退' if statistics.mean(sh) < 0 else '未触发退档'}")
PYEOF

echo "FINALIZE V3 DONE $(date -Is)"
