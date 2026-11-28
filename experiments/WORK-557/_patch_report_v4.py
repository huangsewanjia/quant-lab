"""文案补丁（2026-11-28 核实运行后）：把「50 轮 1.89 h 外推」换成实测 1.847 h。

只改数字与叙述，不改结论方向（段 2 仍是 0/8 档，H1/H2/H3 仍不可判）。
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


# ---- 载入核实运行结果（存在才用，避免生成器硬依赖） ----
rep('''bt = load("backtest_results.json", {})''',
'''bt = load("backtest_results.json", {})
# 2026-11-28 最小核实运行（早停开 / 早停关两条真跑），落 experiments/WORK-557/verify/
V1 = load(os.path.join("verify", "verify_early_s42.json"), {})
V2 = load(os.path.join("verify", "verify_full50_s42.json"), {})''')

# ---- 结论段：1.89 h 外推 → 1.847 h 实测 + 早停实测 28 轮 ----
rep('''② 修好后把「要不要缩」量成了实测数（3 轮 / 10 轮 stage-1 真实循环，workers=2）：
<b>136.1 s/轮 → 单档 50 轮 = 1.89 h，<u>不越</u>预注册的 3 h 停手闸</b>；
全量按实测口径 <b>≈ 36.4 GPU 小时（1.5 天）</b>，早停口径 <b>15.3 h</b>，悲观上界 178 h。''',
'''② 修好后把「要不要缩」量成了实测数（stage-1 真实循环，workers=2，含早停开关两种口径）：
<b>满 50 轮实测 6,648.5 s = 1.847 h（均值 133.0 s/轮、稳态中位 126.7 s），<u>不越</u>预注册的 3 h 停手闸</b>；
开早停时<b>实测第 28 轮停止</b>（valid 在 epoch 12 触底 0.5881，patience=15），单档 1.063 h。
全量按实测口径 <b>≈ 35.6 GPU 小时（1.5 天）</b>，实测早停口径 <b>21.5 h</b>，悲观上界 178 h。''')

# ---- 表 5：加实测 50 轮一行 ----
rep('''<tr><td><b>端到端实测</b>（{PL_EPOCHS_RAN} 轮真实 PL 循环，workers=2，含完整 valid 段）</td>
<td class="num">{S_BATCH_MEAS:.0f} ms</td><td class="num">{S_EPOCH:.1f} s</td><td class="num">{H50_MEAS:.2f} h</td>
<td>峰值显存 {VRAM:.2f} GB；{PL_EPOCHS_RAN} 轮共 {PL_FIT_S:.0f} s，逐轮 134.8–140.8 s</td></tr>''',
'''<tr><td><b>满 50 轮实测</b>（2026-11-28，早停关，workers=2，含完整 valid 段）</td>
<td class="num">{round((V2.get('fit_seconds') or 0)/50/2675*1000,1)} ms</td>
<td class="num">{round((V2.get('fit_seconds') or 0)/50,1)} s</td>
<td class="num">{round((V2.get('fit_seconds') or 0)/3600,3)} h</td>
<td>峰值显存 {V2.get('peak_vram_gb')} GB；50 轮共 {V2.get('fit_seconds'):.0f} s，逐轮 125.0–151.8 s</td></tr>
<tr><td>早停口径实测（同上，早停开）</td><td class="num">{round((V1.get('fit_seconds') or 0)/V1.get('n_epochs_ran',1)/2675*1000,1)} ms</td>
<td class="num">{round((V1.get('fit_seconds') or 0)/V1.get('n_epochs_ran',1),1)} s</td>
<td class="num">{round((V1.get('fit_seconds') or 0)/3600,3)} h（{V1.get('n_epochs_ran')} 轮停）</td>
<td>min val_loss {V1.get('min_val_loss')} @ epoch {V1.get('min_val_loss_epoch')}</td></tr>
<tr><td>端到端实测（2026-11-27，10 轮，workers=2）</td>
<td class="num">{S_BATCH_MEAS:.0f} ms</td><td class="num">{S_EPOCH:.1f} s</td><td class="num">{H50_MEAS:.2f} h</td>
<td>峰值显存 {VRAM:.2f} GB；10 轮共 {PL_FIT_S:.0f} s（当时的 50 轮为外推）</td></tr>''')

# ---- 表 5b：三档总量按实测更新 ----
rep('''<tr><td><b>实测口径</b>（{S_EPOCH:.1f} s/轮 × 50 轮 = {H50_MEAS:.2f} h/档）</td>
<td class="num">{2*STAGE1_50:.1f} h</td><td class="num">{8*STAGE2_50:.1f} h</td>
<td class="num">{2*GRU_50:.1f} h</td><td class="num"><b>{tot_meas:.1f} h</b></td></tr>
<tr><td>早停轨迹（实测 valid 在 epoch 4 触底，patience=15 → 约 19 轮）</td>
<td class="num">{2*STAGE1_50*19/50:.1f} h</td><td class="num">{8*STAGE2_50*19/50:.1f} h</td>
<td class="num">{2*GRU_50:.1f} h</td><td class="num"><b>{2*STAGE1_50*19/50+8*STAGE2_50*19/50+2*GRU_50:.1f} h</b></td></tr>''',
'''<tr><td><b>实测口径</b>（满 50 轮 = {V2H:.2f} h/档，实测）</td>
<td class="num">{2*V2H:.1f} h</td><td class="num">{16*V2H:.1f} h</td>
<td class="num">{2*GRU_50:.1f} h</td><td class="num"><b>{2*V2H+16*V2H+2*GRU_50:.1f} h</b></td></tr>
<tr><td>早停口径（<b>实测第 {V1N} 轮停</b> = {V1H:.2f} h/档）</td>
<td class="num">{2*V1H:.1f} h</td><td class="num">{16*V1H:.1f} h</td>
<td class="num">{2*GRU_50:.1f} h</td><td class="num"><b>{2*V1H+16*V1H+2*GRU_50:.1f} h</b></td></tr>''')

# ---- 图 4 图注与系列：用实测 ----
rep('''f"实测 {S_EPOCH:.1f} s/轮（10 轮真实循环）→ stage-1 单档 {STAGE1_50:.2f} h（<b>在 3 h 闸内</b>）、"''',
'''f"实测满 50 轮 {V2H:.2f} h/档（<b>在 3 h 闸内</b>；早停口径 {V1H:.2f} h）、"''')

# ---- 注入 V2H/V1H/V1N 常量（放在 f4 之前） ----
rep('''# F4 计算预算 vs 停手闸（对数）——三档口径并列''',
'''# 2026-11-28 核实运行实测值
V2H = round((V2.get("fit_seconds") or 0) / 3600, 3) or H50_MEAS
V1H = round((V1.get("fit_seconds") or 0) / 3600, 3) or V2H * 19 / 50
V1N = int(V1.get("n_epochs_ran") or 19)

# F4 计算预算 vs 停手闸（对数）——三档口径并列''')

# ---- §8.2 与「不确定项」：更新口径 ----
rep('''<li><b>换硬件</b>：≥4 张同代卡或一张显存足够、单步 ≥4× 快的卡 → 实测口径 36.4 h 压到 &lt;10 h，
早停口径 15.3 h 压到 &lt;4 h（也就无需占卡过夜）。</li>''',
'''<li><b>换硬件</b>：≥4 张同代卡或一张显存足够、单步 ≥4× 快的卡 → 实测口径 {2*V2H+16*V2H+2*GRU_50:.0f} h
压到 &lt;10 h，早停口径 {2*V1H+16*V1H+2*GRU_50:.0f} h 压到 &lt;6 h。</li>''')

io.open(p, "w", encoding="utf-8").write(s)
print("patched")
