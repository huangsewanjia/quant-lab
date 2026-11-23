"""询价入口的邮箱**回环核对**：每个邮箱必须能在厂商自己的页面上找到。

大管家验收意见 §三：「询价模板 13 家的收件邮箱没有逐个验证真实性」。
本脚本对每个邮箱，遍历候选页面重新抓取，确认页面 HTML 里确实出现该邮箱串。
查不到 → 记「未复核到」，由人工决定是删掉还是换入口。

用法： .venv/bin/python verify_contacts.py <plan.csv> [输出json]
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

# 邮箱 → 候选厂商页面（先试最可能的联系页，再试产品/报告页）
CANDIDATES = {
    'prices@benchmarkminerals.com': [
        'https://www.benchmarkminerals.com/contact',
        'https://www.benchmarkminerals.com/battery-gigafactory/prices',
    ],
    'support@woodmac.com': [
        'https://www.woodmac.com/about/contact-us/',
        'https://www.woodmac.com/lens/lens-energy-storage/',
    ],
    'sales@sneresearch.com': [
        'https://www.sneresearch.com/en/about/contact/',
        'https://www.sneresearch.com/en/business/report_view/148/page/0',
    ],
    'service@trendforce.com': ['https://www.trendforce.com/contact/reporting'],
    'sr_mi@trendforce.com': ['https://www.trendforce.com/contact/reporting'],
    'press@iea.org': ['https://www.iea.org/about/contact', 'https://www.iea.org/contact'],
    'yaoxl@pharmadl.com': ['https://www.yytj.org.cn/ContactUs.aspx'],
    'pricegroup@spglobal.com': [
        'https://www.spglobal.com/commodityinsights/en/contact-us',
        'https://www.spglobal.com/commodityinsights/en/our-methodology/price-assessments/metals/battery-metals',
    ],
    'service@ccidconsulting.com': ['https://www.ccidconsulting.com/contact'],
    'sales.bnef@bloomberg.net': ['https://about.bnef.com/contact-us/'],
    'baohf@pharmadl.com': ['https://www.yytj.org.cn/ContactUs.aspx'],
    'kefu@smm.cn': ['https://www.smm.cn/about/contact'],
    'service@smm.cn': ['https://www.smm.cn/about/contact'],
    'service.en@smm.cn': ['https://www.smm.cn/about/contact'],
    'tp@wsts.org': ['https://www.wsts.org/65/CONTACT',
                    'https://www.wsts.org/135/263/Disclosure',
                    'https://www.wsts.org/'],
    'sh@wsts.org': ['https://www.wsts.org/65/CONTACT'],
    'hbzixun@126.com': ['https://www.hibor.com.cn/'],
    'cs@hufangde.com': ['https://www.fxbaogao.com/'],
    'hfd04@hufangde.com': ['https://www.fxbaogao.com/'],
    'info@datayes.com': ['https://www.datayes.com/'],
    'team.marketing@datayes.com': ['https://www.datayes.com/'],
}

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


def fetch(url, retries=1):
    for a in range(retries + 1):
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,*/*'})
        try:
            with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
                return r.status, r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and a < retries:
                time.sleep(6 * (a + 1))
                continue
            return e.code, ''
        except Exception as e:  # noqa: BLE001
            if a < retries:
                time.sleep(3)
                continue
            return 'ERR', ''


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else 'docs/procurement_plan.csv'
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))
    blob = ' '.join(' '.join(r.values()) for r in rows)
    in_plan = sorted({m.group(0).lower() for m in EMAIL_RE.finditer(blob)})
    cand = {k.lower(): v for k, v in CANDIDATES.items()}
    targets = [e for e in in_plan if e in cand]

    out = {}
    for email in targets:
        rec = {'email': email, 'found_on': None, 'tried': []}
        for url in cand[email]:
            st, body = fetch(url)
            hit = email.lower() in body.lower()
            rec['tried'].append({'url': url, 'status': st, 'contains_email': hit})
            if hit and rec['found_on'] is None:
                rec['found_on'] = url
                rec['found_status'] = st
                break
        out[email] = rec
        mark = '✅' if rec['found_on'] else '❌ 未复核到'
        print(f'{mark}  {email:<36} ' + (f"@{rec['found_on']} (HTTP {rec.get('found_status')})"
                                         if rec['found_on'] else
                                         '；试过：' + ', '.join(f"{t['url']}[{t['status']}]" for t in rec['tried'])),
              flush=True)

    # 清单里出现但不在候选表里的邮箱（需要人工判断）
    others = [e for e in in_plan if e not in CANDIDATES]
    print(f'\n清单中出现的邮箱共 {len(in_plan)} 个；本脚本核对 {len(targets)} 个；'
          f'未纳入核对 {len(others)} 个: {others}')
    ok = sum(1 for e in targets if out[e]['found_on'])
    print(f'核对结果：命中 {ok} / {len(targets)}；未复核到 {len(targets) - ok}')
    json.dump({'checked': out, 'not_checked': others, 'n_in_plan': len(in_plan)},
              open(sys.argv[2] if len(sys.argv) > 2 else 'contact_verification.json',
                   'w', encoding='utf-8'), ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
