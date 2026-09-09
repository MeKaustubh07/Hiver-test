"""Command-line entry points. `uv run support-agent --help`."""
from __future__ import annotations

import json
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich import print as rprint

load_dotenv()
from rich.table import Table

app = typer.Typer(add_completion=False, help="SpotifyCares AI support agent + evaluation harness")
BRAND = "SpotifyCares"


def _llm(backend: str | None, model: str | None):
    from .llm import LLMClient
    return LLMClient(backend=backend, model=model)


def _retriever(exclude_golden: bool = True):
    from .corpus import load_corpus
    from .golden import load_golden
    from .retrieval import Retriever
    ex: set[int] = set()
    if exclude_golden:
        # exclude every sampled candidate (labelled or not) so no example can retrieve its own thread
        for f in ("eval/golden/golden_set.csv", "eval/golden/candidates.csv"):
            if Path(f).exists():
                ex |= set(int(i) for i in load_golden(Path(f), require_labels=False).id)
    return Retriever(load_corpus(BRAND), exclude=ex, brand=BRAND)


@app.command()
def build_corpus(brand: str = BRAND):
    """Reconstruct threads for a brand from the raw Kaggle CSV -> data/processed/<brand>_threads.parquet"""
    from .corpus import build_corpus as _b
    c = _b(brand)
    rprint(f"[green]{brand}: {len(c)} English first-contact threads[/green]")


