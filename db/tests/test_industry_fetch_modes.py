"""行业层抓取窗口契约（WORK-576）：默认必须是日常小窗口，回填只能显式。

抓取脚本本体不在本仓库（既有形态，位于 /srv/share/quantdb/code/industry_fetch/），
这里直接 import 它们做契约测试：目录不存在时整体 skip，避免在别处 checkout 时报错。
不联网、不写 raw：只验证 argparse 默认值、`resolve_mode` 解析与各模块窗口单调性。
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import sys
from pathlib import Path

import pytest

FETCH_DIR = Path("/srv/share/quantdb/code/industry_fetch")
pytestmark = pytest.mark.skipif(
    not (FETCH_DIR / "_common.py").exists(),
    reason="行业层抓取脚本不在本机（/srv/share/quantdb/code/industry_fetch）",
)

MODULES = [
    "fetch_gfex_lithium",
    "fetch_cninfo_ann",
    "fetch_caam_output",
    "fetch_cpcaauto_nev",
    "fetch_mof_fiscal",
    "fetch_nea_power",
    "fetch_pbc_shrzgm",
    "fetch_stats_gov",
]


@pytest.fixture(scope="module")
def common():
    sys.path.insert(0, str(FETCH_DIR))
    yield importlib.import_module("_common")


def test_mode_default_is_incremental(common):
    parser = argparse.ArgumentParser()
    common.add_mode_arg(parser)
    assert parser.parse_args([]).mode == common.INCREMENTAL
    assert parser.parse_args(["--mode", "backfill"]).mode == common.BACKFILL
    with pytest.raises(SystemExit):
        parser.parse_args(["--mode", "deep"])


def test_resolve_mode_fills_small_window_and_keeps_explicit(common):
    parser = argparse.ArgumentParser()
    common.add_mode_arg(parser)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--flag", dest="flag", action="store_true", default=None)
    windows = {"incremental": {"days": 30, "flag": True},
               "backfill": {"days": 1825, "flag": False}}

    args = parser.parse_args([])
    common.resolve_mode(args, windows)
    assert (args.days, args.flag) == (30, True)

    args = parser.parse_args(["--days", "7"])
    common.resolve_mode(args, windows)
    assert args.days == 7, "显式数值必须优先于 --mode"

    args = parser.parse_args(["--mode", "backfill", "--days", "7"])
    common.resolve_mode(args, windows)
    assert (args.days, args.flag) == (7, False)

    args = parser.parse_args(["--mode", "backfill"])
    common.resolve_mode(args, windows)
    assert args.days == 1825


def test_resolve_mode_calls_callables(common):
    parser = argparse.ArgumentParser()
    common.add_mode_arg(parser)
    parser.add_argument("--year", type=int, default=None)
    args = parser.parse_args([])
    common.resolve_mode(args, {"incremental": {"year": lambda: 2026}})
    assert args.year == 2026


# 日常档的旋钮上限（超过即代表默认窗口变深，验收判据「默认必须是小窗口」失守）。
# page_size 不是深度旋钮（页越大请求越少），不参与比较。
INCREMENTAL_BOUNDS = {
    "fetch_gfex_lithium": {"days": 30},
    "fetch_cninfo_ann": {"days": 90, "max_pages": 60},
    "fetch_caam_output": {"months": 3, "articles": 20, "scan": 400},
    "fetch_cpcaauto_nev": {"articles": 30, "pages": 3},
    "fetch_mof_fiscal": {"articles": 24, "pages": 4},
    # nea_power 的深度旋钮（months/articles）由 WORK-579 改写并在其单内调整；
    # 定时任务只固定 --mode incremental，不在本单硬编码其窗口参数。
    "fetch_nea_power": {},
    "fetch_pbc_shrzgm": {"since_year": lambda: dt.date.today().year},
    "fetch_stats_gov": {"pages": 6, "articles": 6},
}


@pytest.mark.parametrize("name", MODULES)
def test_incremental_window_within_bounds(common, name):
    mod = importlib.import_module(name)
    inc = mod.WINDOWS["incremental"]
    for key, bound in INCREMENTAL_BOUNDS[name].items():
        value = inc[key]
        if callable(value):
            value = value()
        if callable(bound):      # 与年份相关：今天必须在「当年」，不写死年份
            bound = bound()
        assert value <= bound, f"{name}.{key}: 日常档 {value} 超过安全上限 {bound}"


@pytest.mark.parametrize("name", MODULES)
def test_argparse_depth_defaults_are_none(common, monkeypatch, name):
    """每个深度旋钮的 argparse 默认值必须是 None。

    这是第 1 轮 B1 的失效机理：resolve_mode 只填 None，默认值一旦写成大窗口，
    --mode incremental 与裸跑都会深抓，而只看 WINDOWS 的断言发现不了。
    在 resolve_mode 处截断，既不联网也不建 session。
    """
    mod = importlib.import_module(name)
    seen: dict = {}

    def stop(args, windows):
        seen.update(vars(args))
        raise RuntimeError("resolved")

    monkeypatch.setattr(mod, "resolve_mode", stop)
    monkeypatch.setattr(sys, "argv", [f"{name}.py"])
    with pytest.raises(RuntimeError, match="resolved"):
        mod.main()
    assert seen["mode"] == common.INCREMENTAL
    for key in INCREMENTAL_BOUNDS[name]:
        assert seen[key] is None, f"{name}.{key} 的 argparse 默认值不是 None，会绕过 --mode"


@pytest.mark.parametrize("name", MODULES)
def test_backfill_window_at_least_incremental(common, name):
    mod = importlib.import_module(name)
    inc, back = mod.WINDOWS["incremental"], mod.WINDOWS["backfill"]
    checked = 0
    for key in INCREMENTAL_BOUNDS[name]:
        if key == "since_year":
            # 起始年份方向相反：越晚 = 窗口越窄，单调性由 test_pbc_backfill_starts_2021 单独断言。
            continue
        a, b = inc[key], back[key]
        if callable(a):
            a = a()
        if isinstance(b, (int, float)) and not isinstance(b, bool):
            assert a <= b, f"{name}.{key}: 回填档 {b} 小于日常档 {a}"
            checked += 1
    assert checked or name in {"fetch_pbc_shrzgm", "fetch_nea_power"}, \
        f"{name} 没有可比较的回填档旋钮"


def test_pbc_backfill_starts_2021(common):
    mod = importlib.import_module("fetch_pbc_shrzgm")
    assert mod.WINDOWS["backfill"]["since_year"] == 2021
    assert mod.WINDOWS["incremental"]["since_year"]() >= 2025


def test_gfex_incremental_has_resume(common):
    mod = importlib.import_module("fetch_gfex_lithium")
    assert mod.WINDOWS["incremental"]["resume"] is True
    assert mod.WINDOWS["incremental"]["days"] <= 30
