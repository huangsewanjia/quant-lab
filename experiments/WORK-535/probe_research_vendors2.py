"""第二轮探测：打开研报订阅厂商的**价格页/产品页**，抓价格与联系入口。

同样纪律：价格只认本脚本实际打开的页面上的数字；打不开记「未公开，需询价」。
"""
import html
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import urljoin

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

SEEDS = [
    ('慧博投研', 'https://www.hibor.com.cn/'),
    ('发现报告', 'https://www.fxbaogao.com/'),
    ('萝卜投研 会员中心', 'https://m-robo.datayes.com/membership/open?hideNavBar=1'),
    ('萝卜投研 官网', 'https://robo.datayes.com/'),
    ('中金公司', 'https://www.cicc.com/'),
    ('中信证券', 'https://www.citics.com/'),
    ('GS Insights', 'https://www.goldmansachs.com/insights'),
    ('MS Ideas', 'https://www.morganstanley.com/ideas'),
    ('JPM Insights', 'https://www.jpmorgan.com/insights'),
    ('Citi Insights', 'https://www.citigroup.com/global/insights'),
    ('BofA Ideas', 'https://ideas.bankofamerica.com/'),
    ('UBS 投行', 'https://www.ubs.com/global/en/investment-bank.html'),
]

LINK_KW = re.compile(r'(vip|price|pricing|member|huiyuan|huyu|cost|fee|order|buy|'
                     r'research|yanbao|研报|会员|价格|订购|订阅|subscribe)', re.I)
PRICE = re.compile(r'(?:US\s*\$|\$|€|EUR\s*|RMB\s*|¥|￥)\s*[\d][\d,\.]*'
                   r'(?:\s*(?:k|K))?'
                   r'|[\d][\d,\.]*\s*(?:元|块|人民币)\s*(?:/|／)?\s*'
                   r'(?:年|月|天|次|个?月|年费|月费|一年|每月|每年)')
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


def fetch(url, retries=1):
    for a in range(retries + 1):
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,*/*'})
        try:
            with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
                return r.status, r.geturl(), r.read().decode('utf-8', 'replace'), ''
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and a < retries:
                continue
            return e.code, url, '', str(e.reason)
        except Exception as e:  # noqa: BLE001
            if a < retries:
                continue
            return 'ERR', url, '', f'{type(e).__name__}: {e}'


def norm(t):
    t = re.sub(r'<script[^>]*>.*?</script>', ' ', t, flags=re.S | re.I)
    t = re.sub(r'<style[^>]*>.*?</style>', ' ', t, flags=re.S | re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    return re.sub(r'\s+', ' ', html.unescape(t))


def main():
    out = {}
    for name, url in SEEDS:
        st, final, body, err = fetch(url)
        rec = {'name': name, 'url': url, 'status': st, 'final': final, 'error': err}
        if body:
            txt = norm(body)
            prices, seen = [], set()
            for m in PRICE.finditer(txt):
                s = m.group(0).strip()
                if s not in seen and len(s) < 40:
                    seen.add(s)
                    prices.append(s)
                    if len(prices) >= 20:
                        break
            rec['prices'] = prices
            links = []
            for m in re.finditer(r'href=["\']([^"\'>]+)["\']', body, re.I):
                h = m.group(1)
                if LINK_KW.search(h) and not h.startswith(('javascript:', '#')):
                    full = urljoin(final, h)
                    if full not in links:
                        links.append(full)
            rec['candidate_links'] = links[:15]
            rec['emails'] = sorted(set(EMAIL.findall(body)))[:6]
            for kw in ('年费', '月费', '会员', '价格', '订阅', '研究报告', '研报', '深度', '机构'):
                i = txt.find(kw)
                if i >= 0:
                    rec.setdefault('hits', {})[kw] = txt[max(0, i - 70):i + 110]
        out[name] = rec
        print(f"\n=== {st} {name} {url}")
        if body:
            print('   prices:', rec.get('prices', [])[:10])
            print('   emails:', rec.get('emails'))
            print('   links:', rec.get('candidate_links', [])[:8])
        else:
            print('   err:', err)
    json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else 'research_vendor_probe2.json',
                        'w', encoding='utf-8'), ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
