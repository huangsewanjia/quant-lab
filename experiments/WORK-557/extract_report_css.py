"""把 WORK-503 报告里的 <style> 块抽出来，作为 WORK-557 报告的版式基准。

WORK-557 报告要求「版式沿用 WORK-503」（白底、主蓝 #0F4D92、次青 #42949E、
警示红 #B64342、无阴影渐变、A4 10.5 pt、图注在图下带来源），所以直接复用上游
已交付的 CSS，不重新设计。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "experiments" / "WORK-503" / "report.html"
DST = ROOT / "templates" / "work557_report.css"

html = SRC.read_text(encoding="utf-8")
blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)
if not blocks:
    raise SystemExit("no <style> block found in %s" % SRC)
css = "\n".join(blocks)
DST.parent.mkdir(parents=True, exist_ok=True)
DST.write_text(css, encoding="utf-8")
print("wrote %s  (%d chars, %d style blocks)" % (DST, len(css), len(blocks)))
for tok in ("#0F4D92", "#42949E", "#B64342", "font-size: 10.5pt", "background: #fff"):
    print("  contains %-22s : %s" % (tok, tok.lower() in css.lower()))
