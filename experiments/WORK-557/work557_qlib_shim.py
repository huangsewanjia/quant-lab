#!/usr/bin/env python3
"""WORK-557：把 pyqlib 的 cp312 wheel 解到稳定位置 .venv/qlib312/，并写入纯 numpy 替身。

为什么需要替身
--------------
pyqlib 0.9.7 只有 cp38/cp39/cp310/cp311/cp312 的轮子，**没有 cp313**；而跑训练的
`ds` 环境是 Python 3.13.15。轮子里只有两个编译产物：

    qlib/data/_libs/expanding.cp312-win_amd64.pyd
    qlib/data/_libs/rolling.cp312-win_amd64.pyd

它们只提供 qlib/data/ops.py:18-19 用到的 6 个函数：
    rolling_slope / rolling_rsquare / rolling_resi
    expanding_slope / expanding_rsquare / expanding_resi

本文件把上游 **.pyx 源码逐行转写**成纯 numpy 实现（上游 .pyx/.cpp 就在同一个轮子
里：qlib/data/_libs/rolling.pyx、expanding.pyx），覆盖进 `_libs` 包。

* **不改上游任何代码**：替身只存在于本机 .venv/ 下（.gitignore 已忽略）；
* **逐行对拍**：`work557_shim_validate.py` 拿 py3.9 环境里**真的 cp39 .pyd**
  做逐元素比较，最大绝对差 < 1e-12 才允许使用；
* 所有累加量用 np.float64，保持 C 的 IEEE 除法语义（C 里除零给 inf/nan，
  Python 的 float 会抛 ZeroDivisionError，故必须用 numpy 标量）。

上游语义摘要（qlib/data/_libs/rolling.pyx）：
  窗口内每个元素的 x = window（进入时的固定值），随元素离开整体左移；
  slope = (N*Σxy − Σx·Σy) / (N*Σx² − (Σx)²)
  rsquare = r²，r = (N*Σxy − Σx·Σy) / sqrt((N*Σx² − (Σx)²)(N*Σy² − (Σy)²))
  resi    = val − (slope·window + (ȳ − slope·x̄))   # 最后一点的残差
"""
import pathlib
import shutil
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
WHL = ROOT / ".tmp_pip" / "pyqlib-0.9.7-cp312-cp312-win_amd64.whl"
DEST = ROOT / ".venv" / "qlib312"

