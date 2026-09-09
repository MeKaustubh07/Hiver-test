"""One-off: profile brands in twcs to choose a target brand. Writes data/processed/twcs.parquet."""
import pandas as pd, re, sys
from pathlib import Path
raw = Path("data/raw/twcs/twcs.csv"); out = Path("data/processed/twcs.parquet")
if out.exists():
    df = pd.read_parquet(out)
else:
    df = pd.read_csv(raw, dtype={"tweet_id": "int64", "author_id": "string", "text": "string",
                                  "response_tweet_id": "string", "in_response_to_tweet_id": "float64"})
    df["inbound"] = df["inbound"].astype(bool)
    df.to_parquet(out, index=False)
print("rows", len(df), "inbound", df.inbound.sum())
brands = df[~df.inbound]
by = brands.groupby("author_id")
dm_re = re.compile(r"\b(DM|direct message|private message|PM us|message us)\b", re.I)
url_re = re.compile(r"https?://\S+")
stats = pd.DataFrame({
    "brand_replies": by.size(),
    "dm_frac": by["text"].apply(lambda s: s.str.contains(dm_re).mean()),
    "url_frac": by["text"].apply(lambda s: s.str.contains(url_re).mean()),
    "avg_len": by["text"].apply(lambda s: s.str.len().mean()),
    "uniq_ratio": by["text"].apply(lambda s: s.str.lower().str.replace(r"@\d+","",regex=True).str.strip().nunique()/len(s)),
}).sort_values("brand_replies", ascending=False)
pd.set_option("display.width", 200)
print(stats.head(30).round(3).to_string())
