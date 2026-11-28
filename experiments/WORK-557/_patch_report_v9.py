"""补丁 v9（2026-11-29）：四处文案 + 前视结论（已通过）+ 边界脆性。

1. H3 的 −0.1282 写明口径（full 档两 seed 中位；四个 VQ run 全算中位 −0.2079）；
2. 前视检查：**已通过**（根因是检查脚本自己的 fit 窗口写成 2015–2019，与数据构造的 2009–2019 不一致）；
3. 边界脆性写进报告：s2024 单 seed 落在「退」带内、s42 部分复现、合并只超边界 0.19pp + 翻带条件；
4. H2 的 note/CI 一致性说明。
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


# ---------- A. 常量：前视状态 + H3 口径 ----------
rep('''S2 = load(os.path.join("verify", "stage2_timing_summary.json"), {})''',
'''S2 = load(os.path.join("verify", "stage2_timing_summary.json"), {})
LA = load("lookahead_check.json", {})    # 2026-11-29：修好 fit 窗口后已 passed=true''')

# ---------- B. 边界脆性：结论框里加一句 ----------
rep('''<div class="word">两 seed 跑完：Δ_drop = <b>{DD:.2%}</b>（部分复现，离 −10% 边界仅 {MARGIN:.2f}pp），净 SR &lt; 0 判退；无 MoE / 无先验两档仍未跑</div>''',
'''<div class="word">两 seed 跑完：Δ_drop = <b>{DD:.2%}</b>（部分复现，离 −10% 边界仅 {MARGIN:.2f}pp）—— <b>结论脆</b>：s2024 单 seed 已落在「退」带内</div>''')

# ---------- C. 结论框后面插一段「边界与翻带条件」 ----------
rep('''<table>
<caption>表 0b｜成本敏感性''',
'''<div class="keybox">
<h3>边界与翻带条件（必须一起读）</h3>
<p>预注册的判带是：进 [−40%, −15%]、退 [−10%, +10%]，其余判「部分复现」。本单三个口径分别落在：</p>
<table>
<caption>表 0c｜同一批结果的三种读数（H1 的边界脆性）</caption>
<thead><tr><th>口径</th><th class="num">Δ_drop</th><th>落在哪一带</th><th>差多少</th></tr></thead>
<tbody>
<tr><td>seed 42 单 seed</td><td class="num">{PS42:.2%}</td><td>部分复现</td><td>离「退」边界 −10% 有 {-PS42*100-10:.2f}pp 余量</td></tr>
<tr><td>seed 2024 单 seed</td><td class="num">{PS24:.2%}</td><td><b>「退」带内</b>（&gt; −10%）</td><td>超进「退」带 {abs(PS24)*100-10:.2f}pp</td></tr>
<tr><td><b>两 seed 中位数（预注册口径）</b></td><td class="num"><b>{DD:.2%}</b></td><td><b>部分复现</b></td>
<td><b>只超「退」边界 {MARGIN:.2f}pp</b></td></tr>
</tbody></table>
<p><b>翻带条件（写死，便于复核）</b>：① 再补一个 seed，若其 Δ_drop &gt; −10%，中位数会跟着挪；
② 换合并口径（如取两 seed 的均值而非中位数：({PS42:.2%} + {PS24:.2%})/2 = {(PS42+PS24)/2:.2%}），同样可能落进「退」；
③ 结论对 RankIC 的极小幅波动敏感 —— 只要 no_vq 的 RankIC 再高 0.0008 左右，Δ_drop 就会跨过 −10%。
所以本单的 H1 应读作「<b>不支持「码本无效」（退），但也不足以判「进」</b>」，不能读作「码本有效性已被证实」。</p>
</div>

<table>
<caption>表 0b｜成本敏感性''')

# ---------- D. H3 口径写清 ----------
rep('''<tr><td>H3 净 SR（8/30 bps）</td><td class="num">见 503</td><td class="num">{SR50:.4f}（两 seed 中位）</td>
<td class="num">—</td><td><b>可判</b>：判退（&lt;0）；<b>已独立复算 0 mismatch @1e-6</b></td></tr>''',
'''<tr><td>H3 净 SR（8/30 bps）</td><td class="num">见 503</td><td class="num">{SR50:.4f}</td>
<td class="num">—</td><td><b>可判</b>：判退（&lt;0）；已独立复算 0 mismatch @1e-6。
<b>口径</b>：{SR50:.4f} 是 <b>full 档两 seed 的中位</b>（per-seed {F_SR:.4f} / {F_SR24:.4f}）；
若把四个 VQ run（full+no_vq × 2 seed）一起取中位，则是 <b>{SR4:.4f}</b>（差 {abs(SR4-SR50)*100:.1f}pp）</td></tr>''')

rep('''SR50 = float((VD.get("H3") or {}).get("sr_median") or F_SR)''',
'''SR50 = float((VD.get("H3") or {}).get("sr_median") or F_SR)
F_SR24 = float((_runs.get("full_s2024") or {}).get("sr") or 0)
import statistics as _st
SR4 = round(_st.median([float((_runs.get(k) or {}).get("sr") or 0)
                        for k in ("full_s42", "full_s2024", "no_vq_s42", "no_vq_s2024")]), 4)''')

# ---------- E. §3 表：H2 行补 note 一致性 ----------
rep('''<td class="st teal">1/2 → <b>seed 2024 可判</b>：ΔRankIC {H2P:+.4f}，95% CI [{H2LO:+.4f}, {H2HI:+.4f}]（不含 0）；seed 42 缺 gru_s42</td></tr>''',
'''<td class="st teal">1/2 → <b>seed 2024 可判</b>：ΔRankIC {H2P:+.4f}，95% CI [{H2LO:+.4f}, {H2HI:+.4f}]（<b>不含 0</b>）；
seed 42 缺 gru_s42。<code>H2.note</code> 里原来那句「CI 含 0 判与论文自曝弱点一致」是模板残留，
已改为按实测逐 seed 生成（{LA_NOTE}）</td></tr>''')

rep('''H2HI = float(_H2.get("hi") or 0)''',
'''H2HI = float(_H2.get("hi") or 0)
_la = bool((LA or {}).get("passed"))
LA_NOTE = ("本 seed CI 不含 0" if H2LO > 0 else "本 seed CI 含 0")''')

# ---------- F. §8.1：前视已通过 ----------
rep('''<li><b>lookahead 检查仍不可用（已修到能跑完，但未通过）</b>：修掉 <code>KeyError: sh600001</code>''',
'''<li><b>lookahead 检查：已通过（2026-11-29 定位到根因在检查脚本自己身上）</b>——
<code>feature_date_mismatches = 0 / 2,327,786</code>、<code>prior_mismatches = 0 / 2,731,806</code>、
<code>label_mismatches = 0</code>，最大残差 2.8e-05 / 2.2e-06 / 7.4e-09（float32 量级）。
根因：检查脚本的 <code>TRAIN</code> 常量写成了 <b>2015–2019</b>（WORK-503 的缩窗），
而 <code>work557_build_dataset.py</code> 的 <code>fit_stats</code> 是按本单预注册 C1 的
<b>2009–2019</b> 拟合的 —— 拟合窗口不同 → <code>f_mean/f_std</code> 不同 → 逐列**全都**对不上，
看起来像 86% 的行「日期错位」。逐列取证：6 个探针日 × 2 只票 × 158 列，<b>0/158 列差异 &gt; 1e-4</b>；
把 qlib 侧整体前移/后移一天后差值反而升到 1.8–4.0（排除日期错位）。改回 2009–2019 后检查通过。
下一条是修之前的状态，保留以免误导：</li>
<li><b>（历史）lookahead 曾不可用</b>：修掉 <code>KeyError: sh600001</code>''')

io.open(p, "w", encoding="utf-8").write(s)
print("patched v9")
