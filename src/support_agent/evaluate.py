"""Evaluation runs over the golden set: intent classifiers, triage policies, reply drafters + judge.
Each run writes eval/results/<name>.json so the report is reproducible from artefacts."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedKFold

from .agent import SupportAgent
from .classify import LLMClassifier, KeywordClassifier, MajorityClassifier, TfidfLRClassifier
from .draft import LinkCatalog, NearestReplyDrafter, TemplateDrafter
from .intents import Taxonomy, load_style_guide
from .judge import ReplyJudge, evidence_block
from .llm import LLMClient
from .metrics import accuracy, bootstrap_ci, classification_metrics, judge_summary, macro_f1, triage_metrics
from .retrieval import Retriever
from .triage import AlwaysEscalate, HistoricalDMPolicy, RulePolicy

RESULTS = Path("eval/results")


def save(name: str, obj: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / f"{name}.json"
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    return p


def cv_predict(model_factory, texts: list[str], labels: list[str], folds: int = 5, seed: int = 0) -> list[str]:
    """Out-of-fold predictions so trained baselines are scored on examples they never saw."""
    preds = [""] * len(texts)
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    y = np.array(labels)
    for tr, te in skf.split(texts, y):
        m = model_factory().fit([texts[i] for i in tr], [labels[i] for i in tr])
        for i in te:
            preds[i] = m.predict(texts[i]).intent
    return preds


def eval_intents(golden: pd.DataFrame, taxonomy: Taxonomy, llm_preds: dict[int, dict] | None, tag: str) -> dict:
    texts, y = golden.customer_message.tolist(), golden.intent.tolist()
    labels = taxonomy.names
    systems = {
        "majority": cv_predict(MajorityClassifier, texts, y),
        "keyword_rules": [KeywordClassifier().predict(t).intent for t in texts],
        "tfidf_lr_cv": cv_predict(TfidfLRClassifier, texts, y),
    }
    if llm_preds:
        systems[f"llm:{tag}"] = [llm_preds.get(int(i), {}).get("intent", "other") for i in golden.id]
    out = {"n": len(y), "labels": labels, "gold_distribution": pd.Series(y).value_counts().to_dict(), "systems": {}}
    for name, p in systems.items():
        m = classification_metrics(y, p, labels)
        m["accuracy_ci95"] = bootstrap_ci(y, p, accuracy)
        m["macro_f1_ci95"] = bootstrap_ci(y, p, macro_f1)
        m["predictions"] = dict(zip([int(i) for i in golden.id], p))
        out["systems"][name] = m
    return out


def eval_triage(golden: pd.DataFrame, taxonomy: Taxonomy, retriever: Retriever, agent_out: dict[int, dict] | None,
                tag: str, intent_source: str = "gold") -> dict:
    y = golden.disposition.tolist()
    rules = RulePolicy(taxonomy)
    knn = HistoricalDMPolicy(k=5)
    systems = {"always_escalate": [], "rules_gold_intent": [], "historical_dm_knn": []}
    for r in golden.itertuples():
        hits = retriever.search(r.customer_message, k=5)
        systems["always_escalate"].append(AlwaysEscalate().decide(r.customer_message, r.intent).disposition)
        systems["rules_gold_intent"].append(rules.decide(r.customer_message, r.intent, 1.0).disposition)
        systems["historical_dm_knn"].append(knn.decide(r.customer_message, r.intent, 1.0, hits).disposition)
    if agent_out:
        systems[f"agent:{tag}"] = [agent_out.get(int(i), {}).get("disposition", "escalate") for i in golden.id]
        # rules using the agent's *predicted* intent (isolates the classifier's contribution)
        systems[f"rules_pred_intent:{tag}"] = [
            rules.decide(m, agent_out.get(int(i), {}).get("intent", "other"), agent_out.get(int(i), {}).get("confidence", 1.0)).disposition
            for i, m in zip(golden.id, golden.customer_message)]
        # ablation: escalate if EITHER the LLM triage OR the rule policy (on the predicted intent) escalates
        systems[f"agent_or_rules:{tag}"] = [
            "escalate" if "escalate" in (a, r) else "auto"
            for a, r in zip(systems[f"agent:{tag}"], systems[f"rules_pred_intent:{tag}"])]
    out = {"n": len(y), "gold_escalation_rate": float(np.mean([d == "escalate" for d in y])), "systems": {}}
    for name, p in systems.items():
        m = triage_metrics(y, p)
        m["accuracy_ci95"] = bootstrap_ci(y, p, accuracy)
        m["predictions"] = dict(zip([int(i) for i in golden.id], p))
        out["systems"][name] = m
    return out


def run_agent(golden: pd.DataFrame, agent: SupportAgent) -> tuple[dict[int, dict], int]:
    outs, pending = {}, 0
    def one(row):
        o = agent.handle(row.customer_message)
        return int(row.id), o.to_dict()
    for i, d in agent.llm.map(one, list(golden.itertuples()), desc="agent"):
        outs[i] = d
        pending += int(d["pending"])
    return outs, pending


def build_reply_candidates(golden: pd.DataFrame, taxonomy: Taxonomy, retriever: Retriever, agent_out: dict[int, dict],
                           tag: str) -> list[dict]:
    """One row per (example, system): template, nearest_reply, and the agent's grounded reply."""
    tmpl, near = TemplateDrafter(taxonomy), NearestReplyDrafter()
    rows = []
    for r in golden.itertuples():
        hits = retriever.search(r.customer_message, k=5)
        a = agent_out.get(int(r.id), {})
        intent = a.get("intent") or r.intent
        cands = {
            "template": tmpl.draft(r.customer_message, intent, hits).reply,
            "nearest_reply": near.draft(r.customer_message, intent, hits).reply,
            f"grounded_llm:{tag}": a.get("reply", ""),
        }
        for sys_name, reply in cands.items():
            if reply:
                rows.append({"example_id": int(r.id), "system": sys_name, "message": r.customer_message,
                             "gold_intent": r.intent, "gold_disposition": r.disposition, "reply": reply,
                             "hit_ids": [h.root_id for h in hits]})
    return rows


def judge_candidates(rows: list[dict], judge: ReplyJudge, retriever: Retriever) -> tuple[list[dict], int]:
    by_id = {int(x.root_id): x for x in retriever.df.itertuples()}
    from .retrieval import Hit
    def hits_for(ids):
        return [Hit(i, 0.0, by_id[i].customer_message, by_id[i].first_reply_clean, by_id[i].transcript, bool(by_id[i].reply_asks_dm))
                for i in ids if i in by_id]
    pending = 0
    def one(r):
        s = judge.score(r["message"], r["reply"], hits_for(r["hit_ids"]))
        return {**r, "judge": s.to_dict() if s else None}
    scored = judge.llm.map(one, rows, desc="judge")
    pending = sum(1 for s in scored if s["judge"] is None)
    return scored, pending


def summarise_judged(scored: list[dict]) -> dict:
    out = {}
    for sys_name in sorted({s["system"] for s in scored}):
        js = [s["judge"] for s in scored if s["system"] == sys_name and s["judge"]]
        out[sys_name] = judge_summary(js)
        # slice by gold disposition: did the reply respect escalation?
        for disp in ("auto", "escalate"):
            sub = [s["judge"] for s in scored if s["system"] == sys_name and s["judge"] and s["gold_disposition"] == disp]
            out[sys_name][f"pass_rate_gold_{disp}"] = float(np.mean([j["passed"] for j in sub])) if sub else None
    return out
