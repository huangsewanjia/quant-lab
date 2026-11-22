# ADR 0002：六源白名单政策采集与双时间轴

状态：已采纳（2026-11-22）

## 背景

政策文本进入研究时，至少需要两个时间：发布日与生效日。只有发布日会把「未来才生效」的规则提前用于历史；只有生效日会丢失公告时点。来源必须限定在官方机关，避免自媒体转载。

## 决策

1. 白名单六源：国务院、央行、证监会、发改委、财政部、工信部。
2. 配置驱动：`db/policy/sources.toml` 记录列表页、链接正则、正文选择器。
3. 正文抽为纯文本，写 `policy/<source>/<sha16>/content.txt`。
4. `meta.json` 必须含 `publish_date`、`effective_date`、`agency`、`document_number`、`url`、`fetched_at`、`sha256`。
5. 双时间轴缺一：不写正文与 meta，写 `policy/missing/YYYYMMDD.jsonl`。
6. 同文去重：正文 sha256 相同即跳过；同 URL 也跳过。
7. 列表解析：gov 用官方 JSON，PBC/CSRC/NDRC/MOF 用静态 HTML，MIIT 用站内搜索 JSON API。

## 理由

- 官方源可追溯，避免转载与二手解读。
- 双时间轴是回测防前视的最小要求。
- 配置化让新增源不改主流程；正文选择器针对站点结构，trafilatura 只兜底。
- MIIT 列表页是 JS 跳转，直接抓 HTML 只有 2 KB 壳；站内搜索 API 实测返回 JSON，稳定可用。

## 后果

- 新闻类条目通常没有生效日，会被记入 missing，这是预期行为。
- 站点改版会导致选择器失效；每次运行都写 `last-run.json` 与 missing，便于发现。
- `document_number` 可能只抓到正文中的「第 848 号」而非完整「国令第 848 号」，因为 gov.cn 的元数据块在正文容器之外；字段仍非空，后续可加元数据块解析。

## 回滚

- 代码回滚：`git revert`。
- 数据回滚：删除 `policy/` 下本单生成的文件与 `index.json`，重新采集；不影响行情库。
