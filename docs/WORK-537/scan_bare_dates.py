import glob
import re
import sys

sys.path.insert(0, "/srv/share/quantdb/code/quant-lab/scripts")
from work537_probe import clean_text

BARE = re.compile(
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*")

for path in sorted(glob.glob("/srv/share/quantdb/raw/foreign_research/_probe/*_listing.html")):
    cid = path.split("/")[-1].split("_")[0]
    text = clean_text(open(path, encoding="utf-8").read())
    hits = BARE.findall(text)
    if hits:
        print(f"{cid:12s} 年份缺失的日期片段 {len(hits):3d} 个，例: {hits[:4]}")
