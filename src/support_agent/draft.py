"""Reply drafting: two baselines and the grounded LLM drafter used by the agent.

  TemplateDrafter     — canned reply per intent (trivial baseline)
  NearestReplyDrafter — copy the brand's reply to the most similar past message (simple baseline)
  GroundedDrafter     — LLM writes a reply conditioned on the brand style guide, hard policies, the
                        intent, and the k most similar past cases with their actual resolutions.
                        URLs are only allowed if they appear in the evidence or the citable catalog.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .intents import Taxonomy
from .llm import LLMClient
from .retrieval import Hit, Retriever

CATALOG_PATH = Path("config/link_catalog.json")
URL_RE = re.compile(r"https?://\S+")
NAME_GREETING_RE = re.compile(r"^(hey|hi|hello|heya|howdy)\s+[A-Z][\w'-]*[!,.:]?\s*", re.I)
MAX_CHARS = 280


@dataclass
class Draft:
    reply: str
    evidence_ids: list[int] = field(default_factory=list)
    stripped_urls: list[str] = field(default_factory=list)
    notes: str = ""
    source: str = ""


class LinkCatalog:
    def __init__(self, path: Path = CATALOG_PATH, use_placeholders: bool | None = None):
        # SUPPORT_AGENT_LEGACY_LINKS=1 reproduces the pre-fix behaviour (root/DM links shown as URLs) for the
        # before/after comparison in the failure analysis.
        self.use_placeholders = (os.getenv("SUPPORT_AGENT_LEGACY_LINKS") != "1") if use_placeholders is None else use_placeholders
        d = json.loads(Path(path).read_text()) if Path(path).exists() else {"citable_pages": [], "tco_to_final": {}}
        self.pages = d["citable_pages"]
        self.tco = d["tco_to_final"]
        self.citable = {p["url"] for p in self.pages}
        self.title_of = {p["url"]: p["title"] for p in self.pages}

    def resolve(self, url: str) -> str:
        u = re.sub(r"[.,;:!?)\]…]+$", "", url)
        return self.tco.get(u, u)

    def placeholder(self, url: str) -> str | None:
        """Links that must never be copied into a reply: DM compose deep-links (account-specific) and
        2017 links that now redirect to a bare site root (the original target is gone)."""
        if not self.use_placeholders:
            return None
        if re.search(r"^https?://(x\.com|twitter\.com)/messages", url):
            return "[DM link]"
        if re.match(r"^https?://[^/]+/?$", url):
            return "[link to a page that no longer resolves]"
        if re.match(r"^https?://t\.co/", url):
            return "[unresolvable t.co link]"
        return None

    def annotate(self, text: str) -> str:
        """Replace t.co links with '<final url> ("title")' when known, so models see real targets.
        Unusable targets are shown as bracketed placeholders so they cannot be copied as citations."""
        def sub(m):
            f = self.resolve(m.group(0))
            ph = self.placeholder(f)
            if ph:
                return ph
            t = self.title_of.get(f)
            return f'{f} ("{t}")' if t else f
        return URL_RE.sub(sub, text)

    def block(self, max_pages: int = 25) -> str:
        return "\n".join(f'- "{p["title"]}": {p["url"]}' for p in self.pages[:max_pages])


def normalise_greeting(reply: str) -> str:
    return NAME_GREETING_RE.sub(lambda m: m.group(1).capitalize() + " there! ", reply).strip()


# ---- baselines -------------------------------------------------------------------------------------
class TemplateDrafter:
    name = "template"

    def __init__(self, taxonomy: Taxonomy):
        self.taxonomy = taxonomy

    def draft(self, message: str, intent: str, hits: list[Hit]) -> Draft:
        it = self.taxonomy.get(intent)
        text = it.typical_resolution if it else "Thanks for reaching out! Can you DM us more details so we can help?"
        return Draft(text[:MAX_CHARS], [], [], "canned reply for intent", self.name)


class NearestReplyDrafter:
    name = "nearest_reply"

    def draft(self, message: str, intent: str, hits: list[Hit]) -> Draft:
        if not hits:
            return Draft("Thanks for reaching out! Can you DM us more details so we can help?", [], [], "no neighbours", self.name)
        h = hits[0]
        return Draft(normalise_greeting(h.first_reply)[:MAX_CHARS], [h.root_id], [], f"copied reply of thread {h.root_id}", self.name)


# ---- grounded LLM drafter --------------------------------------------------------------------------
DRAFT_SYSTEM = """You are the reply-drafting step of @{brand}'s AI support agent on Twitter. Write ONE public reply tweet.
Ground every claim in the evidence: the brand's past resolutions of similar cases, the citable help pages, and the
hard policies. Never invent facts, features, timelines, or URLs. Respond with only a JSON object."""

DRAFT_PROMPT = """BRAND STYLE GUIDE (follow tone and conventions; do NOT add an agent sign-off like /XX):
{style}

