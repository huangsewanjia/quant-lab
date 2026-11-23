"""探测研报订阅厂商的官网 / 价格页：状态码、页面里的价格串、联系入口。

纪律（与本单一致）：价格必须是我**实际打开的页面**上的数字；
打不开就记「未公开，需询价」，不许估。
"""
import html
import json
import re
import ssl
import sys
import urllib.error
import urllib.request

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

URLS = [
    # 聚合平台（声称有公开价）
    ('慧博投研 首页', 'https://www.hibor.com.cn/'),
    ('慧博投研 VIP', 'https://www.hibor.com.cn/vip.htm'),
    ('发现报告 首页', 'https://www.fxbaogao.com/'),
    ('发现报告 VIP', 'https://www.fxbaogao.com/vip'),
    ('萝卜投研 首页', 'https://www.tourbang.com/'),
    ('萝卜投研 会员', 'https://www.tourbang.com/vip'),
    # 中资头部券商研究部
    ('中金公司 研究', 'https://www.cicc.com/zh/indices/index.html'),
    ('中信证券 研究', 'https://www.citics.com/'),
    # 外资投行研究
    ('GS Insights', 'https://www.goldmansachs.com/insights'),
    ('GS Research 产品页', 'https://www.goldmansachs.com/insights/research'),
    ('MS Ideas', 'https://www.morganstanley.com/ideas'),
    ('MS Research', 'https://www.morganstanley.com/ideas/research'),
    ('JPM Insights', 'https://www.jpmorgan.com/insights'),
    ('JPM Research', 'https://www.jpmorgan.com/insights/research'),
    ('UBS Evidence Lab', 'https://www.ubs.com/global/en/investment-bank/insights/evidence-lab.html'),
    ('Citi Research', 'https://www.citi.com/citi/research.html'),
    ('Citi Insights', 'https://www.citigroup.com/global/insights'),
    ('BofA Global Research', 'https://ideas.bankofamerica.com/'),
]

PRICE = re.compile(r'(?:US\s*\$|\$|€|EUR\s*|RMB\s*|¥|￥)\s*[\d][\d,\.]*'
                   r'(?:\s*(?:k|K))?|'
                   r'[\d][\d,\.]*\s*(?:元|块|人民币)\s*[/／]?\s*'
                   r'(?:年|月|个?月|年费|月费|天|次)|\d[\d,\.]*\s*(?:元|块)\s*'
                   r'(?:一年|每月|每年|/年|/月)')
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
PHONE = re.compile(r'(?:(?:\+?86[-\s]?)?1[3-9]\d{9})|'
                   r'(?:0\d{2,3}[-\s]?\d{7,8})|(?:\+?\d[\d\s\-]{8,15}\d)')


def fetch(url, retries=1):
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,*/*'})
        try:
            with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
                return r.status, r.geturl(), r.read().decode('utf-8', 'replace'), ''
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries:
                continue
            return e.code, url, '', str(e.reason)
        except Exception as e:  # noqa: BLE001
            if attempt < retries:
                continue
            return 'ERR', url, '', f'{type(e).__name__}: {e}'


def norm(t):
    t = re.sub(r'<script[^>]*>.*?</script>', ' ', t, flags=re.S | re.I)
    t = re.sub(r'<style[^>]*>.*?</style>', ' ', t, flags=re.S | re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    return re.sub(r'\s+', ' ', html.unescape(t))


def main():
    out = {}
    for name, url in URLS:
        st, final, body, err = fetch(url)
        rec = {'name': name, 'url': url, 'status': st, 'final': final, 'error': err}
        if body:
            txt = norm(body)
            prices = []
            for m in PRICE.finditer(txt):
                s = m.group(0).strip()
                if s not in prices and len(s) < 60:
                    prices.append(s)
            rec['prices'] = prices[:25]
            mails = sorted(set(EMAIL.findall(body)))
            rec['emails'] = mails[:8]
            phones = []
            for m in PHONE.finditer(norm(body)):
                p = m.group(0).strip()
                if 9 <= len(re.sub(r'\D', '', p)) <= 15 and p not in phones:
                    phones.append(p)
            rec['phones'] = phones[:8]
            rec['title'] = (re.search(r'<title[^>]*>(.*?)</title>', body, re.S | re.I)
                            .group(1).strip()[:80] if re.search(r'<title[^>]*>', body, re.S | re.I) else '')
            for kw in ('年费', '月费', '会员', 'VIP', '价格', '订阅', '研究报告', '研报', '招股书', '深度'):
                rec.setdefault('hits', {})
                if kw in txt:
                    idx = txt.find(kw)
                    rec['hits'][kw] = txt[max(0, idx - 60):idx + 90]
        out[name] = rec
        print(f"{st!s:<5} {name:<24} {url}  prices={rec.get('prices', [])[:6]}", flush=True)
    json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else 'research_vendor_probe.json',
                        'w', encoding='utf-8'), ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
