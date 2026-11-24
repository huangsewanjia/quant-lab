#!/usr/bin/env python3
"""WORK-557 段2c→段4 后处理链：等训练结束 → 回测 → 完整性 → 前视 → 独立复算 → 判词 → 成本敏感性。

设计目的：训练是数小时的墙钟任务，而宿主前台调用 60 s 即转后台、每次轮询都要
重读一遍上下文（额度吃紧）。本脚本把「等待 + 全部后处理」放进**一个**后台任务，
跑完后只落一个 `finish_summary.json`，避免空转轮询。

只做「等 + 跑 + 落盘」，**不判断、不改口径**；所有数字仍由下游脚本从产物 JSON 读出。

停手条件③（单档 > 2 h）由 `work557_train_all.py --max-run-seconds` 就地执行：
超时即杀掉该档并停止后续档位，驱动仍会写出 `training_log.json`（含 `stopped` 字段），
所以本脚本的等待逻辑对「正常跑完」和「被停手打断」两种终局都成立。

用法：
    .venv\\Scripts\\python.exe -u scripts/work557_finish.py
    .venv\\Scripts\\python.exe -u scripts/work557_finish.py --wait-only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
EXP = "WORK-557"
EXP_ISSUE = "01a20cc7-07ec-722d-8671-a6201a12dd79"
EXP_DIR = os.path.join(REPO, "experiments", EXP)
LOG_DIR = os.path.join(EXP_DIR, "logs")
VENV_PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")
if not os.path.exists(VENV_PY):
    VENV_PY = sys.executable

# 只认 **python.exe**：否则本探针自身的 powershell 命令行里也含
# 'work557_train_all.py'，会把「探针自己」数成驱动进程 → 驱动真的死了也探测不到
# （独立审查 2026-11-24 发现的静默挂死风险）。
PS_ALIVE = ('(Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" | '
            "Where-Object { $_.CommandLine -like '*work557_train_all.py*' } | "
            "Measure-Object).Count")


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def training_driver_finished() -> bool:
    """训练驱动收尾的判据：它只在**整个驱动结束**时写 `training_log.json`。

    （`collect()` 里的同名写入发生在驱动末尾，`wall_seconds` 只有末尾那次写才有。）
    """
    p = os.path.join(EXP_DIR, "training_log.json")
    if not os.path.exists(p):
        return False
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        return False
    return "wall_seconds" in d


def driver_alive() -> bool:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", PS_ALIVE],
                             capture_output=True, text=True, timeout=90)
        if out.returncode != 0:
            # 探测命令本身失败（powershell 报错 / 超时）→ 保守当成「还活着」。
            # 旧实现把「rc≠0 + stdout 空」当成 0 → 误判驱动已死（复审发现）。
            return True
        try:
            return int((out.stdout or "0").strip() or 0) > 0
        except ValueError:
            return True
    except Exception:
        return True          # 查不到就当还活着：宁可多等，不要误判


def wait_for_training(max_wait_s: float = 90000.0) -> dict:
    """等到训练驱动结束（正常跑完或触发停手条件）。

    默认上限 90000 s（25 h）：驱动每档硬停 7200 s、最坏 10 档 = 72000 s，
    36000 s 的等待上限会先于训练到期（独立审查 2026-11-24 发现）。
    """
    t0 = time.time()
    gone = 0
    while time.time() - t0 < max_wait_s:
        if training_driver_finished():
            log("训练驱动已收尾（training_log.json 含 wall_seconds）")
            return {"finished": True, "reason": "driver_wrote_training_log",
                    "waited_s": round(time.time() - t0, 1)}
        alive = driver_alive()
        if not alive:
            gone += 1
            log("训练驱动进程不在（%d/3）" % gone)
            if gone >= 3:
                return {"finished": False,
                        "reason": "driver_process_gone_without_training_log",
                        "waited_s": round(time.time() - t0, 1)}
        else:
            gone = 0
        time.sleep(60)
    return {"finished": False, "reason": "max_wait_exceeded",
            "waited_s": round(time.time() - t0, 1)}


def run_step(name: str, cmd: list, critical: bool = True) -> dict:
    os.makedirs(LOG_DIR, exist_ok=True)
    lp = os.path.join(LOG_DIR, "finish_%s.log" % name)
    t0 = time.time()
    with open(lp, "w", encoding="utf-8") as lf:
        lf.write("$ " + " ".join(cmd) + "\n\n")
        lf.flush()
        try:
            p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=REPO)
            rc = p.returncode
        except subprocess.SubprocessError as exc:
            rc = -1
            lf.write("\nEXCEPTION: %r\n" % (exc,))
    dt = round(time.time() - t0, 1)
    log("step %-18s rc=%-4s %.1fs -> %s" % (name, rc, dt, lp))
    return {"step": name, "rc": rc, "seconds": dt, "log": lp,
            "cmd": cmd, "critical": critical}


EXPECTED_RUNS = ["%s_s%d" % (a, s)
                 for a in ("full", "no_vq", "no_moe", "no_prior", "gru")
                 for s in (42, 2024)]

# 成本点回测步骤 → (产物文件, 是否预注册点)。预注册点自检不过就中断链。
COST_OUTPUTS = {
    "backtest_8bps": ("backtest_cost_8bps.json", True),
    "backtest_30bps": ("backtest_cost_30bps.json", True),
    "backtest_gross": ("backtest_gross_diagnostic.json", False),
}


def artifacts_missing() -> list:
    """训练产物（每档每 seed 的 json + parquet）缺哪些。"""
    preds = os.path.join(EXP_DIR, "preds")
    missing = []
    for k in EXPECTED_RUNS:
        if not os.path.exists(os.path.join(preds, k + ".json")):
            missing.append(k + ".json")
        elif not os.path.exists(os.path.join(preds, k + ".parquet")):
            missing.append(k + ".parquet")
    return missing


def validate_backtest() -> tuple:
    """回测产物自检：**空结果也返回 rc=0**（`work557_backtest.py` 全部档缺失时
    只打印 “missing … skipped”，`main()` 返回 None → 退出码 0），所以这里必须
    独立断言，否则链会拿着 `{}` 继续跑判词并报「ok=True」（独立审查发现）。

    要求至少有一个 seed 同时具备 full / no_vq / gru（H1 与 H2 的最小完整集）。
    """
    p = os.path.join(EXP_DIR, "backtest_results.json")
    if not os.path.exists(p):
        return False, "backtest_results.json 缺失"
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception as exc:
        return False, "backtest_results.json 解析失败: %r" % (exc,)
    if not d:
        return False, "backtest_results.json 为空（所有档位的预测 parquet 都缺失）"
    ok_seeds = [s for s in (42, 2024)
                if all("%s_s%d" % (a, s) in d for a in ("full", "no_vq", "gru"))]
    if not ok_seeds:
        return False, ("没有任何一个 seed 同时具备 full/no_vq/gru "
                       "（现有键: %s）" % ",".join(sorted(d)))
    return True, ("最小完整集齐备：seed %s 可做 H1/H2；共 %d 档"
                  % (",".join(str(s) for s in ok_seeds), len(d)))


def validate_cost_json(fname: str) -> tuple:
    """成本点回测产物自检：非空且含至少一个 `full_s*` 档。

    `work557_cost_sensitivity.py` 只挑 `full_` 开头的键，产物为空时它会**静默跳过**
    该成本点而 rc 仍为 0（复审 2026-11-25 发现）→ 成本表缺档却报 ok=True。
    """
    p = os.path.join(EXP_DIR, fname)
    if not os.path.exists(p):
        return False, "%s 缺失" % fname
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception as exc:
        return False, "%s 解析失败: %r" % (fname, exc)
    if not d:
        return False, "%s 为空（该成本点的回测全缺）" % fname
    fulls = [k for k in d if k.startswith("full_")]
    if not fulls:
        return False, "%s 里没有 full_s* 档（现有键 %s）" % (fname, ",".join(sorted(d)))
    return True, "%s：%d 档（full 档 %s）" % (fname, len(d), ",".join(sorted(fulls)))


def notify_ready() -> dict:
    """链全部跑完后：生成段2正文文件 + 把 issue 状态置为 in_review 作「产物就绪」信号。

    **只发信号、不发评论**：正文由下一轮 run 审阅后由智能体署名发布（不自动把
    未审的数字推出去）。状态改成 in_review 是平台级的「有产物待验收」信号，
    对应 wakeup 的 `--until-status in_review` 条件（一次性唤醒，避免 20 分钟
    轮询烧额度 —— 本单已过 100M 额度闸）。
    """
    rec = {"body": None, "status_rc": None, "status_out": ""}
    body = os.path.join(EXP_DIR, "comment_stage2.md")
    gen = [VENV_PY, os.path.join("experiments", EXP, "work557_comment_stage2.py"), body]
    p = subprocess.run(gen, cwd=REPO, capture_output=True, text=True)
    rec["body_rc"] = p.returncode
    rec["body"] = body if os.path.exists(body) else None
    if p.returncode != 0:
        rec["body_err"] = (p.stderr or "")[-500:]
        return rec
    cli = os.environ.get("MULTICA_CLI_PATH") or "multica"
    q = subprocess.run([cli, "issue", "status", EXP_ISSUE, "in_review",
                        "--output", "json"], cwd=REPO, capture_output=True,
                       text=True)
    rec["status_rc"] = q.returncode
    rec["status_out"] = (q.stdout or q.stderr or "")[-300:]
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default=EXP)
    ap.add_argument("--wait-only", action="store_true",
                    help="只等训练结束，不跑后处理（调试用）")
    ap.add_argument("--max-wait-seconds", type=float, default=90000.0)
    args = ap.parse_args()

    # 下游 6 个脚本里有几个把实验目录写死成 WORK-557（recompute / cost_sensitivity），
    # 而 verdict 按 --exp 写目录 → 误传别的 exp 会去覆盖 WORK-503 的产物。只收 WORK-557。
    if args.exp != EXP:
        raise SystemExit("本链只支持 --exp %s（下游脚本写死了该实验目录）" % EXP)

    os.makedirs(LOG_DIR, exist_ok=True)
    summary = {"experiment": args.exp,
               "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "wait": {}, "steps": []}

    log("等待训练驱动结束 …")
    summary["wait"] = wait_for_training(args.max_wait_seconds)
    log("等待结束：%s" % summary["wait"])

    if summary["wait"].get("reason") == "max_wait_exceeded":
        # 训练还没收尾就开跑后处理 = 拿半截产物算 H1/H2/H3。宁可停手回报。
        log("等待超时（max_wait_exceeded）——训练未收尾，不跑后处理链")
        summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        summary["ok"] = False
        with open(os.path.join(EXP_DIR, "finish_summary.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1, default=str)
        return 1

    if summary["wait"].get("reason") == "driver_process_gone_without_training_log":
        # 驱动进程消失了却没写 training_log.json：要么崩在收尾前，要么被判死。
        # 只有**全部 10 档产物齐**才继续（崩溃发生在 collect() 之后的情形）；
        # 否则按「训练未收尾」处理，不跑后处理（复审 2026-11-25 发现）。
        miss = artifacts_missing()
        summary["artifacts_missing"] = miss
        if miss:
            log("驱动消失且产物不全（缺 %d 项）——不跑后处理链：%s"
                % (len(miss), ",".join(miss)))
            summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            summary["ok"] = False
            with open(os.path.join(EXP_DIR, "finish_summary.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(summary, fh, ensure_ascii=False, indent=1, default=str)
            return 1
        log("驱动消失但 10 档产物齐全（崩在收尾写日志前）——继续后处理链")

    if args.wait_only:
        summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with open(os.path.join(EXP_DIR, "finish_summary.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1, default=str)
        return 0

    # ---- 后处理链 -------------------------------------------------------
    # 成本点：主 5/15、单边 8/8、双边 30/30、零成本（预注册外诊断）。
    # 非主口径用 --preds-tag 隔离派生文件，避免覆盖主口径的
    # {key}_daily.parquet / {key}_rankic.npy（H2/H3 读的就是主口径那两份）。
    steps = [
        ("backtest_main", [VENV_PY, "-u", "scripts/work557_backtest.py",
                            "--exp", args.exp, "--buy-bps", "5",
                            "--sell-bps", "15", "--out", "backtest_results.json"]),
        ("backtest_8bps", [VENV_PY, "-u", "scripts/work557_backtest.py",
                           "--exp", args.exp, "--buy-bps", "8", "--sell-bps", "8",
                           "--out", "backtest_cost_8bps.json",
                           "--preds-tag", "_cost8"]),
        ("backtest_30bps", [VENV_PY, "-u", "scripts/work557_backtest.py",
                            "--exp", args.exp, "--buy-bps", "15",
                            "--sell-bps", "15",
                            "--out", "backtest_cost_30bps.json",
                            "--preds-tag", "_cost30"]),
        ("backtest_gross", [VENV_PY, "-u", "scripts/work557_backtest.py",
                            "--exp", args.exp, "--buy-bps", "0", "--sell-bps", "0",
                            "--out", "backtest_gross_diagnostic.json",
                            "--preds-tag", "_gross"]),
        ("integrity", [VENV_PY, "-u", "scripts/work557_check_integrity.py",
                       "--exp", args.exp]),
        ("lookahead", [VENV_PY, "-u", "scripts/work557_check_lookahead.py",
                       "--exp", args.exp]),
        ("recompute", [VENV_PY, "-u", "audit/recompute_work557.py"]),
        ("verdict", [VENV_PY, "-u", "scripts/work557_verdict.py",
                     "--exp", args.exp]),
        ("cost_sensitivity", [VENV_PY, "-u", "scripts/work557_cost_sensitivity.py"]),
    ]

    for name, cmd in steps:
        rec = run_step(name, cmd)
        summary["steps"].append(rec)
        if rec["rc"] != 0 and name in ("backtest_main", "recompute", "verdict"):
            # 这三步的产物是下游的硬依赖，失败就不往下跑了（留痕即可）
            log("关键步骤 %s 失败（rc=%s），停止后续链" % (name, rec["rc"]))
            break
        if name == "backtest_main" and rec["rc"] == 0:
            ok, why = validate_backtest()
            summary["backtest_validation"] = {"ok": ok, "detail": why}
            log("回测产物自检：%s —— %s" % ("通过" if ok else "不通过", why))
            if not ok:
                rec["rc"] = 1
                rec["note"] = "backtest 自检不通过：%s" % why
                log("回测产物自检不通过，停止后续链")
                break
        if name in COST_OUTPUTS and rec["rc"] == 0:
            fname, prereg = COST_OUTPUTS[name]
            ok2, why2 = validate_cost_json(fname)
            summary.setdefault("cost_validation", {})[fname] = {"ok": ok2,
                                                                "detail": why2,
                                                                "pre_registered": prereg}
            log("成本点产物自检 %s：%s —— %s" % (fname, "通过" if ok2 else "不通过", why2))
            if not ok2:
                rec["rc"] = 1
                rec["note"] = "成本点自检不通过：%s" % why2
                if prereg:
                    log("预注册成本点 %s 自检不通过，停止后续链" % fname)
                    break

    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    summary["ok"] = all(s["rc"] == 0 for s in summary["steps"])
    if summary["ok"]:
        summary["notify"] = notify_ready()
        log("就绪信号：body=%s status_rc=%s"
            % (summary["notify"].get("body"), summary["notify"].get("status_rc")))
    out = os.path.join(EXP_DIR, "finish_summary.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1, default=str)
    log("链完成 ok=%s -> %s" % (summary["ok"], out))
    for s in summary["steps"]:
        log("  %-18s rc=%-4s %.1fs" % (s["step"], s["rc"], s["seconds"]))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
