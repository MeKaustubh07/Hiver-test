"""Golden evaluation set: sampling candidates from the corpus and reading/writing the label sheet.

Sampling (documented in eval/golden/README.md):
  - 'random'  : uniform over the English first-contact corpus, stratified by month so no single
                incident (e.g. an outage day) dominates.
  - 'targeted': keyword-matched oversampling for intents that are rare in a uniform sample, so every
                intent has enough support to measure per-class recall (config/golden_strata.json).
  - 'hard'    : very short messages, multi-question messages, angry messages, and messages with
                media/links — the cases where classifiers and drafters typically break.
Golden root_ids are excluded from the retrieval index so no example grounds its own answer.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

GOLDEN_DIR = Path("eval/golden")
STRATA_PATH = Path("config/golden_strata.json")
LABEL_COLUMNS = ["id", "stratum", "customer_message", "thread_context", "intent", "disposition", "reason", "notes"]


def _hard_mask(c: pd.DataFrame) -> pd.Series:
    m = c.customer_message
    short = m.str.len() < 40
    multi = m.str.count(r"\?") >= 2
    angry = m.str.contains(r"\b(worst|ridiculous|disgust|joke|useless|pathetic|furious|wtf|f+u+c+k)\b", case=False, regex=True)
    media = c.customer_raw.str.contains(r"https?://", regex=True)
    return short | multi | angry | media


def sample_candidates(corpus: pd.DataFrame, n_random: int = 150, n_targeted: int = 70, n_hard: int = 40,
                      seed: int = 42, strata_path: Path = STRATA_PATH) -> pd.DataFrame:
    c = corpus.copy()
    c["month"] = c.created_at.dt.to_period("M").astype(str)
    taken: set[int] = set()
    parts = []
    # random, stratified by month (proportional, at least 1 per month)
    months = c.month.value_counts()
    per = (months / months.sum() * n_random).round().astype(int).clip(lower=1)
    rows = []
    for mo, k in per.items():
        pool = c[c.month == mo]
        rows.append(pool.sample(min(k, len(pool)), random_state=seed))
    rnd = pd.concat(rows).head(n_random)
    rnd = rnd.assign(stratum="random"); taken |= set(rnd.root_id); parts.append(rnd)
    # targeted by keyword strata
    strata = json.loads(Path(strata_path).read_text())["strata"] if Path(strata_path).exists() else []
    if strata:
        per_stratum = max(1, n_targeted // len(strata))
        for s in strata:
            pat = re.compile(s["pattern"], re.I)
            pool = c[~c.root_id.isin(taken) & c.customer_message.str.contains(pat)]
            take = pool.sample(min(per_stratum, len(pool)), random_state=seed)
            take = take.assign(stratum=f"targeted:{s['name']}"); taken |= set(take.root_id); parts.append(take)
    # hard cases
    pool = c[~c.root_id.isin(taken) & _hard_mask(c)]
    hard = pool.sample(min(n_hard, len(pool)), random_state=seed).assign(stratum="hard")
    parts.append(hard)
    out = pd.concat(parts).drop_duplicates("root_id").sample(frac=1.0, random_state=seed).reset_index(drop=True)
    out = out.rename(columns={"root_id": "id"})
    out["thread_context"] = out.transcript.str.slice(0, 700)
    for col in ("intent", "disposition", "reason", "notes"):
        out[col] = ""
    return out[LABEL_COLUMNS]


def load_golden(path: Path = GOLDEN_DIR / "golden_set.csv", require_labels: bool = True) -> pd.DataFrame:
    g = pd.read_csv(path, dtype={"id": "int64"}).fillna("")
    for col in ("intent", "disposition"):
        if col not in g.columns:
            g[col] = ""
    if require_labels:
        g = g[(g.intent != "") & (g.disposition != "")]
        if "unlabelable" in g.columns:  # spam / non-English / no ask: kept in the file, excluded from scoring
            g = g[~g.unlabelable.astype(str).str.lower().isin(["true", "1"])]
        g = g.reset_index(drop=True)
    g["disposition"] = g.disposition.str.strip().str.lower()
    g["intent"] = g.intent.str.strip()
    return g
