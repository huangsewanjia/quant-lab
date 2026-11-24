import duckdb

con = duckdb.connect()
for d in ("20240102", "20100104", "20200102"):
    p = "Z:/quantdb/raw/stk_limit/trade_date=%s/part.parquet" % d
    try:
        df = con.execute("select * from read_parquet('%s') limit 3" % p).df()
        n = con.execute("select count(*) c from read_parquet('%s')" % p).df()
        print("=== stk_limit %s  rows=%s ===" % (d, n.c.iloc[0]))
        print(df.to_string())
    except Exception as e:
        print("=== stk_limit %s ERR %s" % (d, str(e)[:160]))

print("\n=== daily_basic float_share units (sample) ===")
print(con.execute(
    "select ts_code, trade_date, close, total_share, float_share, free_share, "
    "total_mv, circ_mv from read_parquet("
    "'Z:/quantdb/raw/daily_basic/trade_date=20240102/part.parquet') limit 5"
).df().to_string())

print("\n=== index_daily CSI300 sample ===")
print(con.execute(
    "select * from read_parquet('Z:/quantdb/raw/index_daily/trade_date=20240102/part.parquet') "
    "where ts_code='000300.SH'").df().to_string())
