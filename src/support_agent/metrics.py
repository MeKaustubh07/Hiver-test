"""Automated metrics: intent classification, triage, judge aggregates, judge-vs-human agreement, bootstrap CIs."""
from __future__ import annotations

import numpy as np
from scipy.stats import spearmanr
from sklearn.metrics import cohen_kappa_score, confusion_matrix, f1_score, precision_recall_fscore_support


def classification_metrics(y_true: list[str], y_pred: list[str], labels: list[str]) -> dict:
    p, r, f, s = precision_recall_fscore_support(y_true, y_pred, labels=labels, zero_division=0)
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    return {
        "n": len(y_true),
        "accuracy": float(np.mean(np.array(y_true) == np.array(y_pred))),
        "macro_f1": float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_true, y_pred, labels=labels, average="weighted", zero_division=0)),
        "per_class": {lab: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i]), "support": int(s[i])}
                      for i, lab in enumerate(labels)},
        "confusion": {"labels": labels, "matrix": cm.tolist()},
    }


def bootstrap_ci(y_true: list, y_pred: list, metric, n_boot: int = 1000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap CI over examples for any metric(y_true, y_pred) -> float."""
    rng = np.random.default_rng(seed)
    yt, yp = np.array(y_true), np.array(y_pred)
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(yt), len(yt))
        vals.append(metric(yt[idx].tolist(), yp[idx].tolist()))
    return float(np.percentile(vals, 100 * alpha / 2)), float(np.percentile(vals, 100 * (1 - alpha / 2)))


def accuracy(y_true, y_pred) -> float:
    return float(np.mean(np.array(y_true) == np.array(y_pred)))


def macro_f1(y_true, y_pred) -> float:
    return float(f1_score(y_true, y_pred, average="macro", zero_division=0))


def triage_metrics(y_true: list[str], y_pred: list[str]) -> dict:
    """Escalate = positive class. 'unsafe_auto_rate' is the share of should-escalate cases the system
    auto-handled — the error that actually hurts a support team."""
    yt, yp = np.array(y_true), np.array(y_pred)
    tp = int(((yt == "escalate") & (yp == "escalate")).sum())
    fp = int(((yt == "auto") & (yp == "escalate")).sum())
    fn = int(((yt == "escalate") & (yp == "auto")).sum())
    tn = int(((yt == "auto") & (yp == "auto")).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {
        "n": len(yt), "accuracy": float((yt == yp).mean()),
        "escalate_precision": prec, "escalate_recall": rec,
        "escalate_f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
        "unsafe_auto_rate": fn / (tp + fn) if tp + fn else 0.0,
        "auto_precision": tn / (tn + fn) if tn + fn else 0.0,
        "escalation_rate": float((yp == "escalate").mean()), "gold_escalation_rate": float((yt == "escalate").mean()),
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }


def judge_summary(scores: list[dict]) -> dict:
    if not scores:
        return {"n": 0}
    out = {"n": len(scores)}
    for c in ("grounded", "helpful", "tone", "safe", "overall"):
        vals = [s[c] for s in scores if c in s]
        out[f"mean_{c}"] = float(np.mean(vals)) if vals else None
    out["pass_rate"] = float(np.mean([bool(s.get("passed", s.get("pass", False))) for s in scores]))
    out["unsupported_claim_rate"] = float(np.mean([bool(s.get("unsupported_claims")) for s in scores]))
    return out


def agreement(human: list[dict], judge: list[dict]) -> dict:
    """Both lists aligned by index; each dict has overall (1-5) and pass (bool). Reports weighted kappa,
    Spearman, exact and within-1 agreement on `overall`, and kappa/accuracy on pass/fail."""
    h = np.array([int(x["overall"]) for x in human])
    j = np.array([int(x["overall"]) for x in judge])
    hp = np.array([bool(x["pass"]) for x in human])
    jp = np.array([bool(x["pass"]) for x in judge])
    out = {"n": int(len(h))}
    if len(h) < 2:
        return out
    out["overall_weighted_kappa"] = float(cohen_kappa_score(h, j, weights="quadratic"))
    rho = spearmanr(h, j).correlation
    out["overall_spearman"] = None if rho is None or np.isnan(rho) else float(rho)
    out["overall_exact_agreement"] = float((h == j).mean())
    out["overall_within_1"] = float((np.abs(h - j) <= 1).mean())
    out["mean_human_overall"] = float(h.mean())
    out["mean_judge_overall"] = float(j.mean())
    out["pass_kappa"] = float(cohen_kappa_score(hp, jp)) if len(set(hp)) > 1 and len(set(jp)) > 1 else None
    out["pass_agreement"] = float((hp == jp).mean())
    out["human_pass_rate"] = float(hp.mean())
    out["judge_pass_rate"] = float(jp.mean())
    per = {}
    for c in ("grounded", "helpful", "tone", "safe"):
        if all(c in x for x in human) and all(c in x for x in judge):
            hc = np.array([int(x[c]) for x in human]); jc = np.array([int(x[c]) for x in judge])
            per[c] = {"weighted_kappa": float(cohen_kappa_score(hc, jc, weights="quadratic")),
                      "within_1": float((np.abs(hc - jc) <= 1).mean())}
    out["per_criterion"] = per
    return out


def interpret_kappa(k: float | None) -> str:
    if k is None:
        return "n/a"
    for lim, name in ((0.2, "slight"), (0.4, "fair"), (0.6, "moderate"), (0.8, "substantial")):
        if k < lim:
            return name
    return "almost perfect"
