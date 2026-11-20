# 论文对照表 · WORK-449

论文来源：ML-Quant（arXiv:2507.07107v2）Table 11「真实面板」行。

| 指标 | 臂 | 论文 | 本单 | 口径差异 |
|---|---|---|---|---|
| apparent_ic | no_mask | 0.047 | 0.0077 | factor set 12 vs 213; pool CSI300 vs all-A |
| apparent_ic | halt_only | 0.043 | 0.0106 | same |
| apparent_ic | full_mask | 0.039 | 0.0121 | same |
| sharpe | no_mask | 1.23 | -0.580 | same 8 bps cost, 3% single-name cap |
| sharpe | halt_only | 1.39 | -0.170 | same |
| sharpe | full_mask | 1.63 | -1.132 | same |
| max_drawdown | no_mask | 22.8% | -25.1% | close-cummax |
| max_drawdown | full_mask | 11.4% | -39.6% | close-cummax |
| ann_return | no_mask | 12.1% | -4.4% | 365.25-day annualisation |
| ann_return | full_mask | 15.8% | -11.5% | 365.25-day annualisation |
| delta_sharpe | no_mask − full_mask | −0.40 | +0.5516 | 主检验量 |
| delta_ic | no_mask − full_mask | +0.008 | -0.0044 | 辅证 |

## 不可直接比较的部分

- 股票池：论文全 A（约 5,000 只）vs 本单 CSI300 历史成分并集（680 只）——权重集中度、换手与冲击成本不可直接比较。
- 因子集：论文 213 个（含 LLM 挖掘因子）vs 本单 12 个，预注册冻结、不做前向选择。
- 数据源：论文未公开 A 股端点；本单为 AKShare/东方财富 hfq + 不复权双调用。
- 组合：α=10、单票上限 3%、Ledoit-Wolf 120 日、每日调仓——与论文 §3.6 对齐。
- 成本：主口径 8 bps 双边，与论文 §3.6 / `run_backtest(costs_bps=8)` 一致；另报 5 bps 敏感性（ΔSharpe = +0.4070）。

**只对齐背离方向与量级，不追绝对 Sharpe 水平（预注册 §12）。**
