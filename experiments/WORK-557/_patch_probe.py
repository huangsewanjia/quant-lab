import pathlib

p = pathlib.Path("experiments/WORK-557/work557_data_probe.py")
t = p.read_text(encoding="utf-8")
t = t.replace("hive_partitioning=1)", "hive_partitioning=1, union_by_name=True)")
t = t.replace("read_parquet('%s/stock_basic/partition=*/part.parquet')",
              "read_parquet('%s/stock_basic/partition=*/part.parquet', union_by_name=True)")
t = t.replace('        print("  %-12s missing=%d / %d  rate=%.4f%%  years=%s" % (',
              '        dump_partial(probe, OUT)\n        print("  %-12s missing=%d / %d  rate=%.4f%%  years=%s" % (')
t = t.replace('    print("  suspend rows in pool: %d ; by_year=%s" % (len(sus), byy), flush=True)',
              '    dump_partial(probe, OUT)\n    print("  suspend rows in pool: %d ; by_year=%s" % (len(sus), byy), flush=True)')
p.write_text(t, encoding="utf-8")
print("union_by_name occurrences:", t.count("union_by_name=True"))
print("dump_partial calls:", t.count("dump_partial(probe, OUT)"))
