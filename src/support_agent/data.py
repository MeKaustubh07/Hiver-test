"""Load the TWCS dataset and reconstruct conversation threads for one brand.

Dataset: Kaggle thoughtvector/customer-support-on-twitter (twcs.csv).
Columns: tweet_id, author_id, inbound, created_at, text, response_tweet_id, in_response_to_tweet_id
Customers are anonymised as numeric author_ids; brands keep their handle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

RAW_CSV = Path("data/raw/twcs/twcs.csv")
PARQUET = Path("data/processed/twcs.parquet")

_MENTION_RE = re.compile(r"@\w+")
_URL_RE = re.compile(r"https?://\S+")
_WS_RE = re.compile(r"\s+")


def load_twcs(path: Path | None = None) -> pd.DataFrame:
    """Load the full dataset, caching a parquet copy for fast reloads."""
    pq = path or PARQUET
    if pq.exists():
        return pd.read_parquet(pq)
    df = pd.read_csv(
        RAW_CSV,
        dtype={"tweet_id": "int64", "author_id": "string", "text": "string",
               "response_tweet_id": "string", "in_response_to_tweet_id": "float64"},
    )
    df["inbound"] = df["inbound"].astype(bool)
    pq.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(pq, index=False)
    return df


def clean_text(text: str, keep_urls: bool = False) -> str:
    """Strip @mentions (anonymised ids and handles) and collapse whitespace. Optionally strip URLs."""
    t = _MENTION_RE.sub(" ", str(text))
    if not keep_urls:
        t = _URL_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


@dataclass
class Turn:
    tweet_id: int
    author_id: str
    inbound: bool
    created_at: str
    text: str


def parse_created(s: str) -> datetime:
    return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")


@dataclass
class Thread:
    """A conversation rooted at a customer's first-contact tweet to the brand."""
    root_id: int
    brand: str
    turns: list[Turn] = field(default_factory=list)  # chronological, root first
    burst_extra: list[str] = field(default_factory=list)  # customer's follow-up tweets posted before the brand replied

    @property
    def burst_turns(self) -> list[Turn]:
        """Leading consecutive turns by the customer who opened the thread, before the brand's first reply."""
        out = []
        for t in self.turns:
            if not t.inbound or t.author_id != self.turns[0].author_id:
                break
            out.append(t)
        return out

    @property
    def customer_message(self) -> str:
        """The 'burst': the customer's consecutive tweets before the brand replied, joined in order."""
        texts = [t.text for t in self.burst_turns[:4]] + self.burst_extra
        return " ".join(texts)

    @property
    def first_brand_reply(self) -> str | None:
        for t in self.turns[len(self.burst_turns):]:
            if not t.inbound:
                return t.text
        return None

    def transcript(self, max_turns: int | None = None, keep_urls: bool = True) -> str:
        turns = self.turns if max_turns is None else self.turns[:max_turns]
        lines = []
        for t in turns:
            who = "Customer" if t.inbound else "Agent"
            lines.append(f"{who}: {clean_text(t.text, keep_urls=keep_urls)}")
        return "\n".join(lines)


def brand_subset(df: pd.DataFrame, brand: str) -> pd.DataFrame:
    """Return every tweet in any thread the brand participated in (parents + descendants)."""
    ids = set(df.loc[df.author_id == brand, "tweet_id"].tolist())
    frontier = set(ids)
    parent_of = df.set_index("tweet_id")["in_response_to_tweet_id"]
    children_of = df.set_index("tweet_id")["response_tweet_id"]
    for _ in range(12):  # thread depth bound; TWCS threads are shallow
        new = set()
        for tid in frontier:
            p = parent_of.get(tid)
            if p is not None and not pd.isna(p) and int(p) not in ids:
                new.add(int(p))
            c = children_of.get(tid)
            if isinstance(c, str) and c:
                for cid in c.split(","):
                    cid = int(cid)
                    if cid not in ids:
                        new.add(cid)
        new &= set(df.tweet_id[df.tweet_id.isin(new)])  # only ids that exist
        if not new:
            break
        ids |= new
        frontier = new
    return df[df.tweet_id.isin(ids)].copy()


def build_threads(sub: pd.DataFrame, brand: str) -> list[Thread]:
    """Reconstruct threads rooted at inbound tweets that have no parent in the dataset.

    Each thread is linearised as: root, then the chain of replies following the *brand's*
    responses (first child at each step when the tree forks). Forked side-branches are
    dropped so each thread reads as a single conversation.
    """
    by_id = {int(r.tweet_id): r for r in sub.itertuples(index=False)}
    threads: list[Thread] = []
    for r in sub.itertuples(index=False):
        if not r.inbound:
            continue
        parent = r.in_response_to_tweet_id
        if parent is not None and not pd.isna(parent) and int(parent) in by_id:
            continue  # not a root
        chain = [r]
        cur = r
        seen = {int(r.tweet_id)}
        while True:
            kids = cur.response_tweet_id
            if not isinstance(kids, str) or not kids:
                break
            kid_rows = [by_id[int(k)] for k in kids.split(",") if int(k) in by_id and int(k) not in seen]
            if not kid_rows:
                break
            # prefer the brand's reply when a customer tweet forks; else earliest
            kid_rows.sort(key=lambda k: (k.author_id != brand, k.created_at))
            cur = kid_rows[0]
            seen.add(int(cur.tweet_id))
            chain.append(cur)
            if len(chain) > 30:
                break
        if not any(t.author_id == brand for t in chain[1:]):
            continue  # brand never replied in this thread
        first_brand = next(t for t in chain[1:] if t.author_id == brand)
        burst = _burst_extra(r, first_brand, by_id, seen)
        threads.append(Thread(
            root_id=int(r.tweet_id), brand=brand,
            turns=[Turn(int(t.tweet_id), str(t.author_id), bool(t.inbound), str(t.created_at), str(t.text)) for t in chain],
            burst_extra=burst,
        ))
    return threads


def _burst_extra(root, first_brand, by_id: dict, chain_ids: set[int]) -> list[str]:
    """Customer's own replies to the root (same author, inbound) posted before the brand's first reply,
    excluding tweets already in the linearised chain. Ordered by time; capped at 3."""
    try:
        cutoff = parse_created(str(first_brand.created_at))
    except ValueError:
        return []
    kids = root.response_tweet_id
    if not isinstance(kids, str) or not kids:
        return []
    extra = []
    for k in kids.split(","):
        kid = by_id.get(int(k))
        if kid is None or int(kid.tweet_id) in chain_ids or not kid.inbound or kid.author_id != root.author_id:
            continue
        try:
            when = parse_created(str(kid.created_at))
        except ValueError:
            continue
        if when < cutoff:
            extra.append((when, str(kid.text)))
    return [t for _, t in sorted(extra)[:3]]
