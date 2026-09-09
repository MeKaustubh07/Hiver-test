"""Retrieve historically similar customer messages (and how the brand resolved them).

Hybrid retrieval over the brand corpus: BM25 (lexical) + sentence embeddings (semantic), fused
with reciprocal rank fusion. Embeddings are cached on disk. The retrieval index excludes any
root_ids passed as `exclude` so golden-set threads never ground their own answers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from rank_bm25 import BM25Okapi

CACHE = Path("data/cache")
EMB_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_TOK_RE = re.compile(r"[a-z0-9']+")


def tokenize(s: str) -> list[str]:
    return _TOK_RE.findall(s.lower())


@dataclass
class Hit:
    root_id: int
    score: float
    customer_message: str
    first_reply: str
    transcript: str
    reply_asks_dm: bool


class Retriever:
    def __init__(self, corpus: pd.DataFrame, exclude: set[int] | None = None,
                 emb_model: str = EMB_MODEL, use_embeddings: bool = True, brand: str = "SpotifyCares"):
        ex = exclude or set()
        self.df = corpus[~corpus.root_id.isin(ex)].reset_index(drop=True)
        self.bm25 = BM25Okapi([tokenize(m) for m in self.df.customer_message])
        self.use_embeddings = use_embeddings
        self.emb_model_name = emb_model
        self._model = None
        self.emb = self._load_or_build_embeddings(brand) if use_embeddings else None

    def _encoder(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.emb_model_name)
        return self._model

    def _load_or_build_embeddings(self, brand: str) -> np.ndarray:
        CACHE.mkdir(parents=True, exist_ok=True)
        tag = self.emb_model_name.split("/")[-1]
        p = CACHE / f"emb_{brand}_{tag}.npz"
        if p.exists():
            z = np.load(p)
            if z["root_ids"].shape[0] == len(self.df) and np.array_equal(z["root_ids"], self.df.root_id.values):
                return z["emb"]
            # cache was built over a different subset: reuse rows that overlap
            idx = pd.Series(range(len(z["root_ids"])), index=z["root_ids"])
            if self.df.root_id.isin(idx.index).all():
                return z["emb"][idx.loc[self.df.root_id].values]
        enc = self._encoder()
        emb = enc.encode(self.df.customer_message.tolist(), batch_size=128, normalize_embeddings=True,
                         show_progress_bar=True)
        np.savez(p, emb=emb.astype(np.float32), root_ids=self.df.root_id.values)
        return emb

    def _to_hit(self, i: int, score: float) -> Hit:
        r = self.df.iloc[i]
        return Hit(int(r.root_id), float(score), r.customer_message, r.first_reply_clean, r.transcript, bool(r.reply_asks_dm))

    def search(self, query: str, k: int = 5, pool: int = 50) -> list[Hit]:
        bm = self.bm25.get_scores(tokenize(query))
        bm_rank = np.argsort(-bm)[:pool]
        fused: dict[int, float] = {}
        for rank, i in enumerate(bm_rank):
            if bm[i] > 0:
                fused[i] = fused.get(i, 0.0) + 1.0 / (60 + rank)
        if self.use_embeddings:
            q = self._encoder().encode([query], normalize_embeddings=True)[0]
            sims = self.emb @ q
            em_rank = np.argsort(-sims)[:pool]
            for rank, i in enumerate(em_rank):
                fused[i] = fused.get(i, 0.0) + 1.0 / (60 + rank)
        top = sorted(fused.items(), key=lambda kv: -kv[1])[:k]
        return [self._to_hit(i, s) for i, s in top]

    def embed(self, texts: list[str]) -> np.ndarray:
        return self._encoder().encode(texts, normalize_embeddings=True, batch_size=128)
