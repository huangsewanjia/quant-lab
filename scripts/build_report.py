# -*- coding: utf-8 -*-
"""
单文件 HTML 报告：模板 + base64 内嵌配图 → experiments/<EXP>/report.html

输入：templates/report_template.html（占位符 {{FIG1}}…{{FIG4}}）
      figures/<EXP>/fig*.png
输出：experiments/<EXP>/report.html —— 单文件，图片 base64 内嵌，双击即开、断网可读、可直接打印

运行：
  python scripts/build_report.py --exp WORK-404
"""
import argparse
import base64
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# ---------------------------------------------------------------- 路径（仓库相对，无写死的任务目录）
EXP = os.environ.get("QUANTLAB_EXP", "WORK-404")
TEMPLATES = os.path.join(REPO, "templates")
FIG = os.path.join(REPO, "figures", EXP)
OUT = os.path.join(REPO, "experiments", EXP)

# 模板占位符 → 图件 stem
# 图号按**正文引用顺序**递增（图1 因果链 → 图2 判定矩阵 → 图3 净值曲线 → 图4 复现面板），
# 与图件文件名解耦：fig4_matrix_radar 对应正文图 2，fig2_nav 对应正文图 3。
SLOTS = (("FIG1", "fig1_schematic"), ("FIG2", "fig4_matrix_radar"),
         ("FIG3", "fig2_nav"), ("FIG4", "fig3_reproduce"))
# 新约定：优先用 fig<N>.png（正文图号 = 文件名序号），找不到再回落到旧 stem。
NEW_FIG_RE = re.compile(r"^fig(\d+)\.png$")


def _force_utf8_stdio():
    """把 stdout/stderr 显式切到 UTF-8（Windows 默认控制台编码是 GBK/cp936）。

    脚本输出里只要出现非 GBK 字符（如 U+2212 数学减号），在「输出被重定向到文件
    或管道」时就会抛 UnicodeEncodeError、进程 exit 1——结果文件根本产不出来。
    强制重设编码后，README 里的命令不需要任何环境变量即可跑完并返回真实退出码。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


def _resolve_template(args, exp):
    """优先用 templates/report_template_<EXP>.html（单实验专用模板）。"""
    if args.template:
        return args.template
    specific = os.path.join(TEMPLATES, f"report_template_{exp}.html")
    if os.path.exists(specific):
        return specific
    return os.path.join(TEMPLATES, "report_template.html")


def _resolve_fig(fig_dir, slot_no, legacy_stem):
    """正文图号优先对应 fig<N>.png；否则回落到历史 stem。"""
    candidate = os.path.join(fig_dir, f"fig{slot_no}.png")
    if os.path.exists(candidate):
        return candidate
    return os.path.join(fig_dir, f"{legacy_stem}.png")


def _substitute_data(tpl, exp_dir):
    """用 experiments/<exp>/report_data.json 替换 {{TOKEN}}（缺失即报错）。"""
    data_path = os.path.join(exp_dir, "report_data.json")
    if not os.path.exists(data_path):
        return tpl, []
    import json
    data = json.load(open(data_path, encoding="utf-8"))
    missing = []
    for key, value in data.items():
        token = "{{" + key + "}}"
        if token in tpl:
            tpl = tpl.replace(token, str(value))
    for token in set(re.findall(r"\{\{([A-Za-z0-9_]+)\}\}", tpl)):
        missing.append(token)
    return tpl, sorted(missing)


def main():
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="模板 + 配图 → 单文件 HTML 报告")
    ap.add_argument("--exp", default=EXP, help="实验 ID")
    ap.add_argument("--template", default=None,
                    help="HTML 模板路径（默认按实验 ID 选模板）")
    args = ap.parse_args()
    exp = args.exp
    fig_dir = os.path.join(REPO, "figures", exp)
    out_dir = os.path.join(REPO, "experiments", exp)
    os.makedirs(out_dir, exist_ok=True)

    template = _resolve_template(args, exp)
    if not os.path.exists(template):
        raise SystemExit(f"模板不存在：{template}")
    tpl = open(template, encoding="utf-8").read()

    for i, (key, stem) in enumerate(SLOTS, start=1):
        p = _resolve_fig(fig_dir, i, stem)
        if not os.path.exists(p):
            raise SystemExit(f"缺少配图：{p}；先跑出图脚本 --exp {exp}")
        b64 = base64.b64encode(open(p, "rb").read()).decode("ascii")
        token = "{{" + key + "}}"
        if token not in tpl:
            raise SystemExit(f"模板缺少占位符 {token}")
        tpl = tpl.replace(token, f"data:image/png;base64,{b64}")

    tpl, missing = _substitute_data(tpl, out_dir)
    if missing:
        raise SystemExit("模板占位符无对应数据：" + ", ".join(missing))

    out = os.path.join(out_dir, "report.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(tpl)
    size = os.path.getsize(out)
    print(f"report.html 已生成：{out}  大小={size/1024:.0f} KB")
    assert "{{" not in tpl, "仍有未替换的占位符"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
