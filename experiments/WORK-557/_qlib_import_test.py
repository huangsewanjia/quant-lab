"""WORK-557：Python 3.13 下 qlib 可用性自检（用 .venv/qlib312 + 纯 python _libs 替身）。"""
import sys

sys.path.insert(0, r".venv\qlib312")

import numpy as np  # noqa: E402

print("python:", sys.version)
print("numpy:", np.__version__)
import qlib  # noqa: E402

print("qlib:", qlib.__version__, "from", qlib.__file__)

from qlib.contrib.data.handler import Alpha158  # noqa: E402

exprs, names = Alpha158.__new__(Alpha158).get_feature_config()
print("Alpha158 expressions:", len(exprs))
print("first 5 names:", list(names[:5]))
print("last 5 names:", list(names[-5:]))

ops = set()
for e in exprs:
    import re
    for m in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", e):
        ops.add(m)
print("ops used:", sorted(ops))

from qlib.data._libs.rolling import rolling_slope  # noqa: E402
print("shim rolling_slope smoke:",
      rolling_slope(np.array([1.0, 2.0, 3.0, 4.0]), 3))

import torch  # noqa: E402
print("torch:", torch.__version__, "cuda:", torch.cuda.is_available(),
      torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
