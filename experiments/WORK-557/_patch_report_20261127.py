"""一次性补丁：把 2026-11-27 的 10 轮实测结果写回报告生成器。

不参与运行（补完即删/留档均可）。
"""
import io
import os

p = os.path.join(os.path.dirname(__file__), "..", "..", "scripts",
                 "work557_report_stage34.py")
p = os.path.abspath(p)
s = io.open(p, encoding="utf-8").read()


def rep(old, new):
    global s
    assert old in s, "NOT FOUND: " + old[:70]
    s = s.replace(old, new, 1)


F2_OLD = '''"s2024 跑满 50 轮（4,310 s）：valid loss 自 epoch 1 起单调回升 1.0012 → 1.2896，train loss 一路降到 0.7094，"
        "典型过拟合。s42（虚线）在第 43 轮被 7,200 s 硬闸杀掉。<b>关键读数</b>：按上游 patience=15 的早停，"
        "这条曲线会在 epoch 16 左右自己停 —— 「≤6 轮」的上限并非省时装置，而是把曲线截断在触底之前。"'''
F2_NEW = '''"<b>曲线取自单条 run（s2024），不与 s42 混列</b>：s2024 跑满 50 轮（<code>training_log.json</code> 记 4,346 s；"
        "epoch 49 累计 4,310 s，报告用后者作「训练段墙钟」），valid loss 在 <b>epoch 0 触底 1.0104</b>，"
        "此后单调回升至 1.2896（epoch 1 = 1.0109、8 = 1.1088、21 = 1.2001），train loss 一路降到 0.7094 —— 典型过拟合。"
        "s42（虚线，仅作参照）第 43 轮被 7,200 s 硬闸杀掉。<b>关键读数</b>：按上游 patience=15 的早停，"
        "这条曲线会在 epoch 16 左右自己停 —— 「≤6 轮」的上限并非省时装置，而是把曲线截断在触底之前。"'''
rep(F2_OLD, F2_NEW)

T2_OLD = '''<p>修完后 B1/B2 都消失（<code>train batches/epoch: 2675</code> 正常打印、worker 正常起），
但暴露出第三个问题：<b>fit 结束后进程不退出</b>。2026-11-27 的两次复现都是
「epoch 计时显示训练只用了 1.5 s / 4.5 s，进程却挂到 240 s / 900 s 才被 timeout 杀掉」——
即 <code>persistent_workers=True</code> 在 Windows spawn 下的 teardown 死锁。
这个缺陷会让每个档在收尾时白挂十几分钟，<b>也是 11-25 那轮 run 被「单档 > 7,200 s」误杀的同类原因</b>。
本报告把它列为已知缺陷，未在本次修复（修复需改适配层的 loader 收尾路径，属下一轮工作）。</p>'''
T2_NEW = '''<p>修完后 B1/B2 都消失（<code>train batches/epoch: 2675</code> 正常打印、worker 正常起），
但 2026-11-27 的标定脚本仍出现「epoch 计时只有 1.5–4.5 s、进程却挂到 240–900 s 才被杀」。
本轮用 <code>faulthandler</code> 取栈定位，结论与上一版报告写的「teardown 死锁」<b>不同，此处更正</b>：</p>

<table>
<caption>表 2b｜「挂住」与「硬退」的三个真实原因（2026-11-27 实测，均已定位）</caption>
<thead><tr><th>现象</th><th>证据</th><th>真实原因</th><th>处置</th></tr></thead>
<tbody>
<tr><td>标定脚本跑完 epoch 0 后长时间无输出、被 timeout 杀</td>
<td>faulthandler 栈停在 <code>popen_spawn_win32.__init__</code>，即仍在 spawn worker</td>
<td><b>worker spawn 本身慢</b>（8 worker 共 121 s），不是 teardown</td><td>降并发</td></tr>
<tr><td>epoch 末长期停留</td><td>栈里有 <code>wandb/sdk/lib/asyncio_manager.py</code> 的事件循环线程</td>
<td>上游 <code>train_vqvae.py:97</code> 每轮末调用 <code>wandb.log(wandb.Image(plt))</code>；
标定脚本没像生产路径那样 <code>wandb.init(mode="disabled")</code>，wandb 试图联网挂住</td>
<td>标定脚本补 <code>wandb.init(mode="disabled")</code>（生产路径本来就有）</td></tr>
<tr><td><code>num_workers=8</code> 时进程硬退（RC=127）或卡在 epoch 0→1</td><td>进程直接消失，无 Python traceback</td>
<td>8 worker spawn 在这台机器上不稳定（3 次尝试：1 次成功但 spawn 用 121 s，2 次硬退）</td>
<td>定价改用 <code>num_workers=2</code>：10 轮 1,411 s 跑完并干净退出（RC=0）</td></tr>
</tbody></table>

<p><b>结论</b>：<code>persistent_workers</code> 在 Windows 下的 teardown 死锁<b>未在本轮复现</b> —— 上一版报告把它写成
死锁是误判（那次失败的 harness 是我自己的标定脚本，缺 <code>wandb.init</code>）。生产路径（<code>run_arms</code>）
在 <code>num_workers=2</code> 下 10 轮跑完并正常退出；真正要治的是 <b>8 worker 的 spawn 不稳定</b>，
下一轮起训一律用 <code>--workers 2</code>。</p>'''
rep(T2_OLD, T2_NEW)

