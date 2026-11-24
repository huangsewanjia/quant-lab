#!/usr/bin/env bash
# WORK-555 · 自证清单（一条命令跑完全部自检，逐项打印实测结论）
#
# 用法：bash scripts/work555_selfcheck.sh
# 退出码：任一项不通过 -> 非 0（逐项都跑完，不早退，便于一次看到全部结论）
set -u
cd /srv/share/quantdb/code/quant-lab
PY=/tmp/w555venv/bin/python
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
LOG=/tmp/w555logs
mkdir -p "$LOG"
FAIL=0

run() {   # run <名称> <期望描述> <命令...>
  local name="$1"; shift
  local expect="$1"; shift
  echo "--- $name ---"
  if "$@" > "$LOG/selfcheck_$name.log" 2>&1; then
    echo "    PASS  ($expect)"
  else
    echo "    FAIL  ($expect)  退出码=$?"
    FAIL=1
    tail -6 "$LOG/selfcheck_$name.log" | sed 's/^/      /'
  fi
}

echo "=================================================================="
echo "WORK-555 自证清单  $(date -Is)"
echo "=================================================================="

run primitives        "引擎原语单测（梯度校验 + SWRR 逐位对照预注册附录 B）" \
    "$PY" scripts/work555_test_primitives.py
run mutation_test     "梯度单测的变异测试自证（可分辨必抓到 / 不可分辨不虚报）" \
    "$PY" scripts/work555_gradcheck_mutation_test.py
run tree_bruteforce   "软树梯度的独立全路径暴力实现对照" \
    "$PY" scripts/work555_tree_bruteforce.py
run thread_sens       "BLAS 线程敏感性实测（T=1 vs T=4）" \
    "$PY" scripts/work555_thread_sensitivity.py
run artifact_manifest "产物清单逐份 sha256 一致（锁住审计的直接输入）" \
    "$PY" scripts/work555_artifact_manifest.py --check
run final_numbers     "对外数字与文档同源（failure_log 生成块 + registry 行）" \
    "$PY" scripts/work555_final_numbers.py --check

echo "--- no-mask 断言（必须报错且退出码非 0）---"
"$PY" scripts/work555_backtest.py --arm dee --seed 42 --no-mask > "$LOG/selfcheck_nomask.log" 2>&1
RC=$?
if [ "$RC" -ne 0 ] && grep -q "UNTRADABLE_PRICE_READ" "$LOG/selfcheck_nomask.log"; then
  echo "    PASS  (exit=$RC)  $(grep -m1 UNTRADABLE_PRICE_READ "$LOG/selfcheck_nomask.log" | cut -c1-110)"
else
  echo "    FAIL  期望非 0 退出码且含 UNTRADABLE_PRICE_READ，实际 exit=$RC"
  FAIL=1
fi

echo "--- 快照 sha256（6 份应逐份 MATCH）---"
"$PY" - <<'PYEOF'
import hashlib, json, os, sys
man = json.load(open("experiments/WORK-555/snapshot_manifest.json", encoding="utf-8"))
bad = 0
for it in man["items"]:
    h = hashlib.sha256(open(it["path"], "rb").read()).hexdigest()
    ok = (h == it["sha256"])
    bad += 0 if ok else 1
    print(f"    {'MATCH ' if ok else 'DRIFT'} {it['name']:22s} {h[:16]}")
sys.exit(1 if bad else 0)
PYEOF
[ $? -ne 0 ] && FAIL=1

echo "=================================================================="
if [ "$FAIL" -eq 0 ]; then
  echo "自证清单：全部通过"
else
  echo "自证清单：存在未通过项（见上）"
fi
exit $FAIL
