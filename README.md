# SpotifyCares AI support agent — Hiver SDE intern take-home

An AI support agent for **@SpotifyCares**, built from the Kaggle *Customer Support on Twitter* dataset,
that classifies each incoming customer tweet into one of 10 data-derived intents, drafts a reply grounded
in how the brand historically resolved similar cases, and decides whether to auto-handle or escalate to a
human, with a stated reason. The larger part of the work is the evaluation: a 3-annotator golden set, an
LLM judge with measured human agreement, two baselines per task, and a failure analysis.

**Report:** [`docs/REPORT.md`](docs/REPORT.md) · **Decision log:** [`docs/DECISION_LOG.md`](docs/DECISION_LOG.md) ·
**Golden set:** [`eval/golden/`](eval/golden/README.md)

## Headline results

Golden set of 256 scoreable first-contact messages (3 annotators, Fleiss' kappa 0.95). Agent = Claude
Haiku 4.5 for classify / triage / draft; judge = Claude Sonnet 5. Full tables with confidence intervals
in [`docs/RESULTS.md`](docs/RESULTS.md); what these numbers do *not* mean in the report's Section 6.

| task | trivial baseline | simple baseline | **agent** |
|---|---|---|---|
| intent accuracy (10 classes) | majority 0.17 | TF-IDF+LR 0.54 · keyword rules 0.45 | **0.79** [0.74, 0.84] · macro-F1 0.76 |
| **unsafe auto-handle rate** (should-escalate cases the system auto-handled; lower is better) | always-escalate 0.00 (escalates everything) | rules on predicted intent 0.28 · k-NN on historical DM 0.18 | **0.07** (escalate recall 0.93, escalation rate 0.46 vs gold 0.38) |
| reply quality, judge overall 1-5 / pass rate | canned template 3.26 / 0.53 | copy nearest historical reply 3.05 / 0.40 | **3.72 / 0.67** (unsupported-claim rate 0.14) |
| fully local `qwen3:1.7b` | | | intent 0.53 · unsafe auto 0.09 · escalation rate 0.54 |

Judge-vs-human agreement: the blind 60-reply rating sheet is in `eval/human/`; the agreement table in
the report is filled from it (`support-agent agreement`).

## Reproduce in under 15 minutes

Requirements: Python 3.12+, [`uv`](https://docs.astral.sh/uv/), ~1 GB disk. No Kaggle account needed
(the dataset downloads anonymously) and **no API key needed** for the replay path.

```bash
make setup        # uv sync + download twcs.csv (~170 MB zip)
make data         # reconstruct SpotifyCares threads (~2 min)
make reproduce    # replay the recorded model outputs -> all metrics printed + eval/results/*.json
```

`make reproduce` uses `LLM_BACKEND=cache-only`: every model call in the pipeline is cached by a content
hash of (model, system prompt, prompt, params) under `data/cache/llm/`, and the cache is committed. The
numbers you get are exactly the numbers in the report, and you can read every prompt and every raw
model answer in the cache.

To re-run live:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or: export LLM_BACKEND=ollama  (local, small model)
rm -rf data/cache/llm                  # optional: force fresh calls instead of cache hits
make live
```

Run a single message end-to-end and see every intermediate (retrieval hits, intent, triage rule, reply):

```bash
uv run support-agent demo "my downloaded songs keep disappearing on android, any ideas?"
```

## How the model calls were produced (read this)

No Anthropic API key was available on the machine this was built on. The pipeline therefore has a
**batch queue backend**: it records every prompt it would have sent, the prompts were answered
out-of-band by Claude models (Haiku 4.5 for the agent's classify / triage / draft steps, Sonnet 5 as the
judge) through Claude Code subagents, and the answers were ingested into the same cache the API backend
writes to. Cache records carry `backend: "claude-code-batch"` so this is visible per call. The prompts,
parsing and post-processing are identical to the API path; only the transport differs. A small local model
(`qwen3:1.7b` via Ollama) was also run over the whole golden set as a fully-offline comparison row.

## Repository layout

```
src/support_agent/
  data.py        load twcs.csv, reconstruct threads (root -> brand reply chain, customer "burst")
  corpus.py      per-brand English first-contact corpus with the brand's first reply
  retrieval.py   BM25 + MiniLM hybrid retrieval over past customer messages (RRF)
  intents.py     taxonomy + escalation policy loader (config/intents.json)
  classify.py    majority / keyword-rule / TF-IDF+LR baselines and the LLM classifier
  triage.py      hard regex rules -> LLM triage -> intent defaults; always-escalate and k-NN-DM baselines
  draft.py       template / nearest-reply baselines and the grounded LLM drafter with URL check
  judge.py       LLM-as-judge rubric + blind human rating sheet
  metrics.py     accuracy / macro-F1 with bootstrap CIs, triage metrics, judge summaries, kappa
  evaluate.py    evaluation runs over the golden set
  llm.py         Anthropic / Ollama / queue / cache-only backends behind one interface
  cli.py         `support-agent` commands
config/          intents.json (taxonomy + policy), style_guide.md, policies.md, triage_rules.json,
                 keyword_rules.json, link_catalog.json (resolved t.co links), golden_strata.json
eval/golden/     golden_set.csv, annotator_guide.md, review_queue.csv, agreement.json, README.md
eval/human/      blind human rating sheet + key
eval/results/    every metric as JSON, failure dumps
scripts/         one-off provenance scripts (brand profiling, link resolution, batch queue tooling)
tests/           unit tests for the deterministic parts (`make test`)
```

## Borrowed / cited

- Dataset: Stuart Axelbrooke, *Customer Support on Twitter*, Kaggle (thoughtvector/customer-support-on-twitter).
- Retrieval: `rank-bm25` (Okapi BM25) and `sentence-transformers/all-MiniLM-L6-v2`; reciprocal rank fusion
  after Cormack, Clarke & Buettcher (2009).
- Agreement statistics: Cohen's quadratic-weighted kappa (scikit-learn), Fleiss' kappa (own implementation
  of the standard formula), Spearman (SciPy). Kappa interpretation bands after Landis & Koch (1977).
- LLM-as-judge design follows the common absolute-rubric pattern (e.g. Zheng et al., "Judging LLM-as-a-Judge",
  2023) with a separate, stronger judge model than the drafter to reduce self-preference.
- Everything else (thread reconstruction, taxonomy, policy, prompts, harness) was written for this
  assignment, with AI coding assistance.
