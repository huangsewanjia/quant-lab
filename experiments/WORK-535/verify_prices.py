"""Spot-check every price in docs/procurement_plan.csv against the URL we cite.

For each row whose 公开价格 cell carries a number we re-fetch the cited page(s) and
confirm the number is actually present in the served HTML. A price that cannot be found
at its own URL is a defect: either the row is wrong or the price was never published.

Two rules matter here:
  1. Numbers are compared numerically ("10,000" == "10000.0"), and a token keeps its
     thousands separator, so "$5" appearing somewhere on the page does NOT verify "$5,000".
  2. A price cell may cite several URLs (e.g. two different reports of the same vendor).
     Every number in the cell must be found on at least one of the row's cited pages.

Pages that refuse automated fetches (429/403) are reported as FETCH-FAIL, never as OK:
those prices stay "B 级证据（索引副本）" and must be re-checked in a human browser.

Usage:  .venv/bin/python verify_prices.py <csv-path>
"""
import csv
import html
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

URL_RE = re.compile(r'https?://[^\s，。；、（）)]+')
# Rows whose 公开价格 cell contains one of these tokens carry a checkable price.
CHECKABLE = re.compile(r'[\$€¥]|USD|EUR|RMB')


def fetch(url, retries=2):
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={
            'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,*/*',
            'Accept-Language': 'en-US,en;q=0.9'})
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                return r.status, r.read().decode('utf-8', 'replace'), ''
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                time.sleep(8 * (attempt + 1))
                continue
            return e.code, '', str(e.reason)
        except Exception as e:  # noqa: BLE001
            if attempt < retries:
                time.sleep(5)
                continue
            return 'ERR', '', f'{type(e).__name__}: {e}'


def norm(txt):
    txt = re.sub(r'<script[^>]*>.*?</script>', ' ', txt, flags=re.S | re.I)
    txt = re.sub(r'<style[^>]*>.*?</style>', ' ', txt, flags=re.S | re.I)
    txt = re.sub(r'<[^>]+>', ' ', txt)
    txt = html.unescape(txt)
    return re.sub(r'\s+', ' ', txt)


def as_float(tok):
    try:
        return float(tok.replace(',', ''))
    except ValueError:
        return None


def main(csv_path):
    with open(csv_path, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    checked = failed = skip_count = dup_skipped = 0
    lines = []
    seen = set()
    for r in rows:
        price = (r.get('公开价格') or '').strip()
        url = (r.get('官网价格页URL') or '').strip()
        if not price or not CHECKABLE.search(price) or not url.startswith('http'):
            skip_count += 1
            continue
        cited = [url] + [u for u in URL_RE.findall(price) if u != url]
        key = tuple(sorted(set(cited))) + (price,)
        if key in seen:
            dup_skipped += 1
            continue
        seen.add(key)

        nums = [t for t in re.findall(r'\d[\d,]*(?:\.\d+)?', price) if t not in ('0', '1')]
        page_nums = set()
        statuses = []
        bodies = []
        for u in cited:
            st, body, err = fetch(u)
            statuses.append((u, st, err))
            if body:
                bodies.append(body)
            else:
                lines.append(f'FETCH-FAIL {st:<4} {u}\n            ({err})')

        if not bodies:
            failed += 1
            continue

        text = ' '.join(norm(b) for b in bodies) + ' ' + ' '.join(bodies)
        for t in re.findall(r'\d[\d,]*(?:\.\d+)?', text):
            f = as_float(t)
            if f is not None:
                page_nums.add(f)

        found, missing = [], []
        for tok in nums:
            f = as_float(tok)
            if f is not None and any(abs(p - f) < 1e-9 for p in page_nums):
                found.append(tok)
            else:
                missing.append(tok)

        st = statuses[0][1]
        if not missing:
            lines.append(f'OK      {st:<4} {price}  <-  {cited[0]}'
                         + (f'  (+{len(cited) - 1} 同源页)' if len(cited) > 1 else ''))
            checked += 1
        else:
            lines.append(f'NOT-FOUND  {st:<4} {price}  <-  {cited[0]}'
                         + (f'  (+{len(cited) - 1} 同源页)' if len(cited) > 1 else '')
                         + f'\n            已在页面找到: {found or "无"}；页面没有: {missing}')
            failed += 1

    print('\n'.join(lines))
    print(f'\n=== CSV 总行数: {len(rows)}；其中带可核对数字的行: {checked + failed + dup_skipped}；'
          f'去重后唯一(价格,URL)组合: {checked + failed}；'
          f'页面上直接找到: {checked}；未找到/取不到: {failed}；无价格(跳过): {skip_count} ===')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'docs/procurement_plan.csv'))
