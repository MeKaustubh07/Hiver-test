# Golden evaluation set

`golden_set.csv` — hand-checked labels for first-contact customer messages to @SpotifyCares.
Columns: `id` (root tweet id), `stratum` (how it was sampled), `customer_message` (the customer's
"burst": their consecutive tweets before the brand replied, mentions/URLs stripped), `thread_context`
(what actually happened next, for the annotator only), `intent`, `disposition` (`auto` | `escalate`),
`reason`, `notes`, plus the three independent annotator labels and the adjudication note.

## How it was sampled (`support-agent sample-golden --seed 42`)

From the 26,623 English first-contact threads (see `src/support_agent/golden.py`):

| stratum | n | why |
|---|---|---|
| `random` | 150 | uniform, stratified by month so one incident day cannot dominate |
| `targeted:*` | 70 | keyword oversampling (10 each) for intents that are rare in a uniform sample: account_security, artist_and_creator, support_followup, how_to, account_access, praise/thanks, billing — so every class has enough support to measure recall |
| `hard` | 40 | very short (<40 chars), multi-question, angry, or media/link-only messages — where classifiers and drafters usually break |

260 candidates were drawn; every candidate id is excluded from the retrieval index (labelled or not)
so no example can retrieve its own historical reply. Candidates marked unlabelable (spam, not English,
no discernible ask) are kept in the file with `unlabelable=true` and excluded from scoring.

Because `targeted` and `hard` are oversampled, the golden label distribution is **not** the corpus
distribution. Per-class metrics are therefore more reliable than the headline accuracy, and the
headline accuracy should not be read as the accuracy on live traffic (see the report).

## How it was labelled

1. The intent taxonomy and escalation policy were induced from the data first (`config/intents_full.json`,
   `annotator_guide.md`): four readers each proposed a taxonomy from a disjoint 150-thread slice, one
   pass merged them, three annotators stress-tested the merged guide on fresh slices (intent agreement
   0.97, disposition agreement 0.96 on the same 150 threads), and the guide was revised.
2. Each candidate was then labelled by **three independent annotators** working from the guide, from
   the customer message only (thread context used only to disambiguate meaning, never to copy the
   brand's historical choice). An adjudicator resolved disagreements against the guide and recorded
   why in `adjudication_note`.
3. Annotation was LLM-assisted (Claude agents acting as independent annotators). A second, separate AI
   review pass then re-examined all 107 rows in `review_queue.csv` (every disagreement or hard flag) and
   changed 10 labels — 5 intents, 7 dispositions — recording its reasoning in `ai_review_reason` and
   `ai_review_flags` (`reviewed_by_ai=true`). Three further rows were re-labelled after a thread-
   reconstruction fix (their original "message" was a promotional tweet the customer had replied to).
   `reviewed_by_human` is still blank: a person has not yet checked these labels, and the numbers in the
   report should be read with that in mind. Disagreements are listed first in `review_queue.csv` so the
   human review time goes where it matters.

Inter-annotator agreement before adjudication (`agreement.json`, n=260):

| | unanimous (3/3) | pairwise agreement | Fleiss' kappa |
|---|---|---|---|
| intent (10 classes) | 93.8% | 95.6% | 0.95 |
| disposition (auto/escalate) | 97.3% | 98.2% | 0.96 |

The adjudicator overrode a 2-of-3 majority 2 times (each with a note citing the guide rule).
4 rows are marked unlabelable and excluded from scoring. Caveat: the three annotators are
three runs of the same model family reading the same guide, so this kappa measures guide clarity and
label stability, not human-level agreement; the human review pass is what makes the labels "hand-checked".

Final label distribution (scoreable rows): feature_request_and_feedback 44, content_availability 42, billing_and_subscription 40, app_or_playback_issue 39, account_access_and_settings 21, account_security 20, how_to_and_product_question 18, other 12, support_followup_and_channel_request 11, artist_and_creator 9;
disposition: auto 159, escalate 97.

## What the labels mean

- `intent`: exactly one of the 10 intents in `config/intents.json`. Multi-intent messages take the
  primary actionable ask by the precedence order in the guide.
- `disposition`: `escalate` if the written policy says a human must take over (account lookup,
  money, security beyond self-serve, safety/legal/PII, chasing support, or an unclear message);
  `auto` if a public self-serve reply is sufficient. This is a **policy** label, not "what the brand
  did in 2017": the brand moved 37.7% of first contacts to DM, sometimes inconsistently.
