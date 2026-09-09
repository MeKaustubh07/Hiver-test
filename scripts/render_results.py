"""Render docs/RESULTS.md (all report tables) from eval/results/*.json and eval/golden/agreement.json.
Usage: uv run python scripts/render_results.py [agent_tag] [judge_tag]"""
import json, sys
from pathlib import Path

tag = sys.argv[1] if len(sys.argv) > 1 else "claude-haiku-4-5"
jtag = sys.argv[2] if len(sys.argv) > 2 else "claude-sonnet-5"
R = Path("eval/results")
L = lambda name: json.loads((R / f"{name}.json").read_text()) if (R / f"{name}.json").exists() else None
out = []
P = out.append

I = L(f"intents_{tag}")
if I:
    P(f"### Intent classification (n={I['n']}, 10 classes)\n")
    P("| system | accuracy | 95% CI | macro-F1 | 95% CI |\n|---|---|---|---|---|")
    for name, m in I["systems"].items():
        P(f"| {name} | {m['accuracy']:.3f} | [{m['accuracy_ci95'][0]:.2f}, {m['accuracy_ci95'][1]:.2f}] | {m['macro_f1']:.3f} | [{m['macro_f1_ci95'][0]:.2f}, {m['macro_f1_ci95'][1]:.2f}] |")
    llm = next((k for k in I["systems"] if k.startswith("llm:")), None)
    if llm:
        P(f"\nPer-class F1 (support in brackets), LLM vs TF-IDF+LR:\n")
        P("| intent | n | LLM F1 | LLM P | LLM R | TF-IDF F1 |\n|---|---|---|---|---|---|")
        for c, v in I["systems"][llm]["per_class"].items():
            t = I["systems"]["tfidf_lr_cv"]["per_class"][c]
            P(f"| {c} | {v['support']} | {v['f1']:.2f} | {v['precision']:.2f} | {v['recall']:.2f} | {t['f1']:.2f} |")
T = L(f"triage_{tag}")
if T:
    P(f"\n### Auto-handle vs escalate (n={T['n']}, gold escalation rate {T['gold_escalation_rate']:.2f})\n")
    P("| system | accuracy | 95% CI | escalate P | escalate R | escalate F1 | **unsafe auto-handle rate** | escalation rate |\n|---|---|---|---|---|---|---|---|")
    for name, m in T["systems"].items():
        P(f"| {name} | {m['accuracy']:.3f} | [{m['accuracy_ci95'][0]:.2f}, {m['accuracy_ci95'][1]:.2f}] | {m['escalate_precision']:.2f} | {m['escalate_recall']:.2f} | {m['escalate_f1']:.2f} | **{m['unsafe_auto_rate']:.2f}** | {m['escalation_rate']:.2f} |")
S = L(f"stratum_breakdown_{tag}")
if S:
    P("\n### By sampling stratum\n")
    P("| stratum | n | intent acc (LLM) | intent acc (TF-IDF) | triage acc | unsafe auto-handle | gold escalation rate |\n|---|---|---|---|---|---|---|")
    for s, r in S["per_stratum"].items():
        P(f"| {s} | {r['n']} | {r['llm:'+tag]:.3f} | {r['tfidf_lr_cv']:.3f} | {r['triage_acc']:.3f} | {r['unsafe_auto']:.2f} | {r['gold_esc']:.2f} |")
    v = S["split_half_veto"]
    P(f"\nRule-veto policy, chosen after seeing the golden results: unsafe auto-handle {v['llm_only_unsafe_auto_full']:.2f} (LLM only) -> {v['veto_unsafe_auto_full']:.2f} (with veto) on the full set; "
      f"on 200 random held-out halves the reduction is {v['mean_unsafe_auto_reduction_on_held_out_half']:.3f} on average (5th-95th percentile {v['p5']:.3f} to {v['p95']:.3f}).")
for jt, label in ((f"judged_{tag}_by_{jtag}", "final system"), (f"judged_{tag}-v1_by_{jtag}", "before the unusable-link fix (v1)")):
    J = L(jt)
    if J:
        P(f"\n### Reply quality, LLM judge = {J['judge_model']} ({label})\n")
        P("| reply system | n | grounded | helpful | tone | safe | overall | pass rate | unsupported-claim rate | pass (gold auto) | pass (gold escalate) |\n|---|---|---|---|---|---|---|---|---|---|---|")
        for name, m in J["summary"].items():
            if not m.get("n"): continue
            pa = m.get("pass_rate_gold_auto"); pe = m.get("pass_rate_gold_escalate")
            P(f"| {name} | {m['n']} | {m['mean_grounded']:.2f} | {m['mean_helpful']:.2f} | {m['mean_tone']:.2f} | {m['mean_safe']:.2f} | {m['mean_overall']:.2f} | {m['pass_rate']:.2f} | {m['unsupported_claim_rate']:.2f} | {pa if pa is None else f'{pa:.2f}'} | {pe if pe is None else f'{pe:.2f}'} |")
A = L(f"agreement_{tag}_by_{jtag}")
if A:
    P(f"\n### Judge vs human agreement (n={A['n']} blind ratings)\n")
    P("| metric | value |\n|---|---|")
    for k in ("overall_weighted_kappa", "overall_spearman", "overall_exact_agreement", "overall_within_1", "pass_kappa", "pass_agreement", "human_pass_rate", "judge_pass_rate", "mean_human_overall", "mean_judge_overall"):
        if A.get(k) is not None:
            P(f"| {k} | {A[k]:.3f} |")
    P(f"| kappa interpretation | {A.get('kappa_interpretation')} |")
    for c, v in A.get("per_criterion", {}).items():
        P(f"| {c}: weighted kappa / within-1 | {v['weighted_kappa']:.2f} / {v['within_1']:.2f} |")
G = json.loads(Path("eval/golden/agreement.json").read_text()) if Path("eval/golden/agreement.json").exists() else None
if G:
    P(f"\n### Golden-set annotator agreement (n={G['n']}, 3 annotators, before adjudication)\n")
    P("| | unanimous | pairwise agreement | Fleiss' kappa |\n|---|---|---|---|")
    P(f"| intent | {G['intent_unanimous']:.1%} | {G['intent_pairwise_agreement']:.1%} | {G['intent_fleiss_kappa']:.2f} |")
    P(f"| disposition | {G['disposition_unanimous']:.1%} | {G['disposition_pairwise_agreement']:.1%} | {G['disposition_fleiss_kappa']:.2f} |")
Q = L("intents_qwen3-1.7b")
if Q:
    m = next((v for k, v in Q["systems"].items() if k.startswith("llm:")), None)
    TQ = L("triage_qwen3-1.7b"); tq = TQ["systems"].get("agent:qwen3-1.7b") if TQ else None
    if m:
        P(f"\n### Fully local comparison (qwen3:1.7b via Ollama, same prompts)\n")
        P(f"Intent accuracy {m['accuracy']:.3f} (macro-F1 {m['macro_f1']:.3f})" + (f"; triage accuracy {tq['accuracy']:.3f}, unsafe auto-handle {tq['unsafe_auto_rate']:.2f}, escalation rate {tq['escalation_rate']:.2f}." if tq else "."))
Path("docs/RESULTS.md").write_text("# Results tables (generated by scripts/render_results.py)\n\n" + "\n".join(out) + "\n")
print("\n".join(out)[:3000])