COMMON = '''\
"""Shared, self-contained numpy implementation of qlib's `_libs` helpers.

Transcribed line-by-line from the upstream Cython sources that ship inside the
same wheel: qlib/data/_libs/rolling.pyx and qlib/data/_libs/expanding.pyx.
All accumulators are np.float64 so that division follows C's IEEE semantics
(C: 1/0 -> inf; numpy: inf; Python float: ZeroDivisionError).
"""
import numpy as np

__all__ = ["rolling_slope", "rolling_rsquare", "rolling_resi",
           "rolling_mean", "expanding_slope", "expanding_rsquare",
           "expanding_resi", "expanding_mean"]

NAN = float("nan")


def _isnan(v):
    return np.isnan(v)


class _Rolling:
    """1-D array rolling — mirrors the Cython `Rolling` base class."""

    def __init__(self, window):
        self.window = int(window)
        self.barv = [NAN] * self.window       # deque pre-filled with NaN
        self.na_count = self.window
        self.i_sum = 0
        self.x_sum = np.float64(0.0)
        self.x2_sum = np.float64(0.0)
        self.y_sum = np.float64(0.0)
        self.y2_sum = np.float64(0.0)
        self.xy_sum = np.float64(0.0)

    def _shift(self, val):
        """The part of `update()` that is identical for Slope/Rsquare/Resi."""
        val = np.float64(val)
        self.barv.append(val)
        # NOTE: order matters, this mirrors the Cython statement order exactly
        self.xy_sum = self.xy_sum - self.y_sum
        self.x2_sum = self.x2_sum + self.i_sum - 2 * self.x_sum
        self.x_sum = self.x_sum - self.i_sum
        _val = self.barv.pop(0)
        if not _isnan(_val):
            self.i_sum -= 1
            self.y_sum -= _val
            self.y2_sum -= _val * _val
        else:
            self.na_count -= 1
        if _isnan(val):
            self.na_count += 1
        else:
            self.i_sum += 1
            self.x_sum = self.x_sum + self.window
            self.x2_sum = self.x2_sum + self.window * self.window
            self.y_sum = self.y_sum + val
            self.y2_sum = self.y2_sum + val * val
            self.xy_sum = self.xy_sum + self.window * val
        return val

    def _n(self):
        return self.window - self.na_count


def _rolling_slope(a, window):
    r = _Rolling(window)
    out = np.empty(len(a), dtype=np.float64)
    with np.errstate(all="ignore"):
        for i in range(len(a)):
            r._shift(a[i])
            N = r._n()
            out[i] = ((N * r.xy_sum - r.x_sum * r.y_sum)
                      / (N * r.x2_sum - r.x_sum * r.x_sum))
    return out


def _rolling_rsquare(a, window):
    r = _Rolling(window)
    out = np.empty(len(a), dtype=np.float64)
    with np.errstate(all="ignore"):
        for i in range(len(a)):
            r._shift(a[i])
            N = r._n()
            rvalue = ((N * r.xy_sum - r.x_sum * r.y_sum)
                      / np.sqrt((N * r.x2_sum - r.x_sum * r.x_sum)
                                * (N * r.y2_sum - r.y_sum * r.y_sum)))
            out[i] = rvalue * rvalue
    return out


def _rolling_resi(a, window):
    r = _Rolling(window)
    out = np.empty(len(a), dtype=np.float64)
    with np.errstate(all="ignore"):
        for i in range(len(a)):
            val = r._shift(a[i])
            N = r._n()
            slope = ((N * r.xy_sum - r.x_sum * r.y_sum)
                     / (N * r.x2_sum - r.x_sum * r.x_sum))
            x_mean = r.x_sum / N
            y_mean = r.y_sum / N
            interp = y_mean - slope * x_mean
            out[i] = val - (slope * r.window + interp)
    return out


def _rolling_mean(a, window):
    r = _Rolling(window)
    out = np.empty(len(a), dtype=np.float64)
    vsum = np.float64(0.0)
    with np.errstate(all="ignore"):
        for i in range(len(a)):
            val = np.float64(a[i])
            r.barv.append(val)
            if not _isnan(r.barv[0]):
                vsum -= r.barv[0]
            else:
                r.na_count -= 1
            r.barv.pop(0)
            if _isnan(val):
                r.na_count += 1
            else:
                vsum += val
            out[i] = vsum / (r.window - r.na_count)
    return out


def rolling_slope(a, window):
    return _rolling_slope(a, window)


def rolling_rsquare(a, window):
    return _rolling_rsquare(a, window)


def rolling_resi(a, window):
    return _rolling_resi(a, window)


def rolling_mean(a, window):
    return _rolling_mean(a, window)


def _expanding_state(val, x_sum, x2_sum, y_sum, y2_sum, xy_sum, na_count, size):
    """One expanding step; returns the updated sums."""
    val = np.float64(val)
    if _isnan(val):
        na_count += 1
    else:
        x_sum = x_sum + size
        x2_sum = x2_sum + size * size
        y_sum = y_sum + val
        y2_sum = y2_sum + val * val
        xy_sum = xy_sum + size * val
    return x_sum, x2_sum, y_sum, y2_sum, xy_sum, na_count


def expanding_slope(a):
    n = len(a)
    out = np.empty(n, dtype=np.float64)
    x_sum = np.float64(0.0); x2_sum = np.float64(0.0); y_sum = np.float64(0.0)
    y2_sum = np.float64(0.0); xy_sum = np.float64(0.0); na_count = 0
    with np.errstate(all="ignore"):
        for i in range(n):
            size = i + 1
            x_sum, x2_sum, y_sum, y2_sum, xy_sum, na_count = _expanding_state(
                a[i], x_sum, x2_sum, y_sum, y2_sum, xy_sum, na_count, size)
            N = size - na_count
            out[i] = ((N * xy_sum - x_sum * y_sum)
                      / (N * x2_sum - x_sum * x_sum))
    return out


def expanding_rsquare(a):
    n = len(a)
    out = np.empty(n, dtype=np.float64)
    x_sum = np.float64(0.0); x2_sum = np.float64(0.0); y_sum = np.float64(0.0)
    y2_sum = np.float64(0.0); xy_sum = np.float64(0.0); na_count = 0
    with np.errstate(all="ignore"):
        for i in range(n):
            size = i + 1
            x_sum, x2_sum, y_sum, y2_sum, xy_sum, na_count = _expanding_state(
                a[i], x_sum, x2_sum, y_sum, y2_sum, xy_sum, na_count, size)
            N = size - na_count
            rvalue = ((N * xy_sum - x_sum * y_sum)
                      / np.sqrt((N * x2_sum - x_sum * x_sum)
                                * (N * y2_sum - y_sum * y_sum)))
            out[i] = rvalue * rvalue
    return out


def expanding_resi(a):
    n = len(a)
    out = np.empty(n, dtype=np.float64)
    x_sum = np.float64(0.0); x2_sum = np.float64(0.0); y_sum = np.float64(0.0)
    y2_sum = np.float64(0.0); xy_sum = np.float64(0.0); na_count = 0
    with np.errstate(all="ignore"):
        for i in range(n):
            size = i + 1
            val = np.float64(a[i])
            x_sum, x2_sum, y_sum, y2_sum, xy_sum, na_count = _expanding_state(
                a[i], x_sum, x2_sum, y_sum, y2_sum, xy_sum, na_count, size)
            N = size - na_count
            slope = ((N * xy_sum - x_sum * y_sum)
                     / (N * x2_sum - x_sum * x_sum))
            x_mean = x_sum / N
            y_mean = y_sum / N
            interp = y_mean - slope * x_mean
            out[i] = val - (slope * size + interp)
    return out


def expanding_mean(a):
    n = len(a)
    out = np.empty(n, dtype=np.float64)
    vsum = np.float64(0.0); na_count = 0
    with np.errstate(all="ignore"):
        for i in range(n):
            val = np.float64(a[i])
            if _isnan(val):
                na_count += 1
            else:
                vsum += val
            out[i] = vsum / ((i + 1) - na_count)
    return out
'''

