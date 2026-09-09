"""Build the per-brand working corpus: English first-contact threads with a brand reply.

Outputs data/processed/<brand>_threads.parquet with one row per thread:
  root_id, customer_message (cleaned), customer_raw, first_reply (raw), first_reply_clean,
  n_turns, transcript, created_at, reply_asks_dm (weak signal that a human/DM was needed),
  reply_urls (list of URLs in the first brand reply)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import py3langid as langid

from .data import Thread, brand_subset, build_threads, clean_text, load_twcs

PROCESSED = Path("data/processed")
DM_RE = re.compile(r"\b(DM|DMs|direct message|private message)\b", re.I)
URL_RE = re.compile(r"https?://\S+")
SIGNOFF_RE = re.compile(r"\s*/[A-Z]{1,3}\s*$")


def strip_signoff(reply: str) -> str:
    """SpotifyCares agents sign replies like '... /MG'. Remove for style-neutral grounding."""
    return SIGNOFF_RE.sub("", reply).strip()


def thread_row(t: Thread) -> dict:
    reply = t.first_brand_reply or ""
    return {
        "root_id": t.root_id,
        "customer_raw": t.customer_message,
        "customer_message": clean_text(t.customer_message),
        "first_reply": reply,
        "first_reply_clean": strip_signoff(clean_text(reply, keep_urls=True)),
        "n_turns": len(t.turns),
        "burst_size": len(t.burst_turns) + len(t.burst_extra),
        "transcript": t.transcript(keep_urls=True),
        "created_at": t.turns[0].created_at,
        "reply_asks_dm": bool(DM_RE.search(reply)),
        "reply_urls": json.dumps(URL_RE.findall(reply)),
    }


def build_corpus(brand: str, min_chars: int = 15) -> pd.DataFrame:
    df = load_twcs()
    sub = brand_subset(df, brand)
    threads = build_threads(sub, brand)
    rows = [thread_row(t) for t in threads]
    out = pd.DataFrame(rows)
    out["lang"] = [langid.classify(m)[0] for m in out.customer_message]
    out = out[(out.lang == "en") & (out.customer_message.str.len() >= min_chars)]
    # exact-duplicate customer messages (retweet storms / copy-paste) keep first occurrence
    out = out.drop_duplicates(subset=["customer_message"]).reset_index(drop=True)
    out["created_at"] = pd.to_datetime(out.created_at, format="%a %b %d %H:%M:%S %z %Y", errors="coerce")
    out = out.sort_values("created_at").reset_index(drop=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    out.to_parquet(PROCESSED / f"{brand}_threads.parquet", index=False)
    return out


def load_corpus(brand: str) -> pd.DataFrame:
    p = PROCESSED / f"{brand}_threads.parquet"
    if not p.exists():
        return build_corpus(brand)
    return pd.read_parquet(p)


if __name__ == "__main__":
    import sys
    b = sys.argv[1] if len(sys.argv) > 1 else "SpotifyCares"
    c = build_corpus(b)
    print(f"{b}: {len(c)} English first-contact threads; reply_asks_dm={c.reply_asks_dm.mean():.2%}; "
          f"dates {c.created_at.min()} .. {c.created_at.max()}")
    print(c.n_turns.describe().round(2).to_string())
