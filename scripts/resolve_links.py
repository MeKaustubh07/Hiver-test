"""One-off: resolve every unique t.co link in SpotifyCares first replies to its final URL + page title.

Produces data/processed/link_catalog.csv (tco_url, final_url, status, title). Links from 2017 that
still resolve give us a verifiable catalog of support articles the agent may cite.
"""
import json, re, sys, html
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import httpx, pandas as pd

c = pd.read_parquet("data/processed/SpotifyCares_threads.parquet")
urls = sorted({re.sub(r"[.,;:!?)\]…]+$", "", u) for lst in c.reply_urls.map(json.loads) for u in lst})
print("unique cleaned urls:", len(urls))
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

def resolve(u):
    try:
        with httpx.Client(follow_redirects=True, timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as cl:
            r = cl.get(u)
            m = TITLE_RE.search(r.text[:20000]) if r.status_code == 200 else None
            title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""
            return {"tco_url": u, "final_url": str(r.url), "status": r.status_code, "title": title[:150]}
    except Exception as e:
        return {"tco_url": u, "final_url": "", "status": -1, "title": type(e).__name__}

with ThreadPoolExecutor(12) as ex:
    rows = list(ex.map(resolve, urls))
out = pd.DataFrame(rows)
out.to_csv("data/processed/link_catalog.csv", index=False)
ok = out[out.status == 200]
print("resolved 200:", len(ok), "| unique final urls:", ok.final_url.nunique())
print(ok.final_url.str.extract(r"https?://([^/]+)")[0].value_counts().head(10).to_string())
print(ok.title.value_counts().head(25).to_string())
