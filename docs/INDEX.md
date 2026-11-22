# 项目索引

本索引是 quant-lab 的文档入口。路径均为仓库相对路径；数据与运行时路径写绝对路径。

## 目标与规格

- [PROJECT.md](PROJECT.md)：项目目标、用户、范围、成功标准、主流程。
- [SPEC.md](SPEC.md)：WORK-513 的验收矩阵、CLI 契约、schema 口径。
- [../data_contracts/field_dictionary.md](../data_contracts/field_dictionary.md)：字段单位、复权、缺失、对齐的唯一口径。
- [../data_contracts/data_source_registry.md](../data_contracts/data_source_registry.md)：数据源可用性与踩坑登记。

## 环境与实现

- [ENVIRONMENT.md](ENVIRONMENT.md)：本机实测环境、依赖版本、磁盘、token 位置、质量门。
- [TECH-NOTES.md](TECH-NOTES.md)：选型候选、排除理由、风险。
- [../db/README.md](../db/README.md)：quantdb 目录、命令、测试、冒烟。
- [../db/schema.sql](../db/schema.sql)：15 张 P0 表、主键、单位注释、PIT 列。

## 决策

- [decisions/0001-storage-layout.md](decisions/0001-storage-layout.md)：Parquet L0 + DuckDB L1、分区与 manifest。
- [decisions/0002-policy-collection.md](decisions/0002-policy-collection.md)：六源白名单、双时间轴、去重。

## 运行与交付

- [RUNBOOK.md](RUNBOOK.md)：建库、增量、质量闸、查询、政策、备份、回滚。
- [UI-INVENTORY.md](UI-INVENTORY.md)：命令行入口清单（本项目无图形界面）。
- [DELIVERY.html](DELIVERY.html)：WORK-513 单文件交付说明。
- [../CHANGELOG.md](../CHANGELOG.md)：代码与文档变更记录。

## 当前状态

- 基线 SHA：`78e5b5e76d70417494d064bf5a18310e3ba62fcd`
- 本单分支：`agent/agent/4f411958f723`
- 单股冒烟：600000.SH，2006-11-22 至 2026-11-20，4,808 个交易日
- 数据规模：raw 约 57 MB（含文件系统块开销），DuckDB 约 1.3 MB
- 质量闸：manifest / calendar / pk / ohlc / cross-source / rowcount 全部 PASS