T5_OLD = '''<caption>表 5｜单轮/总量测算（训练段 2,675 个交易日 = 2,675 个 batch）。「真实循环」口径取 90 ms/step。</caption>
<thead><tr><th>项</th><th class="num">单步</th><th class="num">单轮</th><th class="num">50 轮</th><th>备注</th></tr></thead>
<tbody>
<tr><td>PRISM-VQ stage-1（真实循环）</td><td class="num">90 ms</td><td class="num">241 s</td><td class="num">3.35 h</td><td>越 3 h 闸</td></tr>
<tr><td>PRISM-VQ stage-1（微基准上界）</td><td class="num">264 ms</td><td class="num">706 s</td><td class="num">9.8 h</td><td>含数据+模型</td></tr>
<tr><td>PRISM-VQ stage-2</td><td class="num">未实测</td><td class="num">未实测</td><td class="num">未实测</td><td>一次都没跑进去</td></tr>
<tr><td>GRU 基线（实测）</td><td class="num">—</td><td class="num">86 s</td><td class="num">4,310 s（实测）</td><td>s2024</td></tr>
</tbody></table>'''
T5_NEW = '''<caption>表 5｜单轮成本：三个口径并列（训练段 2,675 batch/轮 + valid 486 batch/轮）。</caption>
<thead><tr><th>口径</th><th class="num">单 batch</th><th class="num">单轮</th><th class="num">50 轮</th><th>说明</th></tr></thead>
<tbody>
<tr><td><b>端到端实测</b>（{PL_EPOCHS_RAN} 轮真实 PL 循环，workers=2，含完整 valid 段）</td>
<td class="num">{S_BATCH_MEAS:.0f} ms</td><td class="num">{S_EPOCH:.1f} s</td><td class="num">{H50_MEAS:.2f} h</td>
<td>峰值显存 {VRAM:.2f} GB；{PL_EPOCHS_RAN} 轮共 {PL_FIT_S:.0f} s，逐轮 134.8–140.8 s</td></tr>
<tr><td>50 步热身后外推（上一版用的 90 ms，<b>已作废</b>）</td><td class="num">90 ms</td><td class="num">241 s</td><td class="num">3.35 h</td>
<td>冷启动阶段偏高，被实测推翻</td></tr>
<tr><td>悲观上界：微基准 264 ms（不含 training_step 开销）</td><td class="num">264 ms</td><td class="num">706 s</td><td class="num">9.80 h</td>
<td>取数 126 + 模型 138，偏保守</td></tr>
<tr><td>PRISM-VQ stage-2</td><td class="num">未实测</td><td class="num">未实测</td><td class="num">未实测</td><td>一次都没跑进去，只能按结构推断</td></tr>
<tr><td>GRU 基线 s2024（实测）</td><td class="num">—</td><td class="num">86 s</td><td class="num">4,310 s</td><td>另一模型，仅供参考</td></tr>
</tbody></table>

<table>
<caption>表 5b｜全量预算：两个口径都给（stage-2 未实测，按 stage-1 单轮 ×2 结构推断）。</caption>
<thead><tr><th>口径</th><th class="num">stage-1 ×2 seed</th><th class="num">stage-2 ×8 档</th>
<th class="num">GRU ×2 seed</th><th class="num">合计</th></tr></thead>
<tbody>
<tr><td><b>实测口径</b>（{S_EPOCH:.1f} s/轮 × 50 轮 = {H50_MEAS:.2f} h/档）</td>
<td class="num">{2*STAGE1_50:.1f} h</td><td class="num">{8*STAGE2_50:.1f} h</td>
<td class="num">{2*GRU_50:.1f} h</td><td class="num"><b>{tot_meas:.1f} h</b></td></tr>
<tr><td>早停轨迹（实测 valid 在 epoch 4 触底，patience=15 → 约 19 轮）</td>
<td class="num">{2*STAGE1_50*19/50:.1f} h</td><td class="num">{8*STAGE2_50*19/50:.1f} h</td>
<td class="num">{2*GRU_50:.1f} h</td><td class="num"><b>{2*STAGE1_50*19/50+8*STAGE2_50*19/50+2*GRU_50:.1f} h</b></td></tr>
<tr><td>悲观上界（微基准 264 ms/batch）</td><td class="num">{2*up_epoch*50/3600:.1f} h</td>
<td class="num">{16*up_epoch*50/3600:.1f} h</td><td class="num">{2*GRU_50:.1f} h</td>
<td class="num"><b>{tot_up:.0f} h</b></td></tr>
</tbody></table>
<p class="note">上一版报告的「62 h」是在只有 50 步外推（90 ms/step）时算的，<b>已被 10 轮实测取代</b>；
派单按微基准上界重算的 178.8 h 保留为悲观列。三档差异全部来自「单 batch 多少毫秒」这一个未知数，
现在它有 3 个独立观测值（51 / 90 / 264 ms），实测的 51 ms 可信度最高
（10 轮 × 3,161 batch/轮，含 valid）。</p>'''
rep(T5_OLD, T5_NEW)

