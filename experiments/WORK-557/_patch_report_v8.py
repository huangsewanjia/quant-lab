"""补丁 v8（2026-11-28 夜，最终版）：把「2 seed + 三项链修复 + 基准更正」写进报告。

对着生成器的**当前**文本打补丁（v6/v7 已改过一部分），每处都有断言。
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


# ---------- A. 常量：两 seed 逐值 + H2 ----------
rep('''SR50 = float((VD.get("H3") or {}).get("sr_median") or F_SR)''',
'''SR50 = float((VD.get("H3") or {}).get("sr_median") or F_SR)
_ps = (_H1.get("delta_ic_per_seed") or {})
PS42 = float(_ps.get("42") or DD)
PS24 = float(_ps.get("2024") or DD)
MARGIN = abs(abs(DD) * 100 - 10.0)
_H2 = ((VD.get("H2") or {}).get("per_seed") or {}).get("2024") or {}
H2P = float(_H2.get("point") or 0)
H2LO = float(_H2.get("lo") or 0)
H2HI = float(_H2.get("hi") or 0)''')

# ---------- B. 结论框标题 ----------
rep('''<div class="word">seed 42 两档已跑出：Δ_drop = <b>−13.83%</b>（部分复现），净 SR &lt; 0 判退；其余三档仍未跑</div>''',
'''<div class="word">两 seed 跑完：Δ_drop = <b>{DD:.2%}</b>（部分复现，离 −10% 边界仅 {MARGIN:.2f}pp），净 SR &lt; 0 判退；无 MoE / 无先验两档仍未跑</div>''')

# ---------- C. 结论框第 ③ 条 ----------
rep('''预测与回测已出，<b>H1 可判</b>：Δ_drop = <b>{DD:.2%}</b>，落在「部分复现」带（不进 [−40%,−15%] 也不退 [−10%,+10%]）；
H3 仍判<b>退</b>（净 SR {SR50:.3f} &lt; 0，与 H1 无关）。<b>H2 仍不可判</b>（缺 gru_s42 与其余两档），
且本结果<b>只有 1 个 seed（n_seeds=1），不满足预注册的 ≥2 seed</b>。''',
'''两个 seed（42 / 2024）都跑完并出预测、回测、判词：<b>H1 可判</b>——逐 seed Δ_drop = {PS42:.2%} / {PS24:.2%}，
<b>两 seed 中位数口径 {DD:.2%}</b>，落在「部分复现」带（不进 [−40%,−15%]、不退 [−10%,+10%]，但离 −10% 只差 {MARGIN:.2f}pp）；
H3 判<b>退</b>（两 seed 净 SR 中位数 {SR50:.4f} &lt; 0，与 H1 无关）；
<b>H2 在 seed 2024 上可判</b>：ΔRankIC {H2P:+.4f}，95% CI [{H2LO:+.4f}, {H2HI:+.4f}]，<b>不含 0</b>。
seed 42 缺 GRU 基线（<code>gru_s42</code> 从未成功），故 H2 只有 1/2 seed。''')

# ---------- D. 表 0：按 seed 渲染 ----------
rep('''<tr><td>PRISM-VQ full</td><td class="num">{F_IC:.4f}</td><td class="num">{F_IR:.4f}</td><td class="num">{F_SR:.4f}</td>
<td class="num">{F_AR:.2%}</td><td class="num">{F_MDD:.4f}</td><td class="num">{F_TO:.4f}</td><td class="num">28（早停）</td></tr>
<tr><td>PRISM-VQ no_vq</td><td class="num">{N_IC:.4f}</td><td class="num">{N_IR:.4f}</td><td class="num">{N_SR:.4f}</td>
<td class="num">{N_AR:.2%}</td><td class="num">{N_MDD:.4f}</td><td class="num">{N_TO:.4f}</td><td class="num">早停</td></tr>''',
'''{SEED_ROWS}''')

rep('''def pct(a, b_):''',
'''def _rows_by_seed(runs):
    """按 verdict.runs 里实际存在的 seed 逐行渲染（full / no_vq 成对）。"""
    out = []
    seeds = sorted({k.rsplit("_s", 1)[-1] for k in runs if "_s" in k})
    for sdd in seeds:
        for arm in ("full", "no_vq"):
            r = runs.get(f"{arm}_s{sdd}")
            if not r:
                continue
            out.append(
                f'<tr><td>PRISM-VQ {arm}（s{sdd}）</td>'
                f'<td class="num">{float(r.get("rankic") or 0):.4f}</td>'
                f'<td class="num">{float(r.get("rankicir") or 0):.4f}</td>'
                f'<td class="num">{float(r.get("sr") or 0):.4f}</td>'
                f'<td class="num">{float(r.get("ar") or 0):.2%}</td>'
                f'<td class="num">{float(r.get("mdd") or 0):.4f}</td>'
                f'<td class="num">{float(r.get("turnover") or 0):.4f}</td>'
                f'<td class="num">早停</td></tr>')
    return "\\n".join(out)


def pct(a, b_):''')

rep('''F = _runs.get("full_s42", {})''', '''SEED_ROWS = _rows_by_seed(_runs)
F = _runs.get("full_s42", {})''')

# ---------- E. 对照表 B 的三行 ----------
rep('''<tr><td><b>H1 Δ_drop</b></td><td class="num">−21.64%</td><td class="num">未取到</td><td class="num">—</td>
<td><b>不可判</b>：full 与 no_vq 一个都没出</td></tr>
<tr><td>H2 vs GRU 增量 CI</td><td class="num">见 503 §H2</td><td class="num">未取到</td><td class="num">—</td>
<td><b>不可判</b>：缺 full 档</td></tr>
<tr><td>H3 净 SR（8/30 bps）</td><td class="num">见 503</td><td class="num">未取到</td><td class="num">—</td>
<td><b>不可判</b>：缺四档预测</td></tr>''',
'''<tr><td><b>H1 Δ_drop（同 seed 42）</b></td><td class="num">−17.59%</td><td class="num">{PS42:.2%}</td>
<td class="num">{PS42 + 0.17585:+.2%}</td>
<td><b>方向一致、幅度变弱</b>。<b>注意基准口径</b>：503 的<b>两 seed 合并中位数</b>是 −21.64%，
本行用的是 503 的 <b>s42 单 seed 值 −17.59%</b>（<code>H1.delta_ic_per_seed["42"]</code>）；
拿中位数跟本单单 seed 比会把幅度虚高近一倍（本报告 v1 版即此错，已更正）。</td></tr>
<tr><td>H2 vs GRU 增量 CI</td><td class="num">见 503 §H2</td>
<td class="num">{H2P:+.4f} [{H2LO:+.4f}, {H2HI:+.4f}]</td><td class="num">—</td>
<td><b>可判（s2024）</b>：10,000 次 block bootstrap（block=5）95% CI <b>不含 0</b>；s42 缺 <code>gru_s42</code></td></tr>
<tr><td>H3 净 SR（8/30 bps）</td><td class="num">见 503</td><td class="num">{SR50:.4f}（两 seed 中位）</td>
<td class="num">—</td><td><b>可判</b>：判退（&lt;0）；<b>已独立复算 0 mismatch @1e-6</b></td></tr>''')

# ---------- F. §3 表：H2 / H3 行 ----------
rep('''<td><code>full_*</code> 两份 + <code>gru_*</code> 两份（后者本单有 1 份）</td><td class="st red">0/2 → 不可判</td></tr>''',
'''<td><code>full_*</code> 两份 + <code>gru_*</code> 两份（后者本单有 1 份）</td>
<td class="st teal">1/2 → <b>seed 2024 可判</b>：ΔRankIC {H2P:+.4f}，95% CI [{H2LO:+.4f}, {H2HI:+.4f}]（不含 0）；seed 42 缺 gru_s42</td></tr>''')

rep('''<td class="st red">2/8 → seed 42 两档可判：净 SR {F_SR:.3f} / {N_SR:.3f}，<b>判退</b></td></tr>''',
'''<td class="st teal">4/8 → 两 seed 的 full/no_vq 可判：净 SR 中位 {SR50:.4f}，<b>判退</b>；
<b>已独立复算 0 mismatch @1e-6</b></td></tr>''')

# ---------- G. §8 不确定项：lookahead 真实状态 ----------
rep('''<h3>8.1 不确定项</h3>
<ul>''',
'''<li><b>lookahead 检查仍不可用（已修到能跑完，但未通过）</b>：修掉 <code>KeyError: sh600001</code>
（qlib 二进制缺票 → 现按候选序跳过并记 <code>skipped_symbols</code>）后能完整跑完，结果是
标签轴 <code>label_max_abs_diff = 7.4e-9</code>（通过），但 <code>feature_date_mismatches = 1,997,525 / 2,327,786</code>、
<code>prior_mismatches = 2,617,126 / 2,625,423</code>，最大绝对差 1.35–4.81 且呈<b>常量偏移</b> ——
更像两套归一化口径不一致而非日期错位。<b>所以不能据此声称「无前视」</b>；影响范围：H1/H3 建在同一批特征上，
若特征侧真有前视则所有档同受影响，本检查既不能证实也不能证伪。</li>
<li><b>integrity I4 仍未通过（不影响测试段）</b>：5,798 条 violations 全部落在
2012-01-04 起的头 20 个交易日（≈300 只 × 20 日），根因是该检查读的是 WORK-503 时代的新浪快照
（起点 2012），与 quantdb 面板（2006 起点）不一致；2022–2024 测试段<b>零条</b>。
I3 已修：两份日历日期完全相同，只是 <code>2006-01-04</code> vs <code>20060104</code> 的格式差异，
按日期比较后 <code>identical=true</code>。</li>
<h3>8.1 不确定项</h3>
<ul>
<li><b>实测只覆盖 stage-1、前 10 轮</b>''') 

io.open(p, "w", encoding="utf-8").write(s)
print("patched v8")
