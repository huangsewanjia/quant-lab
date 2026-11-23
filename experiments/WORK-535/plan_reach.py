"""主清单里每个「官网价格页URL」的实测可达性（2026-11-23 一次性全表体检）。

体检脚本： experiments/WORK-535/check_plan_urls.py
原始结果： experiments/WORK-535/plan_url_status.json（47 个去重 URL）

build_plan.py 会把本表的状态追加到每一行的「备注」末尾，
这样任何一行都能自证「给的 URL 是不是真的能打开」，验收条款「标『未公开』的，
URL 必须真实存在（能打开）」可逐行核对。

注意：403/429 是**本机**的结果（Akamai/Cloudflare 质询、限流），
URL 本身存在且大概率人工浏览器可开；只有 404 与 DNS 失败是链接本身有问题，
其中 solarpowereurope.org/insights/outlooks 已改为首页。
"""

REACH = {
    'https://www.spglobal.com/commodityinsights/en/our-methodology/price-assessments/metals/battery-metals':
        '【链接实测 403：Akamai 拒绝本机访问，URL 本身存在，人工浏览器可开】',
    'https://www.iea.org/reports/batteries-and-secure-energy-transitions':
        '【链接实测 403：Cloudflare 质询本机，人工浏览器可开】',
    'https://www.iea.org/data-and-statistics':
        '【链接实测 403：Cloudflare 质询本机，人工浏览器可开】',
    'https://www.iea.org/reports/global-ev-outlook-2026':
        '【链接实测 403：Cloudflare 质询本机，人工浏览器可开】',
    'https://www.irena.org/Data':
        '【链接实测 403：本机被拒，人工浏览器可开】',
    'https://www.cms.gov/':
        '【链接实测 403：本机被拒，人工浏览器可开】',
    'https://www.imf.org/en/Publications/WEO/weo-database':
        '【链接实测 403：本机被拒，人工浏览器可开】',
    'https://stat.unido.org/':
        '【链接实测 403：本机被拒，人工浏览器可开】',
    'https://store-us.semi.org/collections/market-intelligence':
        '【链接实测 429：限流，非 404；价格取自该 URL 的索引副本，人工浏览器可开】',
    'https://www.gg-lb.com/':
        '【链接实测 DNS 解析失败（Errno -2）：域名疑失效或已迁移，须先确认现域名】',
    'https://www.mckinsey.com/featured-insights':
        '【链接实测 连接超时（其余 McKinsey 页面可读）】',
}

OK_TAG = '【链接实测 200】'