{policies}

CITABLE HELP PAGES (the ONLY URLs you may include; cite by pasting the URL exactly):
{catalog}

CLASSIFIED INTENT: {intent}
TRIAGE DECISION: {disposition} — {reason}

EVIDENCE — the {k} most similar past customer messages and how the brand actually resolved them:
{evidence}

CUSTOMER MESSAGE:
\"\"\"{message}\"\"\"

Write the reply. Rules: max {max_chars} characters; if the triage decision is "escalate", the reply must
acknowledge the issue and ask the customer to DM their account email/username (never passwords); if "auto",
give the concrete self-serve resolution the brand historically used. Don't promise refunds, timelines, or
features. Don't ask for information the evidence shows isn't needed.
Return JSON: {{"reply": "<tweet>", "evidence_used": [<evidence numbers you relied on>], "notes": "<=15 words on what you grounded on"}}"""

SHORTEN_PROMPT = """Shorten this support reply to at most {max_chars} characters without changing its meaning, dropping any URL, or adding new claims:
\"\"\"{reply}\"\"\"
Return JSON: {{"reply": "<shortened tweet>"}}"""


POLICIES_PATH = Path("config/policies.md")


def load_policies(path: Path = POLICIES_PATH) -> str:
    return Path(path).read_text() if Path(path).exists() else ""


@dataclass
class GroundedDrafter:
    llm: LLMClient
    taxonomy: Taxonomy
    style_guide: str
    catalog: LinkCatalog
    k: int = 5
    name: str = "grounded_llm"
    policies: str = field(default_factory=load_policies)

    def evidence_block(self, hits: list[Hit]) -> str:
        lines = []
        for i, h in enumerate(hits[: self.k], 1):
            handled = "moved to DM (human)" if h.reply_asks_dm else "answered publicly"
            lines.append(f'[{i}] customer: "{h.customer_message[:240]}"\n    brand ({handled}): "{self.catalog.annotate(h.first_reply)[:300]}"')
        return "\n".join(lines) if lines else "(no similar past cases found)"

    def allowed_urls(self, hits: list[Hit]) -> set[str]:
        allowed = set(self.catalog.citable)
        for h in hits[: self.k]:
            for u in URL_RE.findall(h.first_reply):
                f = self.catalog.resolve(u)
                if self.catalog.placeholder(f) is None:
                    allowed.add(f)
        return allowed

    def ground_check(self, reply: str, allowed: set[str]) -> tuple[str, list[str]]:
        """Strip any URL not in the allowed set (hallucinated links are the #1 grounding failure)."""
        stripped = []
        def sub(m):
            u = self.catalog.resolve(m.group(0))
            if u in allowed:
                return u
            stripped.append(m.group(0))
            return ""
        cleaned = URL_RE.sub(sub, reply)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned, stripped

    def draft(self, message: str, intent: str, hits: list[Hit], disposition: str = "auto", reason: str = "") -> Draft | None:
        prompt = DRAFT_PROMPT.format(style=self.style_guide, policies=self.policies, catalog=self.catalog.block(), intent=intent,
                                     disposition=disposition, reason=reason, k=min(self.k, len(hits)),
                                     evidence=self.evidence_block(hits), message=message, max_chars=MAX_CHARS)
        out = self.llm.complete_json(prompt, system=DRAFT_SYSTEM.format(brand=self.taxonomy.brand),
                                     max_tokens=400, tag="draft")
        if out is None:
            return None
        reply = str(out.get("reply", "")).strip() if isinstance(out, dict) else ""
        if not reply:
            return Draft("Thanks for reaching out! Can you DM us your account's email address so we can take a look?",
                         [], [], "model returned no reply; safe fallback", self.name)
        if len(reply) > MAX_CHARS:
            fix = self.llm.complete_json(SHORTEN_PROMPT.format(reply=reply, max_chars=MAX_CHARS),
                                         system=DRAFT_SYSTEM.format(brand=self.taxonomy.brand), max_tokens=300, tag="shorten")
            if fix is None:
                return None
            if isinstance(fix, dict) and fix.get("reply"):
                reply = str(fix["reply"]).strip()
        reply, stripped = self.ground_check(reply, self.allowed_urls(hits))
        used = out.get("evidence_used", []) if isinstance(out, dict) else []
        ids = [hits[int(i) - 1].root_id for i in used if str(i).isdigit() and 0 < int(i) <= len(hits)]
        return Draft(reply[: MAX_CHARS + 20], ids, stripped, str(out.get("notes", ""))[:200] if isinstance(out, dict) else "", self.name)
