# scripts/alt · 行业替代源抓取脚本（WORK-619）

10 个单文件脚本，把 WORK-541 判定为 B 类（只能近似）的 P0 指标里最值得做的 10 条，做成可复跑的取数管线。

## 契约（每个脚本都必须满足）

| 项 | 要求 | 实现方式 |
|---|---|---|
| 依赖 | 仅标准库（urllib / html.parser / json / csv / hashlib） | 无 requirements.txt；系统 `python3` 直接跑 |
| 窗口 | `--start` / `--end` | 接受 `YYYY-MM-DD` 或 `YYYY`；只影响输出窗口，抓取按源站能力 |
| 幂等 | 同命令连跑两次，`data.csv` 逐字节相同 | 按主键合并后排序写出；行内不含抓取时间 |
| 限速 | 同域名 ≤1 请求/秒 | 模块级 `_LAST[host]` + 1.05 s 间隔 |
| 失败 | 退出码非 0 且打印原因 | 抓不到 / 窗口为空 / 页面改版时 `sys.exit(1)` 并写 stderr |
| 留痕 | 逐行 `source_url`；manifest 记 `rows / csv_sha256 / fetched_at / params` | `save()` 统一写 |

## 目录

```
scripts/alt/
  <slug>/fetch.py          单文件抓取脚本（10 个）
  selection/candidates.csv 20 条候选 + 实测字段（选型的原始输入）
  selection/select_top10.py  按 5 条判据排序 → docs/selfbuild_top10*.csv
  selection/ledger_map.csv 44 条台账的逐行人写字段
  selection/gap_ledger.py  生成 docs/alt_gap_ledger.csv（行数/空值校验）
  run_all.py               10 条一起跑 + 幂等二次比对 → docs/alt_run_log.json
  build_report.py          生成 docs/selfbuild_report.html（图片内嵌）
```

## 10 个数据集

| slug | 源 | P0 行（541 行号） | 备注 |
|---|---|---|---|
| `trendforce_spot_price` | trendforce.cn 现货价页 | 10 | 合约价需会员，不在范围 |
| `benchmark_public_index` | benchmarkminerals.com | 3/4/11/14/65 | 相对指数点，非 USD/吨 |
| `sne_research_releases` | sneresearch.com 新闻稿 | 2/23/35/71 | 一条稿一行的头条绝对值 |
| `shfe_gfex_quotes` | 上期所 `.dat` + 广期所接口 | 67/76 | 无注册仓单 |
| `eurostat_trade` | Eurostat API | 47/73/74 | SITC 大类，非 HS 单品类 |
| `owid_ev_sales` | OWID grapher CSV | 56/57/58/62/66 | IEA GEO 派生 |
| `owid_energy_co2` | OWID grapher CSV | 55 | 总量 CO2 含工业过程 |
| `worldbank_wdi` | World Bank API | 54 | 水平值，增速自算 |
| `irena_re_capacity` | IRENA PxWeb API | 53/16 | 储能仅抽水蓄能 |
| `emmes_storage` | energystorageeurope.eu | 20 | 新闻稿正文数值 |

## 跑法

```bash
# 全部（含幂等比对）
python3 scripts/alt/run_all.py

# 单条
python3 scripts/alt/shfe_gfex_quotes/fetch.py --start 2026-11-17 --end 2026-11-27
```

落盘目录默认 `/srv/share/quantdb/raw/industry_alt/<slug>/`，可用 `ALT_RAW_DIR` 覆盖（测试用）。
入库 DDL 见 `docs/alt_schema.sql`；入库接线由软件交付另单做，本目录脚本不写生产库。

## 已知坑（实测）

- **IRENA PxWeb**：`GET ?query=` 会被忽略、只回 metadata；必须 POST 同一 URL 带 JSON body。`value` 可能是数组也可能是对象，两种都要处理。偶发 404，需重试。
- **openFDA drugsfda**：`limit=1000` 单页 298 s；`count=` 面不支持该端点；`submissions.submission_type` 不可检索。故本轮未入选（医药台账第 32 行）。
- **GFEX**：`trade_date` 必须 `YYYYMMDD`，且 `trade_type` 必填；否则返回 `code=1`。
- **SHFE**：`PRODUCTID` 是小写（`cu_f`），契约名用大写；`kx{YYYYMMDD}.dat` 非交易日 404。
- **100ppi（生意社）**：返回 660 字节 JS cookie 挑战页（`HW_CHECK`），模拟挑战属绕过访问控制，未做。
- **TrendForce / Benchmark / EMMES**：只有当日页面，没有历史接口，历史靠日更累积。
- **SNE 列表**：只回最近 12 条；更早稿件需人工给 `release_id` 并把 URL 加进 `EDITIONS`/列表逻辑。
