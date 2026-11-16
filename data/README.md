# 本目录存放各实验的行情原始快照（原始层，只追加）。
# 由 scripts/fetch_market_data.py 按 `--exp <实验ID>` 生成，实验 ID 作为子目录名。
# **行情快照不入库**（仓库为 public），只保留 experiments/<EXP>/snapshot_manifest.json 里的
# 行数、区间与 sha256 作为快照身份凭据。
