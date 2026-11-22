from __future__ import annotations

from db.policy.fetch_policy import allowed, load_sources


def _sources():
    return {source["id"]: source for source in load_sources()}


def test_policy_hosts_are_exact():
    sources = _sources()
    assert not allowed(sources["gov"], "https://evil.gov.cn/x")
    assert not allowed(sources["gov"], "https://www.csrc.gov.cn/x")
    assert allowed(sources["mof"], "https://gks.mof.gov.cn/x")
    assert allowed(sources["gov"], "https://www.gov.cn/x")
    assert not allowed(sources["gov"], "file:///etc/passwd")
