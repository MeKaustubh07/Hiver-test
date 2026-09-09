"""Intent classifiers: two baselines (majority, keyword rules), a simple ML model (TF-IDF + logistic
regression), and the LLM classifier used by the agent (taxonomy definitions + retrieved neighbours).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline

from .intents import Taxonomy
from .llm import LLMClient
from .retrieval import Hit, Retriever

KEYWORDS_PATH = Path("config/keyword_rules.json")


@dataclass
class Prediction:
    intent: str
    confidence: float
    rationale: str = ""
    source: str = ""


# ---- trivial baseline ------------------------------------------------------------------------------
class MajorityClassifier:
    name = "majority"

    def fit(self, texts: list[str], labels: list[str]):
        vals, counts = np.unique(labels, return_counts=True)
        self.label = str(vals[np.argmax(counts)])
        self.p = float(counts.max() / counts.sum())
        return self

    def predict(self, text: str) -> Prediction:
        return Prediction(self.label, self.p, "most frequent class", self.name)


# ---- simple baseline: hand-written keyword rules ---------------------------------------------------
class KeywordClassifier:
    """Ordered regex rules per intent from config/keyword_rules.json; first match wins; else 'other'."""
    name = "keyword_rules"

    def __init__(self, path: Path = KEYWORDS_PATH):
        d = json.loads(Path(path).read_text())
        self.rules: list[tuple[str, re.Pattern]] = [(r["intent"], re.compile(r["pattern"], re.I)) for r in d["rules"]]
        self.fallback = d.get("fallback", "other")

    def fit(self, texts, labels):
        return self

    def predict(self, text: str) -> Prediction:
        for intent, pat in self.rules:
            m = pat.search(text)
            if m:
                return Prediction(intent, 0.6, f"matched /{pat.pattern[:40]}/ -> {m.group(0)!r}", self.name)
        return Prediction(self.fallback, 0.3, "no rule matched", self.name)


# ---- simple ML baseline ----------------------------------------------------------------------------
class TfidfLRClassifier:
    name = "tfidf_lr"

    def __init__(self):
        self.pipe = Pipeline([
            ("feats", FeatureUnion([
                ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)),
                ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True)),
            ])),
            ("clf", LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced")),
        ])

    def fit(self, texts: list[str], labels: list[str]):
        self.pipe.fit(texts, labels)
        return self

    def predict(self, text: str) -> Prediction:
        proba = self.pipe.predict_proba([text])[0]
        i = int(np.argmax(proba))
        return Prediction(str(self.pipe.classes_[i]), float(proba[i]), "tf-idf + logistic regression", self.name)


# ---- LLM classifier (the agent's) ------------------------------------------------------------------
CLASSIFY_SYSTEM = (
    "You are an intent classifier for {brand}'s customer-support inbox. Classify the customer's message "
    "into exactly one intent from the taxonomy. Judge from the customer's message alone. "
    "Respond with only a JSON object."
)

CLASSIFY_PROMPT = """TAXONOMY (choose exactly one `name`):
{taxonomy}

{policy_hint}{neighbours}CUSTOMER MESSAGE:
\"\"\"{message}\"\"\"

Return JSON: {{"intent": "<name>", "confidence": <0.0-1.0>, "rationale": "<= 20 words"}}"""


@dataclass
class LLMClassifier:
    llm: LLMClient
    taxonomy: Taxonomy
    retriever: Retriever | None = None
    k_neighbours: int = 4
    name: str = "llm"
    last_hits: list[Hit] = field(default_factory=list)

    def fit(self, texts, labels):
        return self

    def _neighbour_block(self, message: str) -> str:
        if self.retriever is None or self.k_neighbours <= 0:
            self.last_hits = []
            return ""
        self.last_hits = self.retriever.search(message, k=self.k_neighbours)
        lines = ["SIMILAR PAST MESSAGES AND HOW THE BRAND ANSWERED (unlabelled context; use only as a hint):"]
        for h in self.last_hits:
            lines.append(f'- customer: "{h.customer_message[:220]}"\n  brand: "{h.first_reply[:200]}"')
        return "\n".join(lines) + "\n\n"

    def predict(self, text: str) -> Prediction:
        prompt = CLASSIFY_PROMPT.format(
            taxonomy=self.taxonomy.prompt_block(with_examples=2),
            policy_hint="Tie-breaks: " + " | ".join(self.taxonomy.tie_breaks) + "\n\n" if self.taxonomy.tie_breaks else "",
            neighbours=self._neighbour_block(text), message=text)
        out = self.llm.complete_json(prompt, system=CLASSIFY_SYSTEM.format(brand=self.taxonomy.brand),
                                     max_tokens=200, tag="classify")
        if out is None:  # queued
            return Prediction("__pending__", 0.0, "queued", self.name)
        if not isinstance(out, dict):
            return Prediction("other", 0.2, "unparseable model output", self.name)
        intent = self.taxonomy.normalise(out.get("intent"))
        try:
            conf = float(out.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        return Prediction(intent, max(0.0, min(1.0, conf)), str(out.get("rationale", ""))[:300], self.name)
