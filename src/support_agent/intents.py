"""Intent taxonomy + escalation policy, loaded from config/intents.json (induced from the data)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path("config")
INTENTS_PATH = CONFIG_DIR / "intents.json"
STYLE_PATH = CONFIG_DIR / "style_guide.md"


@dataclass(frozen=True)
class Intent:
    name: str
    definition: str
    includes: str
    excludes: str
    examples: list[str]
    typical_resolution: str
    default_disposition: str  # auto | escalate | depends


@dataclass(frozen=True)
class EscalationRule:
    rule: str
    applies_to: list[str]  # intent names or ["any"]
    disposition: str       # auto | escalate
    reason_template: str


@dataclass
class Taxonomy:
    brand: str
    intents: list[Intent]
    rules: list[EscalationRule]
    tie_breaks: list[str]

    @property
    def names(self) -> list[str]:
        return [i.name for i in self.intents]

    def get(self, name: str) -> Intent | None:
        return next((i for i in self.intents if i.name == name), None)

    def normalise(self, name: str | None) -> str:
        """Map a model-produced label onto a taxonomy name (case/space tolerant); unknown -> 'other'."""
        if not name:
            return "other"
        key = str(name).strip().lower().replace(" ", "_").replace("-", "_")
        for n in self.names:
            if key == n or key.startswith(n) or n.startswith(key):
                return n
        return "other"

    def prompt_block(self, with_examples: int = 2) -> str:
        lines = []
        for i in self.intents:
            lines.append(f"- {i.name}: {i.definition}")
            lines.append(f"    includes: {i.includes}")
            lines.append(f"    excludes: {i.excludes}")
            for ex in i.examples[:with_examples]:
                lines.append(f"    e.g. \"{ex}\"")
        return "\n".join(lines)

    def policy_block(self) -> str:
        lines = []
        for k, r in enumerate(self.rules, 1):
            scope = ", ".join(r.applies_to)
            lines.append(f"{k}. [{r.disposition.upper()}] ({scope}) {r.rule}")
        if self.tie_breaks:
            lines.append("Tie-breaks: " + " | ".join(self.tie_breaks))
        return "\n".join(lines)


def load_taxonomy(path: Path = INTENTS_PATH) -> Taxonomy:
    d = json.loads(Path(path).read_text())
    intents = [Intent(i["name"], i["definition"], i.get("includes", ""), i.get("excludes", ""),
                      list(i.get("examples", [])), i.get("typical_resolution", ""),
                      i.get("default_disposition", "depends")) for i in d["intents"]]
    rules = [EscalationRule(r["rule"], list(r.get("applies_to", ["any"])), r["disposition"], r["reason_template"])
             for r in d.get("escalation_rules", [])]
    return Taxonomy(d.get("brand", "SpotifyCares"), intents, rules, list(d.get("tie_breaks", [])))


def load_style_guide(path: Path = STYLE_PATH) -> str:
    return Path(path).read_text() if Path(path).exists() else ""
