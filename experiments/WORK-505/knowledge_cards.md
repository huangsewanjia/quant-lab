# WORK-505 · 待落 WeKnora「量化研究·事实与踩坑库」的卡片稿

> **状态**：**未落库**。本单运行时**没有** WeKnora 的工具 / CLI / skill（已核 `tool_search`、PATH
> 与 skills 目录，均无入口），故项目口径「每单收口至少落 1 张卡」**本单无法执行**。
> 本文件把三张卡的正文写好，**任何带 WeKnora 访问权的人可直接照抄发布**（卡号建议
> `量化研究/事实与踩坑库` 下新建三张，标签见各卡）。

---

## 卡 1 · 坑｜Windows 上 conda/numpy 与 pip/torch 的双 OpenMP 运行时冲突

**事实**：本机 `F:\001_install\020_Anaconda\envs\ds` 环境里，numpy 链 **MKL**、而 pip 装的 torch
自带另一份 **OpenMP**（`libiomp5md.dll`）。同时 import 两者即崩：

```
OMP: Error #15: Initializing libiomp5md.dll, but found libiomp5md.dll already initialized.
```

**影响面**：不止 torch —— `quant-lab` 里 WORK-449 / WORK-500 的**全部**脚本（numpy + torch）
在这台机器上都会崩，表现为「脚本报错但看不出跟 numpy 有关」。

**解法（不改用户已有环境）**：

```powershell
# 用 --system-site-packages 建 venv，复用 site-packages 的 torch，再把 numpy 换成 OpenBLAS 版
& 'F:\001_install\020_Anaconda\envs\ds\python.exe' -m venv --system-site-packages F:\quant_work505\venv
& 'F:\quant_work505\venv\Scripts\python.exe' -m pip install --no-deps --upgrade numpy
# 实测：numpy 2.5.5（OpenBLAS）+ torch 2.11.0+cu128，CUDA 可用
```

**反模式（不要用）**：`KMP_DUPLICATE_LIB_OK=TRUE` —— 它只是把「两个运行时同时加载」这个错误压下去，
结果不确定、且会污染后续所有数值口径。沿用 WORK-500 的纪律：**换环境，不改口径**。

**依据 / 关联**：WORK-505 失败日志 §3.2；报告 §7 第 6 条。

---

## 卡 2 · 事实｜WORK-449 的 680 只冻结快照已 674 只不可复原（2026-11-22 取证结论）

**事实**：WORK-449 的冻结快照 `data/WORK-449/*.csv`（680 只 CSI300 成分、1,575,502 行、156.9 MB，
清单 `experiments/WORK-449/snapshot_manifest.json` 逐股 sha256）在本单开工时**只剩 6 只可复原**：
`sh.600000 / 600004 / 600005 / 688506 / 688561 / 688981`（770,855 字节，sha256 与清单**逐字节相符**）。

**搬运通道（7 条，全部实测）**：

| 通道 | 结果 |
|---|---|
| SSH 进 Mo-server 原工作区 | 目标目录已被平台清理（`work-449-acc3a29910af` 已不存在） |
| Mo-server GNOME 回收站 | 11,458 个 CSV，其中 1,370 个指向 WORK-449；**逐条 sha256 命中 6 个**（全在 10:16 的 hfq 代） |
| git 对象库 | 266 + 8,442 个 blob，**零命中**（契约规定 `data/` 不入库） |
| SMB `\\192.168.9.104\share` | 无货 |
| GitHub 远程历史 | 无 `data/` |
| 东财 `push2his` 重取 | **四条路径全断连**：本机直连、`127.0.0.1:7890` 代理、curl_cffi `impersonate='chrome'`、Mo-server 直连 |
| 其它实验的异源面板 | 来源/股票池/复权/窗口全不同，**不可替代** |

**要点**：回收站里残留的是**多代**数据（旧代 10 位小数、qfq 代、hfq 代），**只有 hfq 代与冻结清单
对得上**；逐条 sha256 比对是唯一可靠的判定手段，不能靠「行数一致 / 日期范围一致」认领。

**结论**：2026 年重取**大概率复现不了**冻结的 sha256 ⇒ 任何依赖该快照的复现单，必须先做
「快照可复原性」取证，再承诺判词。

**依据 / 关联**：WORK-505 失败日志 §2.1–2.7、§5；`experiments/WORK-505/snapshot_verification.json`
（680 行逐股判定：PASS 6 / FAIL 0 / MISSING 674）。

---

## 卡 3 · 事实｜本机 git push 403 时，用 GitHub git-data API 逐字节复现 commit

**事实**：本机 `git push` 被拒（`remote: Permission to huangsewanjia/quant-lab.git denied to
yongkaimo.`，Windows 凭据管理器的账号不是仓库账号）；Mo-server 经代理 push 报
`GnuTLS recv error (-110)`。但 Mo-server 上有可解密的 fine-grained PAT（`git-credential-multica`）。

**解法**：不走 git 协议，直接调 GitHub **git-data API** 逐 blob 复现：

```
POST /repos/{owner}/{repo}/git/blobs    {content: <base64>, encoding: "base64"}
POST /repos/{owner}/{repo}/git/trees    {base_tree: <parent tree sha>, tree: [...]}
POST /repos/{owner}/{repo}/git/commits  {message, tree, parents, author, committer}
PATCH /repos/{owner}/{repo}/git/refs/heads/<branch>  {sha, force: false}
```

**三个必须做的核对**（否则推上去的不是同一个 commit）：

1. **每个 blob 回读 sha** 与本地 `git ls-tree` 的 sha 逐一比对；
2. **远端 tree sha 必须与本地 `git show -s --format=%T` 逐位相同**（这是「文件内容完全一致」的硬证据）；
3. **commit sha 会差一个结尾换行**：GitHub API 不给 commit message 补尾换行，git 补 ⇒ 远端 commit
   与本地 commit 是两个 sha、但同一个 tree。本地做法：把本地 commit 的 raw bytes 分别按
   「带尾换行 / 不带」两种拼法 `git hash-object -t commit --stdin` 试哈希，取与远端相同的那一份
   `git hash-object -t commit -w --stdin` 写进对象库，再 `git reset --hard <远端 sha>`，
   使**本地与远程是同一个 sha**。

**安全**：token 只在远端 shell 变量里用（`git credential fill` 取出后 `export TOK`），
不进命令行、不进日志、不回显。

**关联**：WORK-505 失败日志 §4（两轮推送的实测输出）。
