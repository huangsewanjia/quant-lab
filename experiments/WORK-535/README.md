# WORK-535 · P0 行业数据采购清单（可复现资产）

> **工单** WORK-535 ｜ **日期** 2026-11-23
> **交付物**（仓库根 `docs/`）：`procurement_plan.csv`（**109 行**主清单）｜ `procurement_brief.md`（≤2 页决策摘要）｜ `procurement_inquiry_templates.md`（**14 段**询价模板）｜ `procurement_evidence_log.md`（证据分级 / 已修死链 / 停手登记 / 本单 10 处坑）
> **口径登记**：`data_contracts/data_source_registry.md` §6

---

## 这个目录放什么

把一次性 issue 目录里的调研过程固化成仓库常驻资产：**主清单能逐字节重建，价格能逐条复核，摘要里的每个数字有独立复算闸门**。

| 文件 | 作用 |
|---|---|
| `extract_catalog.py` | 从 `data_catalog` 只读抽出 16 条「否（需采购）」+ 61 条「部分免费」 |
| `plan_purchase.py` | 23 行「需采购指标 × 数据商」的**调研底稿**：覆盖度、公开价原文摘录、询价入口、否定性证据 |
| `plan_partial.py` | 61 行「部分免费指标 × 付费源」的底稿：免费替代源、能力差距（频率降级 / 口径变窄 / 滞后） |
| `plan_reach.py` | 每个「官网价格页URL」的实测可达性（2026-11-23 全表体检结果） |
| `plan_research.py` | **研报订阅通道**（工单评论增补）：5 家能服务 5 条战略层「产业链利润/价值分布」硬缺口的研报通道 × 5 条指标 = 25 行 |
| `build_plan.py` | 把上面三张表 + DuckDB 合成 `docs/procurement_plan.csv`（合并重复「指标×数据商」、覆盖度取保守值、附链接实测状态） |
| `verify_prices.py` | 价格复核：重新抓取引用的 URL，**按数值**比对（不是文本匹配） |
| `check_plan_urls.py` | 全表「官网价格页URL」可达性体检 → `plan_url_status.json` |
| `check_free_urls.py` | 「免费替代源」列内链接可达性体检 → `free_url_status.json` |
| `final_stats.py` | 覆盖度排序 + 「先买哪 2–3 家」的贪心集合覆盖算式 → `final_stats.txt` |
| `coverage_stats.py` | 同上（早期版本，口径未剔除「不覆盖」行；**保留作对照，不要用**） |
| `price_verification.txt` | 价格复核原始输出 |
| `plan_url_status.json` / `free_url_status.json` | URL 体检原始结果 |
| `final_stats.txt` | 算式原始输出 |
| `audit_findings.json` | 独立复算结论（由 `audit/recompute_work535.py` 产出，43 项 blocking 0） |
| `probe_research_vendors.py` / `probe_research_vendors2.py` | 研报通道厂商的价格页 / 产品页 / 联系入口探测 → `research_vendor_probe.json` / `research_vendor_probe2.json` |

---

## 复现

```bash
python extract_catalog.py                                          # 1) 取数（只读 DuckDB）
cd . && python build_plan.py ../../docs/procurement_plan.csv      # 2) 重建主清单（逐字节一致）
python verify_prices.py ../../docs/procurement_plan.csv            # 3) 价格复核（会打厂商网站；SEMI 会 429）
python check_plan_urls.py ../../docs/procurement_plan.csv          # 4) URL 体检
python check_free_urls.py ../../docs/procurement_plan.csv          # 5) 免费源链接体检
python final_stats.py ../../docs/procurement_plan.csv              # 6) 覆盖度算式
python probe_research_vendors.py  research_vendor_probe.json        # 6b) 研报通道厂商价格页探测
python probe_research_vendors2.py research_vendor_probe2.json       # 6c) 研报通道厂商联系入口探测
cd ../../ && python audit/recompute_work535.py                    # 7) 独立复算闸门
```

依赖：`duckdb`、`pandas`、`numpy`。数据源：`/srv/share/quantdb/db/quantdb.duckdb`（**只读**，本单不回写 `data_catalog`）。

---

## 本单的三条硬纪律

1. **价格零编造**：任何价格要么在引用的 URL 上复核命中（A 级），要么标注为本机取不到、证据降为 B 级（SEMI，429），要么写 `未公开，需询价` 并给询价入口。**没有「大概是 $X」**。
2. **覆盖度必须有依据**：覆盖度只来自实际打开的厂商产品页；**核实为不覆盖的写「不覆盖」，不计入任何覆盖数**（本单 2 行）。保守口径：同一「指标 × 数据商」有多条证据时，**覆盖度取低**、建议取强，冲突写进备注。
3. **区分数据与报告**：McKinsey / 中国有色金属工业协会 / 中国医药工业信息中心只卖报告或文章 → 标「不买」；TrendForce 报价单位是**每颗芯片规格**（DDR5 16Gb → 72.00），不是本库要的 USD/GB，换算前不可并列。
4. **研报通道只到「覆盖研究域」**：5 家研报通道（慧博 / 发现报告 / 萝卜投研 / 中金 / 中信）虽然各自覆盖 5 条战略层硬缺口，但给的是 **PDF 与图表、不是结构化数据** → 覆盖度记「部分」、建议记「等」，**必须先索取样张**核对「各环节利润率」是否落在里面；外资投行研报合同通常**禁止转载与二次分发**。
