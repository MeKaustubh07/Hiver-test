"""Build config/link_catalog.json from data/processed/link_catalog.csv + corpus usage counts.

Maps every t.co link the brand used to its final URL/title, and keeps the set of support pages the
agent is allowed to cite (support.spotify.com + community + accounts pages; web-player track links are
kept only as a t.co->final map so evidence can show them, not as citable knowledge).
"""
import json, re
from collections import Counter, defaultdict
import pandas as pd

cat = pd.read_csv("data/processed/link_catalog.csv").fillna("")
c = pd.read_parquet("data/processed/SpotifyCares_threads.parquet")
clean = lambda u: re.sub(r"[.,;:!?)\]…]+$", "", u)
uses = Counter(clean(u) for lst in c.reply_urls.map(json.loads) for u in lst)
ctx_for = defaultdict(list)
for r in c.itertuples():
    for u in json.loads(r.reply_urls):
        u = clean(u)
        if len(ctx_for[u]) < 2:
            ctx_for[u].append(r.first_reply_clean[:160])
tco_map = {}
final_stats = defaultdict(lambda: {"title": "", "n_uses": 0, "tco": [], "contexts": []})
for r in cat.itertuples():
    if r.status != 200 or not r.final_url:
        continue
    f = str(r.final_url).split("?")[0].rstrip("/")
    tco_map[r.tco_url] = f
    s = final_stats[f]
    s["title"] = s["title"] or str(r.title)
    s["n_uses"] += uses.get(r.tco_url, 0)
    s["tco"].append(r.tco_url)
    if len(s["contexts"]) < 3:
        s["contexts"].extend(ctx_for.get(r.tco_url, [])[:1])
citable = []
for f, s in final_stats.items():
    host = re.sub(r"^https?://", "", f).split("/")[0]
    if host in {"support.spotify.com", "community.spotify.com", "accounts.spotify.com", "www.spotify.com"} \
            and "Page not found" not in s["title"] and s["title"]:
        citable.append({"url": f, "title": s["title"].replace(" - Spotify", "").strip(), "n_uses": s["n_uses"],
                        "example_contexts": s["contexts"][:2]})
citable.sort(key=lambda x: -x["n_uses"])
out = {"citable_pages": citable, "tco_to_final": tco_map,
       "note": "Resolved on 2026-09-09 from 2017 t.co links; 917 links go to open.spotify.com track/album pages."}
json.dump(out, open("config/link_catalog.json", "w"), indent=1)
print(len(citable), "citable pages;", len(tco_map), "t.co links mapped")
for p in citable[:45]:
    print(f"[{p['n_uses']:>4}] {p['title'][:55]:<55} {p['url']}")
