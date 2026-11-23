"""Check that every URL in docs/procurement_plan.csv really exists / opens.

工单验收条款：「标『未公开』的，URL 必须真实存在（能打开）」。
所以这里不只查「未公开」的行，而是把 84 行里出现的每个 URL 都查一遍，
按 200 / 3xx / 4xx / DNS 失败分类登记，便于在 brief 的「停手登记」里如实写明。

用法： .venv/bin/python check_plan_urls.py <csv-path> [输出json]
"""
import csv
import json
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


def fetch(url, retries=1):
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={'User-Agent': UA,
                                                   'Accept': 'text/html,*/*'})
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
    urls = []
    for r in rows:
        u = (r['官网价格页URL'] or '').strip()
        if u.startswith('http') and u not in urls:
            urls.append(u)
    res = {}
    for i, u in enumerate(urls, 1):
        st, final, err = fetch(u)
        res[u] = {'status': st, 'final': final, 'error': err}
        print(f'{i:>3}/{len(urls)}  {st!s:<5} {u}  {err}', flush=True)
    json.dump(res, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    buckets = {}
    for u, v in res.items():
        k = str(v['status'])
        buckets.setdefault(k, []).append(u)
    print('\n=== 按状态汇总 ===')
    for k in sorted(buckets, key=lambda x: (x.isalpha(), x)):
        print(f'  {k:<6} {len(buckets[k]):>3} 个')
    for k in sorted(buckets, key=lambda x: (x.isalpha(), x)):
        if k != '200':
            print(f'\n--- {k} ---')
            for u in buckets[k]:
                print(f'   {u}   {res[u]["error"]}')

    # 验收条款：标「未公开」的行，其 URL 必须能打开
    bad = []
    for r in rows:
        if '未公开' in (r['公开价格'] or ''):
            u = (r['官网价格页URL'] or '').strip()
            if res.get(u, {}).get('status') != 200:
                bad.append((r['指标名'], r['数据商'], u, res.get(u, {}).get('status')))
    print(f'\n=== 验收：标「未公开」的行共 '
          f'{sum(1 for r in rows if "未公开" in (r["公开价格"] or ""))} 行；'
          f'其中 URL 未返回 200 的: {len(bad)} 行 ===')
    for b in bad:
        print('   ', b)
    return 0


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'docs/procurement_plan.csv',
         sys.argv[2] if len(sys.argv) > 2 else 'plan_url_status.json')
