"""补丁 v10（2026-11-29）：表 0 增加 H2 逐 seed 行；§3 的 H2 行按 seed 数自动写 1/2 或 2/2。

数据全部从 verdict.json 的 H2.per_seed 现读，不手抄；表 0 的 elif 分支保证只有 1 个 seed 时也能渲染。
"""
import io
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
p = os.path.join(ROOT, "scripts", "work557_report_stage34.py")
s = io.open(p, encoding="utf-8").read()


def rep(old, new):
    global s
    assert old in s, "NOT FOUND: " + old[:70]
    s = s.replace(old, new, 1)


# ---- 1. 生成 H2 行的辅助函数（放在 _rows_by_seed 旁边）----
rep('''def _rows_by_seed(runs):''',
'''def _h2_rows(verdict):
    """H2 逐 seed 渲染：ΔRankIC + 95% CI + 是否含 0。"""
    per = ((verdict.get("H2") or {}).get("per_seed") or {})
    out = []
    for sdd in sorted(per):
        v = per[sdd]
        lo, hi, pt = float(v["lo"]), float(v["hi"]), float(v["point"])
        verdict_txt = "不含 0（可证）" if not (lo <= 0.0 <= hi) else "含 0（不可证）"
        out.append(f'<tr><td>H2 vs GRU（s{sdd}）</td>'
                   f'<td class="num">{pt:+.4f}</td>'
                   f'<td class="num" colspan="6">95% CI [{lo:+.4f}, {hi:+.4f}]（{verdict_txt}）'
                   f'；10,000 次 block bootstrap，block=5 日，n={int(v.get("n_joined_days") or 0)} 天</td></tr>')
    if not out:
        out.append('<tr><td>H2 vs GRU</td><td class="num">未取到</td>'
                   '<td class="num" colspan="6">缺 <code>gru_s*</code> 基线与 <code>full_s*</code> 预测</td></tr>')
    return "\\n".join(out)


def _rows_by_seed(runs):''')

rep('''SEED_ROWS = _rows_by_seed(_runs)''',
'''SEED_ROWS = _rows_by_seed(_runs)
H2_ROWS = _h2_rows(VD)
H2_NSEEDS = len(((VD.get("H2") or {}).get("per_seed") or {}))''')

# ---- 2. 表 0 在 Δ_drop 行之后插入 H2 行 ----
rep('''<tr><td><b>Δ_drop</b>（H1）</td><td class="num" colspan="7">''',
'''{H2_ROWS}
<tr><td><b>Δ_drop</b>（H1）</td><td class="num" colspan="7">''')

# ---- 3. §3 的 H2 行：按实际 seed 数写 ----
rep('''<td class="st teal">1/2 → <b>seed 2024 可判</b>：''',
'''<td class="st teal">{H2_NSEEDS}/2 → <b>{H2_SEEDS_TXT} 可判</b>：''')

rep('''_la = bool((LA or {}).get("passed"))''',
'''_la = bool((LA or {}).get("passed"))
_h2per = ((VD.get("H2") or {}).get("per_seed") or {})
H2_SEEDS_TXT = " / ".join(f"s{k}" for k in sorted(_h2per)) if _h2per else "—"''')

io.open(p, "w", encoding="utf-8").write(s)
print("patched v10")