INIT_PY = '''\
"""Compatibility stand-in package for qlib's compiled `_libs` helpers.

The upstream .pyd files are cp38..cp312 only; this package supplies pure-numpy
equivalents transcribed from the upstream .pyx sources and numerically
validated against the real cp39 binaries (see
experiments/WORK-557/work557_shim_validate.py).
"""
from .rolling import (  # noqa: F401
    expanding_slope, expanding_rsquare, expanding_resi, expanding_mean,
    rolling_slope, rolling_rsquare, rolling_resi, rolling_mean,
)
'''


def main():
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(WHL) as z:
        z.extractall(DEST)

    libs = DEST / "qlib" / "data" / "_libs"
    for p in libs.glob("*.pyd"):        # cp312 binaries cannot load on py3.13
        p.unlink()
    (libs / "__init__.py").write_text(INIT_PY, encoding="utf-8")
    (libs / "rolling.py").write_text(COMMON, encoding="utf-8")
    (libs / "expanding.py").write_text(COMMON, encoding="utf-8")
    print("qlib (cp312, pure-python _libs) ->", DEST)
    print("_libs contents:", sorted(p.name for p in libs.iterdir()))
    print("upstream .pyx kept for reference:",
          sorted(p.name for p in libs.glob("*.pyx")))


if __name__ == "__main__":
    main()
