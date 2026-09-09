"""The support agent: retrieve → classify → triage → draft, with every intermediate exposed."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .classify import LLMClassifier
from .draft import Draft, GroundedDrafter, LinkCatalog
from .intents import Taxonomy, load_style_guide, load_taxonomy
from .llm import LLMClient
from .retrieval import Hit, Retriever
from .triage import Decision, LayeredTriage, LLMTriage, RulePolicy

PENDING = "__pending__"


@dataclass
class AgentOutput:
    message: str
    intent: str = ""
    confidence: float = 0.0
    rationale: str = ""
    disposition: str = ""
    reason: str = ""
    rule: str = ""
    triage_source: str = ""
    reply: str = ""
    evidence_ids: list[int] = field(default_factory=list)
    stripped_urls: list[str] = field(default_factory=list)
    draft_notes: str = ""
    neighbours: list[dict] = field(default_factory=list)
    pending: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class SupportAgent:
    def __init__(self, llm: LLMClient, retriever: Retriever, taxonomy: Taxonomy | None = None,
                 style_guide: str | None = None, catalog: LinkCatalog | None = None, k: int = 5,
                 llm_triage: bool = True):
        self.llm = llm
        self.retriever = retriever
        self.taxonomy = taxonomy or load_taxonomy()
        self.style_guide = style_guide if style_guide is not None else load_style_guide()
        self.catalog = catalog or LinkCatalog()
        self.k = k
        self.classifier = LLMClassifier(llm, self.taxonomy, retriever=None)
        hard = RulePolicy(self.taxonomy, hard_only=True)
        fallback = RulePolicy(self.taxonomy)
        self.triage = LayeredTriage(hard, LLMTriage(llm, self.taxonomy) if llm_triage else None, fallback)
        self.drafter = GroundedDrafter(llm, self.taxonomy, self.style_guide, self.catalog, k=k)

    def retrieve(self, message: str) -> list[Hit]:
        return self.retriever.search(message, k=self.k)

    def handle(self, message: str, hits: list[Hit] | None = None) -> AgentOutput:
        hits = hits if hits is not None else self.retrieve(message)
        out = AgentOutput(message=message, neighbours=[{"root_id": h.root_id, "customer": h.customer_message[:160],
                                                        "reply": h.first_reply[:160], "dm": h.reply_asks_dm} for h in hits])
        # classify (the classifier reuses the agent's retrieval instead of searching again)
        self.classifier.last_hits = hits
        self.classifier.retriever = self.retriever
        self.classifier.k_neighbours = min(4, len(hits))
        pred = self._classify(message, hits)
        if pred.intent == PENDING:
            out.pending = True
            return out
        out.intent, out.confidence, out.rationale = pred.intent, pred.confidence, pred.rationale
        # triage
        dec: Decision = self.triage.decide(message, pred.intent, pred.confidence, hits)
        if dec.disposition == PENDING:
            out.pending = True
            return out
        out.disposition, out.reason, out.rule, out.triage_source = dec.disposition, dec.reason, dec.rule, dec.source
        # draft
        d: Draft | None = self.drafter.draft(message, pred.intent, hits, dec.disposition, dec.reason)
        if d is None:
            out.pending = True
            return out
        out.reply, out.evidence_ids, out.stripped_urls, out.draft_notes = d.reply, d.evidence_ids, d.stripped_urls, d.notes
        return out

    def _classify(self, message: str, hits: list[Hit]):
        # feed the already-retrieved neighbours to the classifier prompt
        clf = self.classifier
        saved = clf.retriever
        try:
            clf.retriever = _FixedHits(hits[: clf.k_neighbours])
            return clf.predict(message)
        finally:
            clf.retriever = saved


class _FixedHits:
    def __init__(self, hits: list[Hit]):
        self.hits = hits

    def search(self, query: str, k: int = 5, pool: int = 50) -> list[Hit]:
        return self.hits[:k]
