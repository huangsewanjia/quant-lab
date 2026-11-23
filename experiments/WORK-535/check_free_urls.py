"""检查「免费替代源」列里出现的每个 URL 是否真的能打开。

工单验收只强制「官网价格页URL」可达，但免费替代源是 61 条部分免费行的兜底方案，
一旦链接是死的，那条「免费替代」就是纸面上的。因此逐条实测并登记状态。

用法： .venv/bin/python check_free_urls.py <csv-path> [输出json]
"""
import csv
import json
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
URL_RE = re.compile(r'https?://[^\s，。；、（）)】」]+')


def fetch(url, retries=1):
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,*/*'})
        try:
            with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
                return r.status, r.geturl(), ''
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries:
                time.sleep(6 * (attempt + 1))
                continue
            return e.code, url, str(e.reason)
        except Exception as e:  # noqa: BLE001
            if attempt < retries:
                time.sleep(4)
                continue
            return 'ERR', url, f'{type(e).__name__}: {e}'


def main(csv_path, out_path):
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))
    urls, owner = [], {}
    for r in rows:
        for u in URL_RE.findall(r['免费替代源'] or ''):
            u = u.rstrip('.,;')
            if u not in owner:
                urls.append(u)
                owner[u] = r['指标名']
    res = {}
    for i, u in enumerate(urls, 1):
        st, final, err = fetch(u)
        res[u] = {'status': st, 'final': final, 'error': err, 'owner': owner[u]}
        print(f'{i:>3}/{len(urls)}  {st!s:<5} {u}  {err}', flush=True)
    json.dump(res, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    buckets = {}
    for u, v in res.items():
        buckets.setdefault(str(v['status']), []).append(u)
    print('\n=== 按状态汇总（免费替代源 URL）===')
    for k in sorted(buckets, key=lambda x: (x.isalpha(), x)):
        print(f'  {k:<6} {len(buckets[k]):>3} 个')
    for k in sorted(buckets, key=lambda x: (x.isalpha(), x)):
        if k != '200':
            print(f'\n--- {k} ---')
            for u in buckets[k]:
                print(f'   {u}\n       指标: {res[u]["owner"]}  {res[u]["error"]}')
    return 0


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'docs/procurement_plan.csv',
         sys.argv[2] if len(sys.argv) > 2 else 'free_url_status.json')
