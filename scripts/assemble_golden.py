"""Assemble eval/golden/golden_set.csv from the 3-annotator + adjudicator labelling run.

Input: the workflow result JSON (batches[].annotators[].labels, batches[].final) and candidates.csv.
Output: golden_set.csv (final labels + per-annotator columns), review_queue.csv (disagreements first),
agreement.json (pairwise agreement + Fleiss' kappa for intent and disposition, before adjudication).
"""
import json, sys
from collections import Counter
from pathlib import Path
import numpy as np, pandas as pd

res = json.load(open(sys.argv[1]))
res = res.get("result", res)
cands = pd.read_csv("eval/golden/candidates.csv", dtype={"id": "int64"}).fillna("")
cands["id"] = cands.id.astype(str)

per_annot = {}  # id -> {A: (intent, disp, hard), B: ..., C: ...}
final = {}
feedback = []
for b in res["batches"]:
    for k, ann in enumerate(b["annotators"]):
        who = "ABC"[k]
        for l in ann["labels"]:
            per_annot.setdefault(str(l["id"]), {})[who] = (l["intent"], l["disposition"], bool(l.get("hard")), bool(l.get("unlabelable")), l.get("reason", ""))
    for l in b["final"]:
        final[str(l["id"])] = l
    feedback.extend(b.get("guide_feedback", []))

def fleiss_kappa(mat: np.ndarray) -> float:
    """mat: items x categories counts. Standard Fleiss' kappa."""
    n = mat.sum(axis=1)[0]
    p_j = mat.sum(axis=0) / mat.sum()
    P_i = ((mat ** 2).sum(axis=1) - n) / (n * (n - 1))
    P_bar, P_e = P_i.mean(), (p_j ** 2).sum()
    return float((P_bar - P_e) / (1 - P_e)) if P_e < 1 else 1.0

rows, agree_i, agree_d = [], [], []
intent_cats = sorted({v[0] for d in per_annot.values() for v in d.values()})
mat_i, mat_d = [], []
for r in cands.itertuples():
    a = per_annot.get(r.id, {})
    f = final.get(r.id)
    if not f:
        continue
    labs = [a[w][0] for w in "ABC" if w in a]
    disps = [a[w][1] for w in "ABC" if w in a]
    n_agree_i = Counter(labs).most_common(1)[0][1] if labs else 0
    n_agree_d = Counter(disps).most_common(1)[0][1] if disps else 0
    if len(labs) == 3:
        mat_i.append([labs.count(c) for c in intent_cats]); mat_d.append([disps.count("auto"), disps.count("escalate")])
    rows.append({
        "id": r.id, "stratum": r.stratum, "customer_message": r.customer_message, "thread_context": r.thread_context,
        "intent": f["intent"], "disposition": f["disposition"], "reason": f.get("reason", ""),
        "unlabelable": bool(f.get("unlabelable")), "notes": f.get("adjudication_note", ""),
        "label_A": a.get("A", ("", "", False, False, ""))[0], "label_B": a.get("B", ("", "", False, False, ""))[0], "label_C": a.get("C", ("", "", False, False, ""))[0],
        "disp_A": a.get("A", ("", "", False, False, ""))[1], "disp_B": a.get("B", ("", "", False, False, ""))[1], "disp_C": a.get("C", ("", "", False, False, ""))[1],
        "intent_agreement": f"{n_agree_i}/{len(labs)}", "disposition_agreement": f"{n_agree_d}/{len(disps)}",
        "any_hard": any(a[w][2] for w in a), "reviewed_by_human": "",
    })
g = pd.DataFrame(rows)
Path("eval/golden").mkdir(parents=True, exist_ok=True)
g.to_csv("eval/golden/golden_set.csv", index=False)
q = g[(g.intent_agreement != "3/3") | (g.disposition_agreement != "3/3") | g.any_hard | g.unlabelable]
q = q.sort_values(["intent_agreement", "disposition_agreement"])
q[["id", "stratum", "customer_message", "label_A", "label_B", "label_C", "disp_A", "disp_B", "disp_C", "intent", "disposition", "reason", "notes", "unlabelable", "reviewed_by_human"]].to_csv("eval/golden/review_queue.csv", index=False)
mi, md = np.array(mat_i), np.array(mat_d)
pair = lambda col: float(np.mean([(g[f"{col}_A"] == g[f"{col}_B"]).mean(), (g[f"{col}_A"] == g[f"{col}_C"]).mean(), (g[f"{col}_B"] == g[f"{col}_C"]).mean()]))
stats = {
    "n": int(len(g)), "n_unlabelable": int(g.unlabelable.sum()),
    "intent_unanimous": float((g.intent_agreement == "3/3").mean()), "disposition_unanimous": float((g.disposition_agreement == "3/3").mean()),
    "intent_pairwise_agreement": pair("label"), "disposition_pairwise_agreement": pair("disp"),
    "intent_fleiss_kappa": fleiss_kappa(mi) if len(mi) else None, "disposition_fleiss_kappa": fleiss_kappa(md) if len(md) else None,
    "final_intent_distribution": g[~g.unlabelable].intent.value_counts().to_dict(),
    "final_disposition_distribution": g[~g.unlabelable].disposition.value_counts().to_dict(),
    "adjudicator_overrode_majority": int(sum(1 for r in g.itertuples() if Counter([r.label_A, r.label_B, r.label_C]).most_common(1)[0][1] >= 2 and Counter([r.label_A, r.label_B, r.label_C]).most_common(1)[0][0] != r.intent)),
    "guide_feedback": feedback,
}
json.dump(stats, open("eval/golden/agreement.json", "w"), indent=1)
print(json.dumps({k: v for k, v in stats.items() if k != "guide_feedback"}, indent=1))
print("review queue:", len(q))
