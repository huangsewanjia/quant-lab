"""补丁 v7（2026-11-28 夜）：把「返工 4 项 + seed2024 两档」的新事实写进报告。

必改/必补：
  1. 对照 B 的 503 基准：同 seed 对照必须用 s42 的 −17.59%，不能拿 503 的
     两 seed 中位数 −21.64% 去和本单的单 seed 比（幅度会虚高一倍）；
  2. H3 现在**已独立复算**（mismatch 0 @1e-6），不再写「仅建模侧」；
  3. integrity：I3 已修（两份日历只是 YYYY-MM-DD vs YYYYMMDD 格式差异）；
     I4 仍是 5,798 条，全部落在 2012-01-04 起的头 20 个交易日 → 不影响 2022–2024 测试段；
  4. lookahead：修好 KeyError 后能跑完，但**未通过**（特征/先验两轴与 qlib 重建不一致，
     标签轴 0 差）→ 写进不确定项，不许留在表外；
  5. 表 0 按 verdict.json 里实际存在的 seed 逐行渲染（单 seed 或双 seed 都适用）。
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


# ---------- 1. 503 基准：同 seed 对照 ----------
rep('''<tr><td><b>H1 Δ_drop</b></td><td class="num">−21.64%</td><td class="num">未取到</td><td class="num">—</td>
<td><b>不可判</b>：full 与 no_vq 一个都没出</td></tr>
<tr><td>H2 vs GRU 增量 CI</td><td class="num">见 503 §H2</td><td class="num">未取到</td><td class="num">—</td>
<td><b>不可判</b>：缺 full 档</td></tr>
<tr><td>H3 净 SR（8/30 bps）</td><td class="num">见 503</td><td class="num">未取到</td><td class="num">—</td>
<td><b>不可判</b>：缺四档预测</td></tr>''',
'''<tr><td><b>H1 Δ_drop</b></td><td class="num">−17.59%（s42）</td><td class="num">{PS42:.2%}</td><td class="num">{PS42-(-0.17585):+.2%}</td>
<td><b>可判（单 seed）</b>：s42 → s42 同 seed 对照；<b>503 的两 seed 合并中位数是 −21.64%</b>，
拿它跟本单的单 seed 比会虚高近一倍（v1 版就是这么错的，已更正）</td></tr>
<tr><td>H2 vs GRU 增量 CI</td><td class="num">见 503 §H2</td><td class="num">{H2P:+.4f} [{H2LO:+.4f}, {H2HI:+.4f}]</td>
<td class="num">—</td><td><b>可判（s2024）</b>：CI 不含 0；s42 缺 gru_s42</td></tr>
<tr><td>H3 净 SR（8/30 bps）</td><td class="num">见 503</td><td class="num">{SR50:.4f}（两 seed 中位）</td><td class="num">—</td>
<td><b>可判</b>：判退（&lt;0）；已独立复算 0 mismatch</td></tr>''')

rep('''<p class="note"><b>必须回答的那一句</b>''',
'''<p class="note">口径提醒：上表第 3 行用的是 <b>WORK-503 的 s42 单 seed 值 −17.59%</b>（取自 503 verdict 的
<code>H1.delta_ic_per_seed["42"]</code>）；503 的<b>两 seed 合并中位数</b>是 −21.64%（v1 版报告错用了它，
与本单的单 seed 比会把幅度虚高近一倍，已更正）。</p>
<p class="note"><b>必须回答的那一句</b>''')

# ---------- 2. H3 已独立复算 ----------
rep('''<tr><td>H3</td><td>净 SR（8 / 30 bps 两档）、AR、MDD、换手；净 SR &lt; 0 直接判退</td>
<td>四档 × 成本档的回测产物</td>
<td class="st red">2/8 → seed 42 两档可判：净 SR {F_SR:.3f} / {N_SR:.3f}，<b>判退</b></td></tr>''',
'''<tr><td>H3</td><td>净 SR（8 / 30 bps 两档）、AR、MDD、换手；净 SR &lt; 0 直接判退</td>
<td>四档 × 成本档的回测产物</td>
<td class="st teal">2/8 → seed 42 两档：净 SR {F_SR:.3f} / {N_SR:.3f}，<b>判退</b>；
<b>已独立复算 0 mismatch @1e-6</b></td></tr>''')

# ---------- 3. 表 0 改为按 seed 渲染 ----------
rep('''<tr><td>PRISM-VQ full</td><td class="num">{F_IC:.4f}</td><td class="num">{F_IR:.4f}</td><td class="num">{F_SR:.4f}</td>
<td class="num">{F_AR:.2%}</td><td class="num">{F_MDD:.4f}</td><td class="num">{F_TO:.4f}</td><td class="num">28（早停）</td></tr>
<tr><td>PRISM-VQ no_vq</td><td class="num">{N_IC:.4f}</td><td class="num">{N_IR:.4f}</td><td class="num">{N_SR:.4f}</td>
<td class="num">{N_AR:.2%}</td><td class="num">{N_MDD:.4f}</td><td class="num">{N_TO:.4f}</td><td class="num">早停</td></tr>''',
'''{SEED_ROWS}''')

# ---------- 4. 生成 SEED_ROWS 与双 seed 说明 ----------
rep('''# 在结论框之后插入 seed42 明细小节''', '''# 在结论框之后插入 seed42 明细小节''') if False else None

rep('''def pct(a, b_):''',
'''def _rows_by_seed(runs):
    """按 verdict.runs 里实际存在的 seed 逐行渲染（full/no_vq 成对）。"""
    out = []
    seeds = sorted({k.rsplit("_s", 1)[-1] for k in runs if "_s" in k})
    for sdd in seeds:
        for arm in ("full", "no_vq"):
            r = runs.get(f"{arm}_s{sdd}")
            if not r:
                continue
            ep = r.get("epochs_ran")
            out.append(f'<tr><td>PRISM-VQ {arm}（s{sdd}）</td>'
                       f'<td class="num">{float(r.get("rankic") or 0):.4f}</td>'
                       f'<td class="num">{float(r.get("rankicir") or 0):.4f}</td>'
                       f'<td class="num">{float(r.get("sr") or 0):.4f}</td>'
                       f'<td class="num">{float(r.get("ar") or 0):.2%}</td>'
                       f'<td class="num">{float(r.get("mdd") or 0):.4f}</td>'
                       f'<td class="num">{float(r.get("turnover") or 0):.4f}</td>'
                       f'<td class="num">{ep if ep else "早停"}</td></tr>')
    return "\\n".join(out)


def pct(a, b_):''')

rep('''F = _runs.get("full_s42", {})''',
'''SEED_ROWS = _rows_by_seed(_runs)
F = _runs.get("full_s42", {})''')

# ---------- 5. §8 不确定项：integrity / lookahead 的真实状态 ----------
rep('''<li><b>实测只覆盖 stage-1、单 seed、前 10 轮</b>''',
'''<li><b>lookahead 检查仍不可用（已修到能跑，但未通过）</b>：修掉 <code>KeyError: sh600001</code>
（qlib 二进制缺票，现按候选序跳过并记录 <code>skipped_symbols</code>）之后能完整跑完，结果是
<code>label_max_abs_diff = 7.4e-9</code>（标签轴通过）、但 <code>feature_date_mismatches = 1,997,525 / 2,327,786</code>、
<code>prior_mismatches = 2,617,126 / 2,625,423</code>（最大绝对差 1.35–4.81，呈<b>常量偏移</b>，
更像两套归一化口径不一致而非日期错位）。<b>因此不能据此声称「无前视」</b>；
影响范围：H1/H3 全部建立在同一批特征上，若特征侧真有前视，所有档同受影响 —— 本检查无法证实也无法证伪。</li>
<li><b>实测只覆盖 stage-1、单 seed、前 10 轮</b>''')

rep('''<li><b>实测只覆盖 stage-1、单 seed、前 10 轮</b></li>''', '''</li>''') if False else None

io.open(p, "w", encoding="utf-8").write(s)
print("patched v7")

# ---------- 6. H2：seed 2024 已可判 ----------
rep("""<td><code>full_*</code> 两份 + <code>gru_*</code> 两份（后者本单有 1 份）</td><td class="st red">0/2 → 不可判</td></tr>""",
"""<td><code>full_*</code> 两份 + <code>gru_*</code> 两份（后者本单有 1 份）</td>
<td class="st teal">1/2 → seed 2024 可判：ΔRankIC = <b>{H2P:+.4f}</b>，95% CI <b>[{H2LO:+.4f}, {H2HI:+.4f}]</b>（10,000 次 block bootstrap，block=5），<b>不含 0</b>；seed 42 缺 gru_s42</td></tr>""")

rep('''SR50 = float((VD.get("H3") or {}).get("sr_median") or F_SR)''',
'''SR50 = float((VD.get("H3") or {}).get("sr_median") or F_SR)
_H2 = ((VD.get("H2") or {}).get("per_seed") or {}).get("2024") or {}
H2P = float(_H2.get("point") or 0)
H2LO = float(_H2.get("lo") or 0)
H2HI = float(_H2.get("hi") or 0)''')

# ---------- 7. 结论框：两 seed 口径 ----------
rep("""<div class="word">seed 42 两档已跑出：Δ_drop = <b>−13.83%</b>（部分复现），净 SR &lt; 0 判退；其余三档仍未跑</div>""",
"""<div class="word">两 seed 跑完：Δ_drop = <b>{DD:.2%}</b>（部分复现，贴 −10% 边界），净 SR &lt; 0 判退；无 MoE/无先验两档仍未跑</div>""")

rep("""预测与回测已出，<b>H1 可判</b>：Δ_drop = <b>{DD:.2%}</b>，落在「部分复现」带（不进 [−40%,−15%] 也不退 [−10%,+10%]）；
H3 仍判<b>退</b>（净 SR {SR50:.3f} &lt; 0，与 H1 无关）。<b>H2 仍不可判</b>（缺 gru_s42 与其余两档），
且本结果<b>只有 1 个 seed（n_seeds=1），不满足预注册的 ≥2 seed</b>。""",
"""两个 seed（42 / 2024）都已跑完并出预测、回测、判词：<b>H1 可判</b>：
逐 seed Δ_drop = {PS42:.2%} / {PS24:.2%}，<b>两 seed 中位数口径 {DD:.2%}</b>，落在「部分复现」带
（不进 [−40%,−15%]、也不退 [−10%,+10%]，但离 −10% 只差 {MARGIN:.2f} 个百分点）；
H3 判<b>退</b>（两 seed 净 SR 中位数 {SR50:.4f} &lt; 0，与 H1 无关）；
<b>H2 在 seed 2024 上可判</b>：ΔRankIC {H2P:+.4f}，95% CI [{H2LO:+.4f}, {H2HI:+.4f}] 不含 0。
seed 42 缺 GRU 基线（`gru_s42` 从未成功），故 H2 只有 1/2 seed。""")

rep('''GAP = abs(float(_H1.get("paper_delta_formula") or 0.2693) * 100 - abs(DD) * 100)''',
'''GAP = abs(float(_H1.get("paper_delta_formula") or 0.2693) * 100 - abs(DD) * 100)
_ps = (_H1.get("delta_ic_per_seed") or {})
PS42 = float(_ps.get("42") or DD)
PS24 = float(_ps.get("2024") or DD)
MARGIN = abs(abs(DD) * 100 - 10.0)''')

io.open(p, "w", encoding="utf-8").write(s)
print('patched v7 (extended)')
