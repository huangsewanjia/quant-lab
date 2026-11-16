# 审计闸门（`audit/`）

> **一句话**：任何要出现在对外材料里的数字，都必须能由本目录的脚本在**同一份快照**上**独立复算**出来。
> 复算不复用建模阶段的任何函数——不共用口径、不共用实现、不共用结论。

---

## 一、审计契约（硬规定）

| # | 规定 | 怎么落地 | 怎么验 |
|---|---|---|---|
| 1 | **任何对外数字必须过这里** | 报告正文、指标表、图注里的数字，都要能由 `audit_recompute.py` 在同一份 `snapshot_manifest.json` 锁定的快照上复算出来 | 对外材料里出现的数字 → 在 `audit_findings.json` 里找到同名条目 → 状态不是 BLOCKING |
| 2 | **不复用建模阶段的函数** | `audit_recompute.py` **禁止** import `scripts/build_analysis.py` 等建模脚本的任何函数、类或变量；数值一律用 `csv` 模块直读快照、用纯 Python 列表/字典重算 | `grep -n "^import\|^from" audit/audit_recompute.py` → 只允许标准库（`argparse/csv/hashlib/json/os/sys`） |
| 3 | **判据先写死再跑** | 阈值与声称值写在脚本里（`check(...)` 的第三个参数），跑完不改；**不放宽阈值让结论变绿**，也不删断言 | 看 `check()` 调用处的 `tol=` 与声称值 |
| 4 | **审计者不复用建模者的结论** | 审计阶段独立复算一遍，不直接引用建模阶段的输出作为「已验证」 | `audit_findings.json` 的 `measured` 字段来自重算，不来自 `metrics.json` |
| 5 | **缺失如实记缺** | 取不到的数据源、没生成的图件、没跑的实验，写进 findings 并注明「未取到」，不用别的口径冒充 | `audit_findings.json` 里 `severity=SUGGESTION` 的条目 |
| 6 | **不联网** | 审计只读本地快照与本地 QA 产物；跨源校验用的另一源点值是**当初实跑时记下来的常量**，写死在脚本里 | 脚本内无 `requests` / `akshare` 调用 |

> **一句话记忆**：建模阶段负责算出数字，审计阶段负责**不算一遍**然后对账。

---

## 二、目录里有什么

| 文件 | 是什么 | 状态 |
|---|---|---|
| `audit_recompute.py` | 独立复算脚本。5 节：重算对外数字 → 跨源点值校验 → 快照 sha256 一致性 → 图件 QA 产物 → 最终 HTML 禁止内容扫描 | 唯一执行入口 |
| `README.md` | 本文件：审计契约 + 调用方式 + 已知边界 | 契约正文 |

---

## 三、怎么跑

```powershell
# 在仓库根目录执行；--exp 省略时默认取环境变量 QUANTLAB_EXP，再默认 WORK-404
python audit/audit_recompute.py --exp WORK-404
```

**不需要任何环境变量。** 脚本内部在 `main()` 入口把 `sys.stdout` / `sys.stderr` 显式切到 UTF-8（`_force_utf8_stdio()`），Windows 默认的 GBK(cp936) 控制台、Git Bash、以及「输出重定向到文件或管道」三种情形都能直接跑完。

> **为什么必须内置这一步**：Windows 默认区域设置是 cp936。脚本里只要有一个非 GBK 字符（例如 U+2212 数学减号 `−`），输出被重定向时 `sys.stdout` 就会用 GBK 编码并抛 `UnicodeEncodeError`，进程以 **exit 1** 收场——`audit_findings.json` 根本产不出来，审计结论也就无从谈起。
> 双重保险：**①** 判据标签统一用 ASCII `-`（不用 `−`）；**②** 脚本内强制 UTF-8 输出。

**退出码语义**

| 退出码 | 含义 | 能不能进研究备忘录 |
|---|---|---|
| `0` | 判决 `PASS`（无 BLOCKING） | 可以（但仍须披露 SUGGESTION 条目） |
| `1` | 判决 `BLOCKING`（存在 blocking） | **不可以**，必须带着实测值退回对应阶段重做 |

**输出**

| 产物 | 路径 | 说明 |
|---|---|---|
| 终端判决 | stdout | 逐条 `[OK]/[FAIL]` + 末行 `== 审计判决：PASS（PASS n / SUGGESTION n / BLOCKING n）==` |
| 结论文件 | `experiments/<EXP>/audit_findings.json` | 每条含 `severity / item / measured / expected / gap / location / how_to_verify` |

---

## 四、审计了什么

| 节 | 查什么 | 判据 |
|---|---|---|
| 1. 独立重算对外数字 | 16 个对外数字（区间收益、风格差 pp、成交额代理水平与变化） | 与声称值之差 ≤ `tol`（默认 0.0005，pp 类 0.01） |
| 2. 跨源点值校验 | 源A（腾讯快照）对源C（Tushare，服务端出口 IP）的 6 个关键点 | 绝对差 ≤ 0.005（源A 落盘 2 位小数、源C 原始精度，判据为四舍五入半格） |
| 3. 快照 sha256 一致性 | manifest 里每条快照的 sha256 与磁盘文件重算值 | 完全相等；不等即判「快照漂移」 |
| 4. 图件 QA 产物 | 对齐审计 verdict、碰撞审计 verdict | 对齐 `PASS` / `NOT APPLICABLE`；碰撞 `REVIEW REQUIRED` 记 SUGGESTION、`FAIL` 记 BLOCKING |
| 5. 最终 HTML 禁止内容 | 首页三行免责是否齐全 | 「内部研究模拟」「非持牌机构」「不构成投资建议」三行齐全 |

---

## 五、已知边界（诚实披露）

| 边界 | 现状 | 影响 |
|---|---|---|
| **只覆盖 WORK-404 口径** | 脚本第 1 节的 16 个判据是按 WORK-404 的对外数字写死的 | `WORK-413-S2` 的判定表**尚未**过独立复算，`experiments/registry.md` 已标注为已知缺口，不当作「已验收」 |
| **不审建模逻辑本身** | 只对账「数字对不对」，不审「口径选得对不对」 | 口径选择由 `data_contracts/` 的契约与研究备忘录负责 |
| **东财成交额缺失** | 源B 被本机 IP 限流，12 条记录 `ok:false` | 全市场成交额只能用 `volume × close` 代理口径（系统性偏高 3.3–3.6 倍），见 `data_contracts/metric_definitions.md` §6 |
| **fig3 碰撞审计 REVIEW REQUIRED** | 1 个 warn、0 个 fail | 记 SUGGESTION，不阻断 |
| **跨源点值是常量** | 第 2 节的 6 个源C 点值是当初实跑时写死的常量 | 源C 侧不重新联网取数；如需重新取，用 Tushare `index_daily` 同参数复跑并更新常量 |

---

## 六、新增一个实验时怎么接入

1. 先在 `experiments/registry.md` 插入该实验的一行（含「审计状态」栏）。
2. 在 `audit_recompute.py` 里**新增**该实验的判据（新的 `check()` 调用与新的对比常量），**不要**改写已有实验的判据。
3. 跑 `python audit/audit_recompute.py --exp <EXP>`，退出码 0 才算过闸门。
4. 把 `experiments/<EXP>/audit_findings.json` 随实验一起入库（它是结论的一部分，不是中间产物）。
5. 在研究备忘录里写明：结论来源 = 哪个提交 + 哪份快照 + 哪次审计运行的判决。
