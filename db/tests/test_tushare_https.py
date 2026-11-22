from __future__ import annotations

import tushare.pro.client as ts_client

from db.etl.fetch import TUSHARE_HTTPS_URL, secure_tushare_transport


def test_tushare_transport_is_https():
    secure_tushare_transport()
    assert ts_client.DataApi._DataApi__http_url == TUSHARE_HTTPS_URL
    assert ts_client.DataApi._DataApi__http_url.startswith("https://")
