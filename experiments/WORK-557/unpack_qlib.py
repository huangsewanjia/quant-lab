"""把 pyqlib 的 cp312 wheel 当作 zip 解开，放到 .tmp_qlib312/（纯 Python 复用，不改结构）。"""
import pathlib
import zipfile

root = pathlib.Path(__file__).resolve().parents[2]
whl = root / ".tmp_pip" / "pyqlib-0.9.7-cp312-cp312-win_amd64.whl"
target = root / ".tmp_qlib312"
target.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(whl) as z:
    names = z.namelist()
    z.extractall(target)

print("extracted %d entries -> %s" % (len(names), target))
compiled = [n for n in names if n.endswith((".pyd", ".so", ".dll", ".lib"))]
print("compiled artifacts: %d" % len(compiled))
for n in compiled[:12]:
    print("   ", n)
