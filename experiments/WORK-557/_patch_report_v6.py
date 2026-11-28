"""补丁 v6（2026-11-28 晚）：把 seed 42 两档的**真实结果**写进报告。

之前报告说「H1/H2/H3 全部不可判」——现在 seed 42 的 full 与 no_vq 都有了预测与回测，
Δ_drop 出来了（−13.83%，落在「部分复现」带）。这一段必须改，否则又是「新数字没贯穿」。
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


# ---------- 载入 seed42 结果 ----------
rep('''S2 = load(os.path.join("verify", "stage2_timing_summary.json"), {})''',
'''S2 = load(os.path.join("verify", "stage2_timing_summary.json"), {})
VD = load("verdict.json", {})            # seed 42 的判词（2026-11-28 晚）
CS = {}                                  # 成本点：从三个 backtest json 现读
for _tag, _fn in (("gross", "backtest_gross_diagnostic.json"),
                  ("single8", "backtest_cost_8bps.json"),
                  ("double30", "backtest_cost_30bps.json")):
    CS[_tag] = load(_fn, {})''')

# ---------- 结论框：从「全部不可判」改成「seed42 已出」 ----------
rep('''<div class="word">段 2 未达成：四档 PRISM-VQ × 2 seed = 8 次尝试，<b>0 次进入有效训练</b></div>''',
'''<div class="word">seed 42 两档已跑出：Δ_drop = <b>−13.83%</b>（部分复现），净 SR &lt; 0 判退；其余三档仍未跑</div>''')

rep('''③ 唯一跑完的是 GRU 基线 s2024（50 轮 / 4,310 s），因此 <b>H1/H2/H3 全部不可判</b> —— 不是「跑了不支持」，
是「没跑出来」。本报告不替它们下判词。''',
'''③ 2026-11-28 晚补跑 <code>full / seed 42</code> 与 <code>no_vq / seed 42</code>（各含 stage-1 + stage-2），
预测与回测已出，<b>H1 可判</b>：Δ_drop = <b>{DD:.2%}</b>，落在「部分复现」带（不进 [−40%,−15%] 也不退 [−10%,+10%]）；
H3 仍判<b>退</b>（净 SR {SR50:.3f} &lt; 0，与 H1 无关）。<b>H2 仍不可判</b>（缺 gru_s42 与其余两档），
且本结果<b>只有 1 个 seed（n_seeds=1），不满足预注册的 ≥2 seed</b>。''')

# 在结论框之后插入 seed42 明细小节
rep('''<h2><span class="num">1</span>现场取证：计划任务到底跑出了什么</h2>''',
'''<div class="keybox">
<h3>seed 42 的结果（2026-11-28 晚补跑，n_seeds = 1）</h3>
<table>
<caption>表 0｜seed 42 两档：预测质量（RankIC 由 test 段 2022–2024 计算）与组合表现（5/15 bps 主口径）。</caption>
<thead><tr><th>档</th><th class="num">RankIC</th><th class="num">RankICIR</th><th class="num">净 SR</th>
<th class="num">年化 AR</th><th class="num">MDD</th><th class="num">日均单边换手</th><th class="num">实际轮次</th></tr></thead>
<tbody>
<tr><td>PRISM-VQ full</td><td class="num">{F_IC:.4f}</td><td class="num">{F_IR:.4f}</td><td class="num">{F_SR:.4f}</td>
<td class="num">{F_AR:.2%}</td><td class="num">{F_MDD:.4f}</td><td class="num">{F_TO:.4f}</td><td class="num">28（早停）</td></tr>
<tr><td>PRISM-VQ no_vq</td><td class="num">{N_IC:.4f}</td><td class="num">{N_IR:.4f}</td><td class="num">{N_SR:.4f}</td>
<td class="num">{N_AR:.2%}</td><td class="num">{N_MDD:.4f}</td><td class="num">{N_TO:.4f}</td><td class="num">早停</td></tr>
<tr><td><b>Δ_drop</b>（H1）</td><td class="num" colspan="7"><b>{DD:.2%}</b> —— 预注册带：进 [−40%,−15%] / 退 [−10%,+10%]，
其余判「部分复现」→ <b>部分复现</b>（论文 Table 2 的 Δ_formula = 26.93%，即 Δ_drop = −26.93%，本单弱了 {GAP:.1f} 个百分点）</td></tr>
</tbody></table>

<table>
<caption>表 0b｜成本敏感性（seed 42，同一组预测只换成本点）。0 bps 是事后诊断，不入预注册。</caption>
<thead><tr><th>成本点</th><th class="num">full 净 SR</th><th class="num">no_vq 净 SR</th><th class="num">full 年化</th><th class="num">no_vq 年化</th></tr></thead>
<tbody>
<tr><td>0 bps（事后诊断）</td><td class="num">{G_FSR:.4f}</td><td class="num">{G_NSR:.4f}</td><td class="num">{G_FAR:.2%}</td><td class="num">{G_NAR:.2%}</td></tr>
<tr><td>单边 8 bps（预注册敏感性）</td><td class="num">{E_FSR:.4f}</td><td class="num">{E_NSR:.4f}</td><td class="num">{E_FAR:.2%}</td><td class="num">{E_NAR:.2%}</td></tr>
<tr><td>买 5 / 卖 15 bps（主口径）</td><td class="num">{F_SR:.4f}</td><td class="num">{N_SR:.4f}</td><td class="num">{F_AR:.2%}</td><td class="num">{N_AR:.2%}</td></tr>
<tr><td>双边 30 bps（预注册敏感性）</td><td class="num">{D_FSR:.4f}</td><td class="num">{D_NSR:.4f}</td><td class="num">{D_FAR:.2%}</td><td class="num">{D_NAR:.2%}</td></tr>
</tbody></table>
<p class="note">读法：<b>毛利（0 bps）是正的</b>（full SR {G_FSR:.3f} / no_vq {G_NSR:.3f}），
到 8 bps 就转负 —— 与 WORK-503 同型：信号有，但被日均 16.7% 的换手吃掉了。
主口径下 full 净 SR {F_SR:.3f} &lt; 0，按预注册 <b>H3 判退</b>（与 H1 无关，直接判退）。</p>
</div>

<h2><span class="num">1</span>现场取证：计划任务到底跑出了什么</h2>''')

# 结论框里用到的变量（放在 f-string 之前的常量区）
rep('''# 2026-11-28 核实运行实测值''',
'''# 2026-11-28 seed42 结果（现读产物，不手抄）
_H1 = (VD.get("H1") or {})
DD = float(_H1.get("delta_drop") or 0)
GAP = abs(float(_H1.get("paper_delta_formula") or 0.2693) * 100 - abs(DD) * 100)
_runs = (VD.get("runs") or {})
F = _runs.get("full_s42", {})
N = _runs.get("no_vq_s42", {})
F_IC, F_IR, F_SR = float(F.get("rankic") or 0), float(F.get("rankicir") or 0), float(F.get("sr") or 0)
F_AR, F_MDD, F_TO = float(F.get("ar") or 0), float(F.get("mdd") or 0), float(F.get("turnover") or 0)
N_IC, N_IR, N_SR = float(N.get("rankic") or 0), float(N.get("rankicir") or 0), float(N.get("sr") or 0)
N_AR, N_MDD, N_TO = float(N.get("ar") or 0), float(N.get("mdd") or 0), float(N.get("turnover") or 0)
SR50 = float((VD.get("H3") or {}).get("sr_median") or F_SR)
def _cs(tag, key):
    return float(((CS.get(tag) or {}).get(key) or {}).get("sr") or 0)
def _car(tag, key):
    return float(((CS.get(tag) or {}).get(key) or {}).get("ar") or 0)
G_FSR, G_NSR = _cs("gross", "full_s42"), _cs("gross", "no_vq_s42")
G_FAR, G_NAR = _car("gross", "full_s42"), _car("gross", "no_vq_s42")
E_FSR, E_NSR = _cs("single8", "full_s42"), _cs("single8", "no_vq_s42")
E_FAR, E_NAR = _car("single8", "full_s42"), _car("single8", "no_vq_s42")
D_FSR, D_NSR = _cs("double30", "full_s42"), _cs("double30", "no_vq_s42")
D_FAR, D_NAR = _car("double30", "full_s42"), _car("double30", "no_vq_s42")

# 2026-11-28 核实运行实测值''')

# ---------- §3 主检验节标题与表：H1 可判 ----------
rep('''<h2><span class="num">3</span>段 3 主检验：H1 / H2 / H3 全部「不可判」</h2>''',
'''<h2><span class="num">3</span>段 3 主检验：H1 已可判（seed 42），H2 仍不可判</h2>''')

rep('''<tr><td>H1</td><td>Δ_drop = (RankIC_no_vq − RankIC_full) / RankIC_full，取两 seed 中位数</td>
<td><code>full_{{s42,s2024}}</code>、<code>no_vq_{{s42,s2024}}</code> 四份预测</td><td class="st red">0/4 → 不可判</td></tr>''',
'''<tr><td>H1</td><td>Δ_drop = (RankIC_no_vq − RankIC_full) / RankIC_full，取两 seed 中位数</td>
<td><code>full_{{s42,s2024}}</code>、<code>no_vq_{{s42,s2024}}</code> 四份预测</td>
<td class="st teal">2/4 → <b>seed 42 可判：{DD:.2%}（部分复现）</b>；缺 s2024，未达 ≥2 seed</td></tr>''')

rep('''<tr><td>H3</td><td>净 SR（8 / 30 bps 两档）、AR、MDD、换手；净 SR &lt; 0 直接判退</td>
<td>四档 × 成本档的回测产物</td><td class="st red">0/8 → 不可判</td></tr>''',
'''<tr><td>H3</td><td>净 SR（8 / 30 bps 两档）、AR、MDD、换手；净 SR &lt; 0 直接判退</td>
<td>四档 × 成本档回测产物</td>
<td class="st red">2/8 → seed 42 两档可判：净 SR {F_SR:.3f} / {N_SR:.3f}，<b>判退</b></td></tr>''')

io.open(p, "w", encoding="utf-8").write(s)
print("patched v6")
