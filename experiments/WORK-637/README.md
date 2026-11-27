# WORK-637 · 图表视觉检索（arXiv:2611.23723v1）在本机 A 股上的可回测复现

复现对象：*When Price Charts Rhyme: Historical Visual Retrieval for Stock Return Prediction*（arXiv:2611.23723v1，2026-11-18，无官方代码）。

## 交付物

| 文件 | 内容 |
|---|---|
| `preregistration.md` | 预注册协议（8 项写死 + D1–D10 偏离清单 + 阶段 B 超参）。提交早于任何回测产物 |
| `snapshot_manifest.json` | 8 个 quantdb 端点的 ok/error 原文、行数、日期范围、sha256；派生面板 sha256 |
| `imagelib_manifest_seed42.json` / `_seed2024.json` | 图像库目录、文件数、字节数、按年分布、清单级 sha256、重建命令 |
| `embedding_meta_seed*.json` | 权重 sha256、维度、分辨率、实测吞吐、embedding npy 的 sha256 |
| `signal_meta_seed*.json` | K 网格、行数、前视违规计数、信号 CSV 的 sha256 |
| `signals/` | `seed*_knn.csv`（K=100/1000/3000 长表）、`seed*_same_stock.csv`（H2 消融）、`weekly_*.csv` |
| `backtest_seed*_K*_c*.json` | 回测指标、bootstrap CI、分年、成本敏感性 |
| `audit_recompute.json` | 独立复算逐项比对（相对差 ≤1e-6） |
| `assertion_violation_*.txt` | 三条引擎断言的「真的会抛错」原文 |
| `report.html` / `preview_first_screen.png` / `report_print.pdf` | 单文件 HTML（图片 base64 内嵌）、1440×900 首屏、A4 打印 |
| `verdict.json` | 判词机器可读版 |
| `failure_log.md` | 失败尝试、修掉的缺陷、协议偏离的披露 |

## 重建命令（按顺序）

```bash
PY=/srv/data/multica/workspaces/workspace-c8faa36a018a/work-637-328d3fac2b83/workdir/.venv/bin/python
cd /srv/data/multica/workspaces/workspace-c8faa36a018a/work-637-328d3fac2b83/workdir/quant-lab

$PY -u scripts/work637_common.py            # 构建面板 -> data/WORK-637/panel.npz
$PY -u scripts/work637_snapshot.py          # 端点台账 -> snapshot_manifest.json
$PY -u scripts/work637_render.py --seed 42  # 185,729 张 PNG（8 进程，约 18 min）
$PY -u scripts/work637_encode.py --seed 42  # 384 维 embedding（约 55 min，CPU）
$PY -u scripts/work637_signal.py --seed 42  # 递归库 + 精确 top-K -> signals/
$PY -u scripts/work637_backtest.py --seed 42 --K 1000 --cost-bp 12
$PY -u scripts/work637_backtest.py --seed 42 --K 100  --cost-bp 12
$PY -u scripts/work637_backtest.py --seed 42 --K 3000 --cost-bp 12
$PY -u scripts/work637_backtest.py --seed 42 --signal-col same_stock --tag h2
$PY -u scripts/work637_assert_check.py      # 断言对照运行
$PY -u audit/recompute_work637.py           # 独立复算
$PY -u scripts/work637_report.py --preview   # 图 + HTML + 首屏 + PDF
```

图像库落在 `/srv/share/quantdb/code/WORK-637/imagelib/seed<seed>/<year>/`（不入 git，可由上面的 render 命令重建，
重建后的 PNG 字节与清单 sha256 一致）。面板与 embedding 落在 `data/WORK-637/`（`*.npy` / `*.parquet` 被 .gitignore 覆盖）。

## 关键口径（一句话）

池 = PIT 中证 300（`index_weight` 250 份月度快照 as-of）；图 = 20 日 qfq K 线 + MA20 + 量，140×140；
编码 = 冻结 DINOv2-S（CLS，384 维，L2）；信号 = 合法历史库 top-1000 邻居的**已实现** 5 日收益（`open(t+6)/open(t+1)-1`）均值；
组合 = top 十分位等权多头，周五出信号、次一交易日开盘成交、持有 5 个交易日，扣单边 12 bp，
涨停/跌停/停牌掩码先于信号生效。
