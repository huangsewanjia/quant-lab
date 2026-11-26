# 01 · 可行性实测（WORK-623 第一段）

统计时点：2026-11-27 01:25–01:41 CST　来源主机：Mo-server（Linux）
证据落盘：`/srv/share/quantdb/raw/reports_fulltext/_evidence/2026-11-27/`（含 `manifest.json`，逐条 sha256）
本段结论只覆盖「不登录能实测到的部分」；凡需要登录态才能看到的（下载端点、控件、额度），一律写**未取到**，不写成「不存在」。

## 1. 实测结果一览

| 项 | 实测值 | 证据 |
|---|---|---|
| `GET https://www.fxbaogao.com/robots.txt` | HTTP 200，`text/plain`，124 B，sha256 `4ad599866e98e7cf…` | `robots-www.fxbaogao.com.txt` |
| `GET https://static.fxbaogao.com/robots.txt` | HTTP 200，124 B，内容与主域一致 | 未另存（同内容） |
| `GET https://www.fxbaogao.com/detail/5884540`（匿名） | HTTP 200，`text/html; charset=UTF-8`，85,752 B，sha256 `8c23e1b9104fbf58…` | `detail-5884540.html` |
| 详情页发布时点 | `article:published_time` = `2026-11-16 10:15:02 CST (+08:00)` | 同上，`grep published_time` |
| 详情页标题 | `艾奇在线广告研究院:《2026中国教育行业GEO与SEO优化研究报告：…》-发现报告` | 同上，`og:title` |
| 站内 `/pdf` 下载链接（匿名页） | **0 处**；全页 `/pdf` 命中 1 处，为页脚外链 `https://www.foxitsoftware.cn/pdf-reader/` | `grep -o '/pdf'` 计数 |
| `GET https://static.fxbaogao.com/detail_source/js/app-v1.js` | HTTP 200，8,177 B，sha256 `ca4917ea58dd47ee…` | `static-app-v1.js` |
| 前端 VIP 判定 | 读 `localStorage.user`；`vipTime` 未过期 → 徽章「高级VIP」，`bVipTime` 未过期 → 「标准VIP」，否则「普通用户」 | `app-v1.js` `getVipState()` |
| 本机 CDP `127.0.0.1:9222` | **关闭**（`/dev/tcp` 连接失败） | 见 §3 |
| `DESKTOP-TS1FCDR` 可达性 | 本机 `getent hosts` 无记录；无 ssh 配置项 | 见 §3 |
| `/srv/share/quantdb/secrets/fxbaogao_cookie.json` | **不存在**（目录也未建） | `ls` 无输出 |
| 本机 venv（`/home/moyongkai/.venvs/quantdb`） | `pypdf 7.1.1`、`duckdb 2.0.1` 可用；`playwright` **未安装** | `python -c "import playwright"` → ModuleNotFoundError |
| 桌面机 Python / Playwright 现状 | **未取到**（本机无法连到 DESKTOP-TS1FCDR，也没有该机执行通道） | — |

## 2. robots.txt 原文（2026-11-27 实测，124 B）

```
Sitemap: https://www.fxbaogao.com/sitemap.xml

User-agent: *
Disallow: /pdf
Disallow: /user
Disallow: /page2
Disallow: /view
```

判定：`/detail/`、`/`、静态资源允许；`/pdf`、`/user`、`/page2`、`/view` 禁止。
因此本通道实现上**从不向 `/pdf` 发 HTTP 请求**——PDF 只允许由用户自己的登录浏览器会话（CDP 驱动其已开窗口）或用户手动点击产生。自动化的网络请求只落在 `/detail/` 等允许路径。

## 3. 登录态与执行位置（为什么真实抓取没跑）

```
$ hostname
Mo-server
$ getent hosts DESKTOP-TS1FCDR      # 无输出
$ cat ~/.ssh/config                 # 无该主机项
$ timeout 1 bash -c 'echo > /dev/tcp/127.0.0.1/9222'   # 退出码非 0
$ ls /srv/share/quantdb/secrets     # 目录不存在
```

子单 [WORK-624]「要你动手｜登录发现报告，把 VIP 登录态交给采集器」当前状态 `todo`，用户尚未在 DESKTOP-TS1FCDR 上启动 `--remote-debugging-port=9222` 的独立 profile Chrome。
结论：**登录态不存在**，任何「VIP 能看到什么/能不能下载」的判断都只能写未取到。

## 4. 未取到（不是不存在）

| 问题 | 状态 | 说明 |
|---|---|---|
| 下载按钮的真实 DOM 选择器 | 未取到 | 匿名页不渲染下载控件；需登录后在 `fetch --dry-run` 诊断 |
| 下载请求打到的端点（是否为 `/pdf?id=…`） | 未取到 | 同上；且 `/pdf` 路径本通道不会主动请求 |
| 高级 VIP 的下载额度/频控数值 | 未取到 | 站内规则页需登录；`/user` 亦被 robots 禁止 |
| 桌面机 Python 版本、Playwright 是否可装 | 未取到 | 本机到该机没有执行通道（不是「不能装」） |

## 5. 对实现的影响（已落进代码的约束）

1. 采集器把「登录态检查」做成**前置闸**：不是高级 VIP 就整批停，写 `login_state.json` 证据，不逐篇硬试。
2. 首选路径是 CDP 复用用户已登录 Chrome；拿不到 CDP 时退到 `sheet` 子命令生成单文件待下载清单，用户手点，`adopt` 收编。两条路径都遵守 `/pdf` 不主动请求。
3. 所有失败走 `failed.jsonl`（`report_id` / `url` / `http_status` / `reason`），不静默跳过。
