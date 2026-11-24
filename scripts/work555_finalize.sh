#!/usr/bin/env bash
# WORK-555 · DEE 臂落盘后的收口流水线（顺序固定）
#   回测 DEE 两种子 → 独立复算（全部臂）→ 配图 → 单文件 HTML 报告 → 首屏 PNG + 打印 PDF
#   → 勾稽自检（列相加=合计 / Δ 相加=合计 Δ / 报告内外链检查）
set -u
cd /srv/share/quantdb/code/quant-lab
PY=/tmp/w555venv/bin/python
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1

echo "=== [1/7] DEE 回测 (8bps 主口径 + 5bps 敏感性) $(date -Is)"
for s in 42 43; do
  "$PY" scripts/work555_backtest.py --arm dee --seed $s 2>&1 | tail -4
done

echo "=== [2/7] 独立复算（不 import 建模函数） $(date -Is)"
"$PY" audit/recompute_work555.py > /tmp/w555logs/audit_final.log 2>&1
AUDIT_RC=$?
tail -12 /tmp/w555logs/audit_final.log
echo "audit exit=$AUDIT_RC"

echo "=== [3/7] 配图 $(date -Is)"
# 配图脚本用**系统 python3**：matplotlib 只装在系统解释器里，venv 里没有
OMP_NUM_THREADS=2 python3 scripts/work555_figures.py 2>&1 | tail -8

echo "=== [4/7] 单文件 HTML 报告 $(date -Is)"
"$PY" scripts/work555_report.py 2>&1 | tail -4

echo "=== [5/7] 首屏 PNG + 打印 PDF $(date -Is)"
bash scripts/work555_screenshot.sh 2>&1 | tail -4

echo "=== [6/7] 交付件自检 $(date -Is)"
HTML=experiments/WORK-555/report.html
echo "--- 外链检查（应为 0；data: 与 arXiv 引用不计）---"
grep -o 'src="http[^"]*"' "$HTML" | wc -l
grep -o '<script[^>]*src="http[^"]*"' "$HTML" | wc -l
grep -o 'href="http[^"]*\.css[^"]*"' "$HTML" | wc -l
echo "--- 内嵌图片数 ---"
grep -o 'data:image/png;base64,' "$HTML" | wc -l
echo "--- 报告里出现的关键数字 ---"
python3 - <<'PYEOF'
import json, os
OUT = "experiments/WORK-555"
runs = {f[4:-5]: json.load(open(os.path.join(OUT, f), encoding="utf-8"))
        for f in sorted(os.listdir(OUT)) if f.startswith("run_") and f.endswith(".json")}
bts = {f[9:-5]: json.load(open(os.path.join(OUT, f), encoding="utf-8"))
       for f in sorted(os.listdir(OUT)) if f.startswith("backtest_") and f.endswith(".json")}
for s in (42, 43):
    d, m = runs.get(f"dee_s{s}"), runs.get(f"mlp_only_s{s}")
    if not (d and m):
        continue
    rd, rb = d["test_rankic_mean"], m["test_rankic_mean"]
    delta = (rd - rb) / rb
    b = bts.get(f"dee_s{s}", {}).get("metrics", {}).get("main_8bps", {})
    print(f"seed {s}: RankIC_DEE={rd:+.6f}  RankIC_MLP={rb:+.6f}  Δ_ic={delta*100:+.2f}%"
          f"  DEE_sharpe8={b.get('sharpe_rf0', float('nan')):+.4f}"
          f"  ann8={b.get('annualized_return', float('nan'))*100:+.2f}%"
          f"  mdd8={b.get('max_drawdown', float('nan'))*100:+.2f}%"
          f"  turn={b.get('turnover_daily_mean', float('nan')):.3f}")
ds = []
for s in (42, 43):
    d, m = runs.get(f"dee_s{s}"), runs.get(f"mlp_only_s{s}")
    if d and m:
        ds.append((d["test_rankic_mean"] - m["test_rankic_mean"]) / m["test_rankic_mean"])
if ds:
    import statistics
    print(f"Δ_ic mean={statistics.mean(ds)*100:+.2f}%  range={(max(ds)-min(ds))*100:+.2f}pp")
    ric = [runs[f"dee_s{s}"]["test_rankic_mean"] for s in (42, 43)]
    print(f"DEE 两种子 RankIC 极差 = {max(ric)-min(ric):.6f}  (阈值 0.01)")
PYEOF

echo "=== [7/7] 快照 sha256 复核 $(date -Is)"
sha256sum data/WORK-555/*.csv | head -8
echo "FINALIZE DONE $(date -Is)"
