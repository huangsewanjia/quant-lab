import glob
import os
import time

import duckdb

con = duckdb.connect()
con.execute("PRAGMA threads=2")

tests = [
    ("single file", "Z:/quantdb/raw/adj_factor/trade_date=20060104/part.parquet"),
    ("glob 2006 (5 files)", "Z:/quantdb/raw/adj_factor/trade_date=2006*/part.parquet"),
    ("glob 2006-2009 (~950)", "Z:/quantdb/raw/adj_factor/trade_date=200[6-9]*/part.parquet"),
    ("glob all (~5075)", "Z:/quantdb/raw/adj_factor/trade_date=*/part.parquet"),
]
for label, pat in tests:
    t0 = time.time()
    try:
        n = con.execute("select count(*) from read_parquet('%s')" % pat).fetchone()[0]
        print("%-28s rows=%-10d %.1fs" % (label, n, time.time() - t0), flush=True)
    except Exception as e:
        print("%-28s ERR after %.1fs: %s" % (label, time.time() - t0, str(e)[:160]), flush=True)

t0 = time.time()
files = glob.glob("Z:/quantdb/raw/adj_factor/trade_date=*/part.parquet")
print("python glob: %d files in %.1fs" % (len(files), time.time() - t0), flush=True)