F4_OLD = '''{fig(f4, "<b>图 4｜按实测单步成本推算的全窗 50 轮预算 vs 预注册停手闸（对数轴）。</b>"
        "单个 stage-1 档 ≈ 3.35 h 已越 3 h 闸；stage-2 结构更重（MoE + Transformer），按 2× 保守估计。"
        "合计 ≈ 62 GPU 小时。红色为该档允许的上限。<b>这不是额度问题，是墙钟问题。</b>"
        "来源：<code>throughput_bench.json</code> + <code>data_probe.json</code> 的 2,675 个交易日。")}'''
F4_NEW = '''{fig(f4, "<b>图 4｜全窗 50 轮预算：实测口径 vs 悲观上界 vs 停手闸（对数轴）。</b>"
        f"实测 {S_EPOCH:.1f} s/轮（10 轮真实循环）→ stage-1 单档 {STAGE1_50:.2f} h（<b>在 3 h 闸内</b>）、"
        f"全量合计 {tot_meas:.1f} h；悲观上界（微基准 264 ms/batch）合计 {tot_up:.0f} h。"
        "stage-2 未实测、按 stage-1 ×2 结构推断。红色为单档允许上限。"
        "来源：<code>throughput_bench_pl.json</code>（10 轮）、<code>throughput_bench.json</code>（微基准）、"
        "<code>data_probe.json</code>（2,675 个交易日）。")}

{fig(f7, "<b>图 5｜逐轮墙钟（10 轮，workers=2，含完整 valid 段）。</b>"
        "134.8–140.8 s、中位 136.1 s，全程无退化 —— 与 11-25 那次「epoch 从 73 s 退化到 800 s」形成对照，"
        "说明那时的退化更像 8 worker 争抢，而不是模型越跑越慢。"
        "来源：<code>throughput_bench_pl.json</code> 的 <code>epoch_seconds</code>。")}'''
rep(F4_OLD, F4_NEW)

s = s.replace("图 5｜「行情表无行」与「真缺口」不是一回事", "图 6｜「行情表无行」与「真缺口」不是一回事")
s = s.replace("图 6｜涨跌停判据的 3.0× / 5.0× 过判", "图 7｜涨跌停判据的 3.0× / 5.0× 过判")

S6_OLD = '''<li><b>共享 Python 环境在 11-26 07:56 被损坏</b>'''
S6_NEW = '''<li><b><code>num_workers=8</code> 在这台机器上不可靠</b>：2026-11-27 三次尝试里 2 次硬退（RC=127，无 traceback）、
1 次成功但 spawn 用掉 121 s；<code>num_workers=2</code> 同一配置 10 轮连跑稳定。
11-25 那轮 GRU 的「epoch 73 s → 800 s 退化」与 1 次 <code>CUDA illegal memory access</code>，
方向上也与 8 worker 争抢一致。</li>
<li><b>共享 Python 环境在 11-26 07:56 被损坏</b>'''
rep(S6_OLD, S6_NEW)

U81_OLD = '''<li><b>stage-2 成本完全未测</b>：一次都没跑进去，表 5 的 2× 是结构推断。若 stage-2 实际是 stage-1 的 4×，
总量会从 62 h 涨到 ≈100 h。</li>
<li><b>teardown 死锁未修</b>：<code>persistent_workers</code> 在 Windows spawn 下收尾挂死（实测 240–900 s 两次），
会让每档白挂十几分钟，也会让「单档超时」判据误触发。</li>'''
U81_NEW = '''<li><b>stage-2 成本完全未测</b>：一次都没跑进去，表 5b 的 2× 是结构推断。若 stage-2 实际是 stage-1 的 4×，
实测口径总量会从 {tot_meas:.1f} h 涨到 {2*STAGE1_50 + 16*STAGE2_50 + 2*GRU_50:.0f} h 量级。</li>
<li><b>实测只覆盖 stage-1、单 seed、前 10 轮</b>：10 轮都在 valid 触底（epoch 4）之后，未跑到早停；
50 轮墙钟是按 136.1 s/轮线性外推（图 5 逐轮稳定，线性外推置信度较高）。</li>
<li><b>「teardown 死锁」是上一版的误判</b>，本轮更正为「标定脚本缺 <code>wandb.init</code>」+「8 worker spawn 不稳」；
生产路径在 workers=2 下 10 轮干净退出，未见死锁。</li>'''
rep(U81_OLD, U81_NEW)

io.open(p, "w", encoding="utf-8").write(s)
print("patched ok")