@app.command()
def sample_golden(n_random: int = 150, n_targeted: int = 70, n_hard: int = 40, seed: int = 42,
                  out: Path = Path("eval/golden/candidates.csv")):
    """Sample unlabelled golden-set candidates (random + targeted + hard strata)."""
    from .corpus import load_corpus
    from .golden import sample_candidates
    df = sample_candidates(load_corpus(BRAND), n_random, n_targeted, n_hard, seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    rprint(f"[green]wrote {len(df)} candidates -> {out}[/green]")
    rprint(df.stratum.value_counts().to_dict())


@app.command()
def demo(message: str, backend: str = None, model: str = None):
    """Run the full agent on one message and print every intermediate."""
    from .agent import SupportAgent
    agent = SupportAgent(_llm(backend, model), _retriever())
    o = agent.handle(message)
    rprint(json.dumps(o.to_dict(), indent=1, ensure_ascii=False))


@app.command()
def run_agent(backend: str = None, model: str = None, tag: str = None, golden: Path = Path("eval/golden/golden_set.csv"),
              unlabelled: bool = False):
    """Run the agent over the golden set -> eval/results/agent_<tag>.json (re-run until nothing is pending)."""
    from .agent import SupportAgent
    from .evaluate import run_agent as _run, save
    from .golden import load_golden
    llm = _llm(backend, model)
    tag = tag or llm.model.replace(":", "-").replace("/", "-")
    agent = SupportAgent(llm, _retriever())
    g = load_golden(golden, require_labels=not unlabelled)
    outs, pending = _run(g, agent)
    save(f"agent_{tag}", {"tag": tag, "model": llm.model, "backend": llm.backend, "outputs": outs})
    rprint(f"[green]agent run: {len(outs)} examples, {pending} pending, llm stats {llm.stats}[/green]")


@app.command()
def eval_intents(tag: str = None, golden: Path = Path("eval/golden/golden_set.csv")):
    """Score intent classifiers (baselines + agent's LLM classifier if agent_<tag>.json exists)."""
    from .evaluate import eval_intents as _e, save
    from .golden import load_golden
    from .intents import load_taxonomy
    g, tax = load_golden(golden), load_taxonomy()
    llm_preds = _load_agent(tag)
    res = _e(g, tax, llm_preds, tag or "")
    save(f"intents_{tag or 'baselines'}", res)
    t = Table("system", "accuracy", "95% CI", "macro-F1", "95% CI")
    for name, m in res["systems"].items():
        t.add_row(name, f"{m['accuracy']:.3f}", f"[{m['accuracy_ci95'][0]:.2f}, {m['accuracy_ci95'][1]:.2f}]",
                  f"{m['macro_f1']:.3f}", f"[{m['macro_f1_ci95'][0]:.2f}, {m['macro_f1_ci95'][1]:.2f}]")
    rprint(t)


@app.command()
def eval_triage(tag: str = None, golden: Path = Path("eval/golden/golden_set.csv")):
    """Score auto-vs-escalate policies (baselines + agent)."""
    from .evaluate import eval_triage as _e, save
    from .golden import load_golden
    from .intents import load_taxonomy
    g, tax = load_golden(golden), load_taxonomy()
    res = _e(g, tax, _retriever(), _load_agent(tag), tag or "")
    save(f"triage_{tag or 'baselines'}", res)
    t = Table("system", "acc", "esc-P", "esc-R", "esc-F1", "unsafe-auto", "esc-rate")
    for name, m in res["systems"].items():
        t.add_row(name, f"{m['accuracy']:.3f}", f"{m['escalate_precision']:.2f}", f"{m['escalate_recall']:.2f}",
                  f"{m['escalate_f1']:.2f}", f"{m['unsafe_auto_rate']:.2f}", f"{m['escalation_rate']:.2f}")
    rprint(t)


@app.command()
def judge_replies(tag: str, judge_backend: str = None, judge_model: str = None, golden: Path = Path("eval/golden/golden_set.csv")):
    """LLM-judge the template / nearest-reply / grounded replies -> eval/results/judged_<tag>.json"""
    from .draft import LinkCatalog
    from .evaluate import build_reply_candidates, judge_candidates, save, summarise_judged
    from .golden import load_golden
    from .intents import load_taxonomy
    from .judge import ReplyJudge
    g, tax = load_golden(golden), load_taxonomy()
    agent_out = _load_agent(tag) or {}
    ret = _retriever()
    rows = build_reply_candidates(g, tax, ret, agent_out, tag)
    jl = _llm(judge_backend, judge_model)
    judge = ReplyJudge(jl, LinkCatalog(), policies=_policies(), brand=BRAND)
    scored, pending = judge_candidates(rows, judge, ret)
    jt = jl.model.replace(":", "-").replace("/", "-")
    save(f"judged_{tag}_by_{jt}", {"tag": tag, "judge_model": jl.model, "rows": scored, "summary": summarise_judged(scored)})
    rprint(f"[green]{len(scored)} replies judged, {pending} pending; judge stats {jl.stats}[/green]")
    _print_judge(summarise_judged(scored))


@app.command()
def make_rating_sheet(tag: str, judge_tag: str, n: int = 60, seed: int = 0):
    """Blind, shuffled human rating sheet (same rubric as the judge) -> eval/human/ratings_<tag>.csv + key."""
    from .draft import LinkCatalog
    from .judge import write_human_rating_sheet
    p = Path(f"eval/results/judged_{tag}_by_{judge_tag}.json")
    d = json.loads(p.read_text())
    ret = _retriever(); cat = LinkCatalog()
    by_id = {int(x.root_id): x for x in ret.df.itertuples()}
    rows = []
    for r in d["rows"]:
        ev = "; ".join(f"[{i}] {cat.annotate(by_id[h].first_reply_clean)[:140]}" for i, h in enumerate(r["hit_ids"][:3], 1) if h in by_id)
        rows.append({"example_id": r["example_id"], "system": r["system"], "message": r["message"], "evidence_text": ev, "reply": r["reply"]})
    n_written = write_human_rating_sheet(rows, Path(f"eval/human/ratings_{tag}.csv"), Path(f"eval/human/ratings_{tag}_key.csv"), n, seed)
    rprint(f"[green]wrote {n_written} blind rows -> eval/human/ratings_{tag}.csv (key in ratings_{tag}_key.csv)[/green]")


@app.command()
def agreement(tag: str, judge_tag: str, ratings: Path = None):
    """Judge-vs-human agreement on the rated subset -> eval/results/agreement_<tag>.json"""
    import pandas as pd
    from .evaluate import save
    from .metrics import agreement as _agree, interpret_kappa
    ratings = ratings or Path(f"eval/human/ratings_{tag}.csv")
    key = pd.read_csv(f"eval/human/ratings_{tag}_key.csv")
    h = pd.read_csv(ratings).merge(key, on=["rating_id", "example_id"])
    h = h[h.overall.notna() & (h.overall.astype(str).str.strip() != "")]
    if h.empty:
        rprint("[red]no human ratings filled in yet[/red]"); raise typer.Exit(1)
    d = json.loads(Path(f"eval/results/judged_{tag}_by_{judge_tag}.json").read_text())
    jmap = {(r["example_id"], r["system"]): r["judge"] for r in d["rows"] if r["judge"]}
    human, judge = [], []
    for r in h.itertuples():
        j = jmap.get((int(r.example_id), r.system))
        if j is None:
            continue
        human.append({"overall": int(float(r.overall)), "pass": str(r.__getattribute__("pass")).strip().lower() in {"true", "1", "yes", "y"},
                      **{c: int(float(getattr(r, c))) for c in ("grounded", "helpful", "tone", "safe") if str(getattr(r, c)).strip() not in {"", "nan"}}})
        judge.append({"overall": j["overall"], "pass": j["passed"], "grounded": j["grounded"], "helpful": j["helpful"], "tone": j["tone"], "safe": j["safe"]})
    res = _agree(human, judge)
    res["kappa_interpretation"] = interpret_kappa(res.get("overall_weighted_kappa"))
    save(f"agreement_{tag}_by_{judge_tag}", res)
    rprint(json.dumps(res, indent=1))


@app.command()
def queue_status():
    """How many prompts are waiting in the batch queue (queue backend)."""
    from .llm import pending_requests
    p = pending_requests()
    by = {}
    for r in p:
        by[r.get("tag", "")] = by.get(r.get("tag", ""), 0) + 1
    rprint({"pending": len(p), "by_tag": by})


@app.command()
def ingest_answers(answers: Path, backend_label: str = "claude-code-batch"):
    """Write batch answers ({key, text} JSONL) into the LLM cache."""
    from .llm import ingest_answers as _ing
    rprint(f"[green]ingested {_ing(answers, backend_label)} answers[/green]")


@app.command()
def report():
    """Print every result table found in eval/results."""
    for p in sorted(Path("eval/results").glob("*.json")):
        d = json.loads(p.read_text())
        rprint(f"\n[bold]{p.name}[/bold]")
        if "systems" in d:
            for name, m in d["systems"].items():
                keys = [k for k in ("accuracy", "macro_f1", "escalate_f1", "unsafe_auto_rate") if k in m]
                rprint("  ", name, {k: round(m[k], 3) for k in keys})
        elif "summary" in d:
            _print_judge(d["summary"])
        elif "overall_weighted_kappa" in d:
            rprint("  ", {k: v for k, v in d.items() if not isinstance(v, dict)})


def _print_judge(summary: dict):
    t = Table("system", "n", "grounded", "helpful", "tone", "safe", "overall", "pass", "unsupp.")
    for name, m in summary.items():
        if m.get("n"):
            t.add_row(name, str(m["n"]), *[f"{m[f'mean_{c}']:.2f}" for c in ("grounded", "helpful", "tone", "safe", "overall")],
                      f"{m['pass_rate']:.2f}", f"{m['unsupported_claim_rate']:.2f}")
    rprint(t)


def _load_agent(tag: str | None) -> dict[int, dict] | None:
    if not tag:
        return None
    p = Path(f"eval/results/agent_{tag}.json")
    if not p.exists():
        rprint(f"[yellow]no {p}; run `run-agent --tag {tag}` first[/yellow]")
        return None
    return {int(k): v for k, v in json.loads(p.read_text())["outputs"].items()}


def _policies() -> str:
    from .intents import load_taxonomy
    p = Path("config/policies.md")
    return p.read_text() if p.exists() else load_taxonomy().policy_block()


if __name__ == "__main__":
    app()


@app.command()
def failures(tag: str, judge_tag: str = None, out: Path = None, max_per_bucket: int = 25):
    """Dump concrete failure cases (wrong intent, unsafe auto-handle, over-escalation, low-scoring replies)
    with the message, gold label, prediction, evidence and reply -> eval/results/failures_<tag>.md"""
    from .golden import load_golden
    g = load_golden().set_index("id")
    a = _load_agent(tag) or {}
    lines = [f"# Failure cases for agent `{tag}`\n"]
    wrong, unsafe, over = [], [], []
    for i, o in a.items():
        if i not in g.index:
            continue
        row = g.loc[i]
        if o.get("intent") != row.intent:
            wrong.append((i, row, o))
        if row.disposition == "escalate" and o.get("disposition") == "auto":
            unsafe.append((i, row, o))
        if row.disposition == "auto" and o.get("disposition") == "escalate":
            over.append((i, row, o))
    def block(title, items, show_reply=True):
        lines.append(f"\n## {title} ({len(items)})\n")
        for i, row, o in items[:max_per_bucket]:
            lines.append(f"### id {i} — gold `{row.intent}` / `{row.disposition}` → pred `{o.get('intent')}` ({o.get('confidence', 0):.2f}) / `{o.get('disposition')}`")
            lines.append(f"- message: {row.customer_message}")
            lines.append(f"- gold reason: {row.reason}")
            lines.append(f"- agent rationale: {o.get('rationale', '')}")
            lines.append(f"- triage: [{o.get('triage_source')}/{o.get('rule')}] {o.get('reason')}")
            if show_reply:
                lines.append(f"- reply: {o.get('reply', '')}")
                if o.get("stripped_urls"):
                    lines.append(f"- stripped hallucinated URLs: {o['stripped_urls']}")
            nb = o.get("neighbours", [])[:2]
            for n in nb:
                lines.append(f"- evidence {n['root_id']} ({'DM' if n['dm'] else 'public'}): \"{n['customer'][:120]}\" → \"{n['reply'][:120]}\"")
            lines.append("")
    block("Wrong intent", wrong)
    block("UNSAFE: should escalate, agent auto-handled", unsafe)
    block("Over-escalation: could auto-handle, agent escalated", over)
    if judge_tag:
        d = json.loads(Path(f"eval/results/judged_{tag}_by_{judge_tag}.json").read_text())
        low = [r for r in d["rows"] if r["judge"] and r["system"].startswith("grounded") and (r["judge"]["overall"] <= 2 or not r["judge"]["passed"])]
        low.sort(key=lambda r: r["judge"]["overall"])
        lines.append(f"\n## Grounded replies the judge failed ({len(low)})\n")
        for r in low[:max_per_bucket]:
            j = r["judge"]
            lines.append(f"### id {r['example_id']} — overall {j['overall']}, grounded {j['grounded']}, helpful {j['helpful']}, safe {j['safe']}")
            lines.append(f"- message: {r['message']}")
            lines.append(f"- reply: {r['reply']}")
            lines.append(f"- judge: {j['rationale']} | unsupported: {j['unsupported_claims']}")
            lines.append("")
    stripped = [(i, o) for i, o in a.items() if o.get("stripped_urls")]
    lines.append(f"\n## Hallucinated URLs stripped by the ground check ({len(stripped)})\n")
    for i, o in stripped[:max_per_bucket]:
        lines.append(f"- id {i}: {o['stripped_urls']} — reply: {o.get('reply','')[:160]}")
    out = out or Path(f"eval/results/failures_{tag}.md")
    out.write_text("\n".join(lines))
    rprint(f"[green]wrote {out}: {len(wrong)} wrong intent, {len(unsafe)} unsafe auto, {len(over)} over-escalated, {len(stripped)} stripped URLs[/green]")
