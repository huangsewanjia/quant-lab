# WORK-536 实测证据索引

本目录只放**索引与结论摘要**。原始研报元数据在 `/srv/share/quantdb`，按版权口径不入库。

## 运行时间

- 抓取：2026-11-23 06:09–06:11 UTC（14:09–14:11 UTC+8）
- 建表：2026-11-23 06:15 UTC
- 校验：2026-11-23 06:15 UTC

## 证据文件（绝对路径）

| 内容 | 路径 |
|---|---|
| 抓取批次清单 | `/srv/share/quantdb/meta/reports/runs/20261123T060932Z_20230101_20261123.json` |
| 分区 manifest | `/srv/share/quantdb/meta/reports/trade_date=YYYYMMDD.json` |
| 汇总统计 | `/srv/share/quantdb/meta/reports/summary.json` |
| 抽样校验 | `/srv/share/quantdb/meta/reports/validation/verify-20261123T061503Z.json` |
| 抓取日志 | `/srv/share/quantdb/logs/reports-fetch-20261123.jsonl` |
| 幂等第一次 | `/srv/data/multica/workspaces/workspace-c8faa36a018a/work-536-fd8955b65de3/workdir/evidence/idem_run1.json` |
| 幂等第二次 | `/srv/data/multica/workspaces/workspace-c8faa36a018a/work-536-fd8955b65de3/workdir/evidence/idem_run2.json` |

## 核心数字

| 指标 | 值 |
|---|---:|
| `report_meta` 行数 | 68,874 |
| `consensus_eps` 行数 | 1,705,783 |
| `rating_change` 行数 | 10,403 |
| 覆盖标的 | 3,818 |
| 覆盖机构 | 68 |
| 覆盖行业 | 175 |
| 10 只标的条数偏差 | 全部 0% |
| 3 只标的一致预期最大偏差 | 3.48%（比亚迪 2027） |
| 2025-01 同区间重跑 | 两次均 527 行 |

## 关键结论

上游列表接口对 2023 年及更早研报的 EPS/PE 预测字段做了截断：2023 年当年 EPS 空值率 99.97%，2024 年次年/后年空值率约 100%。因此 2023 年无法重建完整一致预期。详见 `../README.md` §6.4。
