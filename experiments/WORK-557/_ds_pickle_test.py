import os, sys, pickle, traceback
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
import pandas as pd
from work557_run_arms import Work503Dataset
ds_dir = os.path.join(repo, "data", "WORK-557", "ds")
si = pd.read_parquet(os.path.join(ds_dir, "sample_index.parquet"))
si["datetime"] = pd.to_datetime(si["datetime"])
sub = si[si["segment"] == "train"].sort_values(["datetime","instrument"], kind="mergesort")
ds = Work503Dataset(ds_dir, sub.set_index(["datetime","instrument"], drop=True), True, True)
print("built", len(ds))
b = pickle.dumps(ds, protocol=5)
print("pickle OK bytes", len(b))
import multiprocessing as mp
try:
    ctx = mp.get_context("spawn")
    ds2 = pickle.loads(b)
    print("spawn ctx pickle roundtrip OK", len(ds2))
except Exception:
    traceback.print_exc()
