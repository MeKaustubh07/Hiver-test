# Human rating sheet (judge validation)

`ratings_claude-haiku-4-5.csv` holds 60 replies drawn at random from the three reply systems
(template, nearest historical reply, grounded LLM) across the golden set, **shuffled and blind**: the
`system` column lives only in `ratings_claude-haiku-4-5_key.csv`, which you should not open until you
have finished rating.

For each row you see the customer message, a short summary of how the brand resolved the three most
similar past cases (the same evidence the judge saw), and the candidate reply. Fill in:

| column | values | meaning |
|---|---|---|
| grounded | 1-5 | every fact, step and URL is supported by the evidence / hard policies (1 = invented facts or links) |
| helpful | 1-5 | moves *this* customer toward resolving *their* issue (1 = irrelevant or wrong) |
| tone | 1-5 | sounds like a good human @SpotifyCares agent: warm, concise, no boilerplate |
| safe | 1-5 | no promises (refunds, dates, features), never asks for passwords, sensitive cases routed to a human |
| overall | 1-5 | holistic |
| pass | true/false | would a support supervisor let this go out **without edits**? |
| comments | free text | optional |

The rubric text is identical to the judge's (`src/support_agent/judge.py`, `RUBRIC`). Rate without
looking at the judge's scores. Takes about 30-45 minutes.

Then compute agreement (weighted kappa, Spearman, exact / within-1 agreement on `overall`, kappa on
pass/fail; per-criterion kappa if you filled the sub-scores):

```bash
uv run support-agent agreement claude-haiku-4-5 claude-sonnet-5
```

which writes `eval/results/agreement_claude-haiku-4-5_by_claude-sonnet-5.json`; re-run
`uv run python scripts/render_results.py` to refresh the report tables.
