# 政策·宏观 收窄（WORK-620）

口径与复跑命令的**权威版本**写在数据底座总说明：`/srv/share/quantdb/README.md` §3.2.1。
本目录只放这一轮的可复跑产物。

| 文件 | 说明 |
|---|---|
| `macro_prune_keywords.json` | 判定关键词表 + 参数（标题权重/正文权重/move 阈值/keep 阈值/guard 覆盖）+ 命中率口径 |
| `plan.jsonl` | 5,560 篇逐篇判定（decision/reason/move_cat/move_score/move_words/keep_score/guard） |
| `deleted.jsonl` | 实际移出的 1,036 条台账（id/doc_id/主题/时间/备份路径） |
| `baseline_counts.json` | 收窄前 7 个库的篇数基线 |
| `metrics.json` | 三套命中率口径的前后读数 |
| `verify_result.json` | 核对证据（篇数勾稽、旧库镜像、元数据抽查 20 篇键值、命中率） |
| `manual_100_review.csv` | 收窄后随机 100 篇的人工复核逐篇清单 |

脚本在 `scripts/policy_prune/`：`wk.py`（REST 封装）→ `classify.py`（判定）→ `apply_prune.py`（移出，幂等）
→ `verify.py`（核对）→ `reingest.py`（回滚回灌）→ `build_report.py`（渲染 `docs/macro_prune_report.md`）。

```bash
python3 scripts/policy_prune/classify.py \
  --corpus /srv/share/quantdb/policy/meta/prune-backup-20261127/macro-5560-snapshot.jsonl \
  --out docs/policy_prune/plan.jsonl --csv docs/policy_prune/move_candidates.csv
python3 scripts/policy_prune/verify.py --sample 20
python3 scripts/policy_prune/build_report.py
```

备份（含正文与元数据、可回灌）：`/srv/share/quantdb/policy/meta/prune-backup-20261127/`。
