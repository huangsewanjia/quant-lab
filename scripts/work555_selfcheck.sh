#!/usr/bin/env bash
# WORK-555 · 自证清单（一条命令跑完全部自检，逐项打印实测结论）
#
# 用法：bash scripts/work555_selfcheck.sh
# 退出码：任一项不通过 -> 非 0（逐项都跑完，不早退，便于一次看到全部结论）
#
# 可沙箱化：脚本按**自身位置**定位仓库（不用硬编码路径），
# 审查方把 scripts/ 与 experiments/ 拷进沙箱后可直接跑，不会写被审仓库。
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
cd "$REPO"
PY=/tmp/w555venv/bin/python
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
LOG=${TMPDIR:-/tmp}/w555logs
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
echo "仓库: $REPO"
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
run quant_diag        "产物精度诊断（产物复算值与建模侧报告值在 1e-6 判据内且两侧实现逐位相等）" \
    "$PY" scripts/work555_float32_quant_diag.py --check
run final_numbers     "对外数字与文档同源（failure_log 生成块 + registry 行 + 审计新鲜度）" \
    "$PY" scripts/work555_final_numbers.py --check

echo "--- 审计新鲜度（审计所依据的产物清单 sha256 必须与当前清单一致）---"
"$PY" - <<'PYEOF'
import hashlib, json, os, sys
OUT = "experiments/WORK-555"
am = os.path.join(OUT, "artifact_manifest.json")
au = os.path.join(OUT, "audit_findings.json")
if not (os.path.exists(am) and os.path.exists(au)):
    print("    FAIL  缺 artifact_manifest.json 或 audit_findings.json")
    sys.exit(1)
h = hashlib.sha256(open(am, "rb").read()).hexdigest()
a = json.load(open(au, encoding="utf-8"))
rec = a.get("artifact_manifest_sha256")
if rec is None:
    print("    FAIL  审计输出未记录 artifact_manifest_sha256（审计无法判新鲜度）")
    sys.exit(1)
if rec != h:
    print(f"    FAIL  审计已陈旧：审计记录 {rec[:16]}… vs 当前清单 {h[:16]}…")
    print("          -> 产物在审计之后变过，必须重跑 audit/recompute_work555.py")
    sys.exit(1)
print(f"    PASS  审计 {a.get('generated_at')} 依据的清单 sha256 = {h[:16]}…")
print(f"          审计判决 {a.get('verdict')}（blocking {a.get('n_blocking')} / {a.get('n_findings')} 项）")
if a.get("n_blocking"):
    print("    FAIL  审计存在未关闭的 blocking，不得据此出正式结论")
    sys.exit(1)
# 第四轮审查 S4-1 实测发现：门禁原先只读 n_blocking **计数器**，不看 findings[] 数组，
# 于是「数组里有 severity=blocking、但计数器仍是 0」的状态会被完全放行（负控 nc_sev）。
# 这里自行从 findings[] 派生一次，并要求与计数器互相一致。
arr_blocking = [f for f in a.get("findings", []) if f.get("severity") == "blocking"]
if arr_blocking:
    print(f"    FAIL  findings[] 里有 {len(arr_blocking)} 条 severity=blocking，"
          f"但 n_blocking={a.get('n_blocking')}（计数器与数组不一致）")
    for f in arr_blocking[:5]:
        print(f"          - {f.get('item')}")
    sys.exit(1)
if int(a.get("n_blocking") or 0) != len(arr_blocking):
    print("    FAIL  n_blocking 与 findings[] 派生值不一致")
    sys.exit(1)
print(f"    PASS  findings[] 派生 blocking 数 = {len(arr_blocking)}，与 n_blocking 一致")
PYEOF
[ $? -ne 0 ] && FAIL=1

echo "--- no-mask 断言（必须报错且退出码非 0）---"
"$PY" scripts/work555_backtest.py --arm dee --seed 42 --no-mask > "$LOG/selfcheck_nomask.log" 2>&1
RC=$?
if [ "$RC" -ne 0 ] && grep -q "UNTRADABLE_PRICE_READ" "$LOG/selfcheck_nomask.log"; then
  echo "    PASS  (exit=$RC)  $(grep -m1 UNTRADABLE_PRICE_READ "$LOG/selfcheck_nomask.log" | cut -c1-110)"
else
  echo "    FAIL  期望非 0 退出码且含 UNTRADABLE_PRICE_READ，实际 exit=$RC"
  FAIL=1
fi

# nomask_all 会写回 experiments/WORK-555/nomask_assert.json，而该文件在**产物清单**
# 的 sha256 覆盖范围内 —— 不还原的话，跑一次自证清单就会让清单漂移（第四轮审查 S4-2
# 实测：第 1 次跑全绿，第 2 次跑即报「漂移 1」）。这里跑前备份、跑后还原，使自证清单幂等。
NOMASK_REC=experiments/WORK-555/nomask_assert.json
NOMASK_BAK="$LOG/nomask_assert.bak"
[ -f "$NOMASK_REC" ] && cp "$NOMASK_REC" "$NOMASK_BAK"
run nomask_all "不可成交断言 8 组合（4 臂 × 2 种子）逐组触发且退出码非 0" \
    "$PY" scripts/work555_nomask_all.py
if [ -f "$NOMASK_BAK" ]; then
  cp "$NOMASK_BAK" "$NOMASK_REC"
  rm -f "$NOMASK_BAK"
  echo "    (已还原 $NOMASK_REC，避免自证清单改写清单覆盖的产物)"
fi

echo "--- 快照 sha256（6 份应逐份 MATCH）---"
"$PY" - <<'PYEOF'
import hashlib, json, sys
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
