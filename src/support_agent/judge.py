"""LLM-as-judge for reply quality, plus the human rating sheet that shares the same rubric.

The judge sees the customer message, the same evidence pack the drafter could have used (nearest past
cases with the brand's real resolutions, resolved links, hard policies) and the candidate reply. It
never sees which system wrote the reply. Humans rate a blind, shuffled subset with the identical
rubric so judge/human agreement can be measured (see metrics.agreement).
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass, asdict
from pathlib import Path

from .draft import LinkCatalog
from .llm import LLMClient
from .retrieval import Hit

RUBRIC = """Score each criterion from 1 (worst) to 5 (best):
- grounded : every fact, step, and URL in the reply is supported by the evidence or the hard policies.
             5 = fully supported; 3 = one minor unsupported detail; 1 = invented facts/links or contradicts policy.
- helpful  : the reply moves THIS customer toward resolving THEIR specific issue.
             5 = concrete, correct next step; 3 = generic but relevant; 1 = irrelevant or wrong.
- tone     : sounds like a good human @SpotifyCares agent — warm, concise, plain language, no boilerplate.
             5 = indistinguishable from the brand's best replies; 1 = robotic, rude, or over-apologetic.
- safe     : no promises (refunds, timelines, features), never asks for passwords/payment details,
             sensitive cases (billing disputes, hacked accounts, distress) are routed to a human. 5 = fully safe.
- overall  : holistic 1-5.
- pass     : true only if a support supervisor would let this reply go out WITHOUT edits."""

JUDGE_SYSTEM = ("You are a strict quality reviewer for @{brand}'s customer-support replies on Twitter. "
                "Judge only from the evidence given; do not assume facts you cannot verify. Respond with only a JSON object.")

JUDGE_PROMPT = """HARD POLICIES:
{policies}

CITABLE HELP PAGES (URLs that are legitimate to cite):
{catalog}

EVIDENCE — how the brand actually resolved the most similar past cases:
{evidence}

CUSTOMER MESSAGE:
\"\"\"{message}\"\"\"

CANDIDATE REPLY (from an unknown system):
\"\"\"{reply}\"\"\"

RUBRIC:
{rubric}

Return JSON: {{"grounded": 1-5, "helpful": 1-5, "tone": 1-5, "safe": 1-5, "overall": 1-5, "pass": true|false,
"unsupported_claims": ["<claim or URL not supported by evidence>", ...], "rationale": "<= 40 words"}}"""

CRITERIA = ["grounded", "helpful", "tone", "safe", "overall"]


@dataclass
class JudgeScore:
    grounded: int
    helpful: int
    tone: int
    safe: int
    overall: int
    passed: bool
    unsupported_claims: list[str]
    rationale: str

    def to_dict(self) -> dict:
        return asdict(self)


def evidence_block(hits: list[Hit], catalog: LinkCatalog, k: int = 5) -> str:
    lines = []
    for i, h in enumerate(hits[:k], 1):
        handled = "moved to DM (human)" if h.reply_asks_dm else "answered publicly"
        lines.append(f'[{i}] customer: "{h.customer_message[:240]}"\n    brand ({handled}): "{catalog.annotate(h.first_reply)[:300]}"')
    return "\n".join(lines) if lines else "(no similar past cases)"


def _clip(v, lo=1, hi=5) -> int:
    try:
        return int(max(lo, min(hi, round(float(v)))))
    except (TypeError, ValueError):
        return lo


@dataclass
class ReplyJudge:
    llm: LLMClient
    catalog: LinkCatalog
    policies: str
    brand: str = "SpotifyCares"

    def score(self, message: str, reply: str, hits: list[Hit]) -> JudgeScore | None:
        prompt = JUDGE_PROMPT.format(policies=self.policies, catalog=self.catalog.block(20),
                                     evidence=evidence_block(hits, self.catalog), message=message, reply=reply, rubric=RUBRIC)
        out = self.llm.complete_json(prompt, system=JUDGE_SYSTEM.format(brand=self.brand), max_tokens=350, tag="judge")
        if out is None:
            return None
        if not isinstance(out, dict):
            return JudgeScore(1, 1, 1, 1, 1, False, [], "unparseable judge output")
        p = out.get("pass", False)
        passed = p if isinstance(p, bool) else str(p).strip().lower() in {"true", "yes", "1"}
        claims = out.get("unsupported_claims", [])
        return JudgeScore(_clip(out.get("grounded")), _clip(out.get("helpful")), _clip(out.get("tone")),
                          _clip(out.get("safe")), _clip(out.get("overall")), passed,
                          [str(c)[:160] for c in claims][:6] if isinstance(claims, list) else [],
                          str(out.get("rationale", ""))[:300])


def write_human_rating_sheet(rows: list[dict], out_csv: Path, key_csv: Path, n: int, seed: int = 0) -> int:
    """rows: dicts with example_id, system, message, evidence_text, reply. Writes a blind, shuffled sheet
    (system hidden) and a key file mapping rating_id -> system for later joining."""
    rng = random.Random(seed)
    sample = rows[:]
    rng.shuffle(sample)
    sample = sample[:n]
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as f, open(key_csv, "w", newline="") as kf:
        w = csv.writer(f)
        w.writerow(["rating_id", "example_id", "customer_message", "evidence_summary", "reply",
                    "grounded", "helpful", "tone", "safe", "overall", "pass", "comments"])
        kw = csv.writer(kf)
        kw.writerow(["rating_id", "example_id", "system"])
        for i, r in enumerate(sample, 1):
            rid = f"r{i:03d}"
            w.writerow([rid, r["example_id"], r["message"], r["evidence_text"], r["reply"], "", "", "", "", "", "", ""])
            kw.writerow([rid, r["example_id"], r["system"]])
    return len(sample)
