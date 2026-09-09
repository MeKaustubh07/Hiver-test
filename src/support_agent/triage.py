"""Auto-handle vs escalate decision with a stated reason.

Layers (safety first):
  1. Hard rules  — regex triggers from config/triage_rules.json (billing disputes, account security,
                   legal, abuse, self-harm, data/deletion, artist/business). Deterministic; always win.
  2. LLM triage  — reasons over the escalation policy, the intent, and how the brand historically
                   handled the nearest similar cases (did they move to DM?).
  3. Fallback    — the intent's default disposition and a confidence threshold.
Baselines: always-escalate (trivial), rules-only (simple), historical-DM k-NN (data-driven).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .intents import Taxonomy
from .llm import LLMClient
from .retrieval import Hit

RULES_PATH = Path("config/triage_rules.json")


@dataclass
class Decision:
    disposition: str  # auto | escalate
    reason: str
    rule: str = ""    # which rule/layer produced it
    source: str = ""


# ---- layer 1: hard rules -------------------------------------------------------------------------
class RulePolicy:
    """Ordered rules; first match wins. A rule matches by regex on the message and/or by intent."""
    name = "rules"

    def __init__(self, taxonomy: Taxonomy, path: Path = RULES_PATH, hard_only: bool = False):
        self.taxonomy = taxonomy
        d = json.loads(Path(path).read_text())
        self.rules = []
        for r in d["rules"]:
            if hard_only and not r.get("hard", False):
                continue
            self.rules.append({
                "name": r["name"], "pattern": re.compile(r["pattern"], re.I) if r.get("pattern") else None,
                "intents": set(r.get("intents", [])), "disposition": r["disposition"],
                "reason": r["reason"], "hard": bool(r.get("hard", False)),
            })
        self.low_conf = float(d.get("low_confidence_threshold", 0.55))

    def match(self, text: str, intent: str) -> Decision | None:
        for r in self.rules:
            if r["intents"] and intent not in r["intents"]:
                continue
            if r["pattern"] is not None:
                m = r["pattern"].search(text)
                if not m:
                    continue
                return Decision(r["disposition"], r["reason"].format(match=m.group(0)), r["name"], self.name)
            return Decision(r["disposition"], r["reason"].format(match=intent), r["name"], self.name)
        return None

    def decide(self, text: str, intent: str, confidence: float = 1.0) -> Decision:
        d = self.match(text, intent)
        if d:
            return d
        if confidence < self.low_conf:
            return Decision("escalate", f"intent uncertain (confidence {confidence:.2f} < {self.low_conf})",
                            "low_confidence", self.name)
        it = self.taxonomy.get(intent)
        default = it.default_disposition if it else "escalate"
        disp = "escalate" if default in ("escalate", "depends") else "auto"
        return Decision(disp, f"default for intent '{intent}' is {default}", "intent_default", self.name)


# ---- baselines -------------------------------------------------------------------------------------
class AlwaysEscalate:
    name = "always_escalate"

    def decide(self, text: str, intent: str, confidence: float = 1.0, hits: list[Hit] | None = None) -> Decision:
        return Decision("escalate", "policy: every message goes to a human", "always", self.name)


class HistoricalDMPolicy:
    """Escalate iff the majority of the k nearest historical cases were moved to DM by the brand."""
    name = "historical_dm_knn"

    def __init__(self, k: int = 5):
        self.k = k

    def decide(self, text: str, intent: str, confidence: float = 1.0, hits: list[Hit] | None = None) -> Decision:
        hits = (hits or [])[: self.k]
        if not hits:
            return Decision("escalate", "no similar past cases", "no_neighbours", self.name)
        frac = sum(h.reply_asks_dm for h in hits) / len(hits)
        disp = "escalate" if frac >= 0.5 else "auto"
        return Decision(disp, f"{frac:.0%} of {len(hits)} nearest past cases were moved to DM", "knn_dm", self.name)


# ---- layer 2: LLM triage ---------------------------------------------------------------------------
TRIAGE_SYSTEM = (
    "You are the triage step of {brand}'s AI support agent on Twitter. Decide whether the incoming message "
    "can be AUTO-HANDLED with a public self-serve reply, or must be ESCALATED to a human agent (who can look "
    "up the account over DM, issue refunds, or handle sensitive situations). Follow the policy rules in order; "
    "the first matching rule wins. When genuinely unsure, escalate. Respond with only a JSON object."
)

TRIAGE_PROMPT = """ESCALATION POLICY (ordered; first match wins):
{policy}

CLASSIFIED INTENT: {intent} (classifier confidence {confidence:.2f})
{history}
CUSTOMER MESSAGE:
\"\"\"{message}\"\"\"

Return JSON: {{"disposition": "auto" | "escalate", "rule": "<which policy rule applied, in a few words>", "reason": "<one sentence a supervisor would accept>"}}"""


@dataclass
class LLMTriage:
    llm: LLMClient
    taxonomy: Taxonomy
    name: str = "llm_triage"

    def decide(self, text: str, intent: str, confidence: float = 1.0, hits: list[Hit] | None = None) -> Decision | None:
        hist = ""
        if hits:
            n_dm = sum(h.reply_asks_dm for h in hits)
            hist = (f"HISTORICAL HANDLING: of the {len(hits)} most similar past cases, the brand moved {n_dm} to DM "
                    f"(needed a human/account lookup) and answered {len(hits) - n_dm} publicly.\n")
        prompt = TRIAGE_PROMPT.format(policy=self.taxonomy.policy_block(), intent=intent, confidence=confidence,
                                      history=hist, message=text)
        out = self.llm.complete_json(prompt, system=TRIAGE_SYSTEM.format(brand=self.taxonomy.brand),
                                     max_tokens=220, tag="triage")
        if out is None:
            return None
        if not isinstance(out, dict):
            return Decision("escalate", "triage output unparseable; defaulting to human", "parse_error", self.name)
        disp = str(out.get("disposition", "escalate")).strip().lower()
        disp = "auto" if disp.startswith("auto") else "escalate"
        return Decision(disp, str(out.get("reason", ""))[:300], str(out.get("rule", ""))[:120], self.name)


# ---- combined policy used by the agent ---------------------------------------------------------------
@dataclass
class LayeredTriage:
    """Hard rules override; then the LLM decides; then the rule policy on the predicted intent can still
    veto an auto-handle (either layer saying "escalate" wins — two independent signals, safety first)."""
    hard: RulePolicy
    llm: LLMTriage | None
    fallback: RulePolicy
    name: str = "layered"
    rules_can_veto: bool = True

    def decide(self, text: str, intent: str, confidence: float = 1.0, hits: list[Hit] | None = None) -> Decision:
        d = self.hard.match(text, intent)
        if d and d.disposition == "escalate":
            d.source = "hard_rule"
            return d
        if self.llm is None:
            return self.fallback.decide(text, intent, confidence)
        d2 = self.llm.decide(text, intent, confidence, hits)
        if d2 is None:
            return Decision("__pending__", "queued", "", self.name)
        if d2.disposition == "auto" and self.rules_can_veto:
            r = self.fallback.decide(text, intent, confidence)
            if r.disposition == "escalate":
                return Decision("escalate", f"rule veto: {r.reason} (LLM said auto: {d2.reason})", r.rule, "rule_veto")
        return d2
