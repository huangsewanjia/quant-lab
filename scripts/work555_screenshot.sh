#!/usr/bin/env bash
# WORK-555 · 交付件截图与打印 PDF
#  - 1440×900 首屏 PNG（断网可读的本地 file:// 打开）
#  - A4 纵向打印首页 PDF
# 用法：bash scripts/work555_screenshot.sh
set -euo pipefail
REPO=/srv/share/quantdb/code/quant-lab
CHROME=/home/moyongkai/.local/bin/google-chrome
HTML="$REPO/experiments/WORK-555/report.html"
OUTDIR="$REPO/experiments/WORK-555"
PROFILE=$(mktemp -d)

[ -x "$CHROME" ] || { echo "chrome not found at $CHROME"; exit 2; }
[ -f "$HTML" ] || { echo "report not found: $HTML"; exit 2; }

"$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars \
  --user-data-dir="$PROFILE" \
  --window-size=1440,900 \
  --screenshot="$OUTDIR/report_firstscreen_1440x900.png" \
  "file://$HTML" >/dev/null 2>&1

"$CHROME" --headless --disable-gpu --no-sandbox --no-pdf-header-footer \
  --user-data-dir="$PROFILE" \
  --print-to-pdf="$OUTDIR/report_print.pdf" \
  "file://$HTML" >/dev/null 2>&1

rm -rf "$PROFILE"
ls -la "$OUTDIR/report_firstscreen_1440x900.png" "$OUTDIR/report_print.pdf"
