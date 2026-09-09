# SpotifyCares AI support agent — evaluation report

*Hiver SDE intern take-home. Dataset: Kaggle "Customer Support on Twitter" (thoughtvector), brand @SpotifyCares.*

## 1. Problem framing

**What the agent does.** For each customer's first public tweet to @SpotifyCares it (1) classifies the
message into one of 10 intents induced from the data, (2) drafts a reply grounded in how SpotifyCares
actually resolved the most similar past cases, and (3) decides whether the reply can go out
automatically or a human must take over, with a reason a supervisor would accept.

**Why SpotifyCares.** Chosen on measured evidence, not familiarity (`scripts/profile_brands.py`): 43k brand
replies, 96% English, only 31% of first replies are "DM us" redirects (Apple 53%, T-Mobile 82%,
Comcast 72%), and the replies carry real resolutions: help-article links, a troubleshooting ladder,
consistent policies. Airlines' replies are mostly apologies; Amazon is multilingual and generic. A brand
whose replies contain nothing but "DM us" gives an agent nothing to ground on.

**What "good" means for this brand.** In order of importance:

1. **Never auto-handle what needs a human.** On Twitter, "escalate" means a human moves the case to DM
   to look up the account. Billing, refunds, login failures on all devices, hacked accounts beyond the
   self-serve page, safety/legal/PII, and chasing an existing ticket must never get a public bot reply
   that pretends to resolve them. The metric that matters is the *unsafe auto-handle rate*: the share of
   should-escalate messages the agent auto-handled. Over-escalation is cheap; under-escalation is a
   complaint or a churned customer.
2. **Say only what the brand would say.** Replies must be grounded: no invented URLs, features, dates,
   refunds or account facts. SpotifyCares never promises content arrival dates, never discusses refunds
   publicly, never suggests phone support (there is none). A fluent reply that promises a refund is worse
   than a canned one.
3. **Sound like SpotifyCares.** Warm, brief, "we'll take a look backstage", one link named in the sentence,
   under 280 characters. Tone is third because a stiff-but-correct reply is fixable; a wrong one is not.
4. **Get the intent right** — but intent accuracy is instrumental: it matters only insofar as it changes the
   reply or the disposition. Two intents that lead to the same action were merged for that reason.

**What I chose not to build.**

- *Multi-turn conversation.* The agent handles the first contact only. Mid-thread turns ("it's an iPhone 7
  on iOS 11") are context for retrieval, not classification targets. The troubleshooting ladder the brand
  uses is inherently multi-turn; the agent produces rung 1 and hands off.
- *Live incident awareness.* The brand's outage wording ("little hiccup, all fixed now") is only correct
  when an incident is confirmed. Without a status feed the agent treats outage-shaped reports as
  individual problems and never declares anything fixed.
- *Named-customer greetings and agent sign-offs.* The corpus greets by first name and signs "/XX". Names
  are anonymised in the dataset and a bot should not pose as a named agent, so replies use "Hey there!"
  and no sign-off.
- *Fine-tuning.* 200 labels are far too few, and the point of the exercise is the evaluation, not the model.
- *Banking77.* It is a different domain (banking intents); using it for intent induction would have
  imported a taxonomy the data does not support.
- *A DM-side agent.* Everything after "DM us your email" is invisible in the dataset.

## 2. System

```
customer tweet ──▶ retrieve k=5 similar past cases (BM25 + MiniLM, RRF) ──▶ classify (LLM + taxonomy + neighbours)
                                                                        ──▶ triage (hard regex rules ▸ LLM over policy ▸ intent default)
                                                                        ──▶ draft (LLM grounded on style guide, policies, evidence, citable pages)
                                                                        ──▶ URL check: any link not in evidence/catalog is stripped and logged
```

- **Taxonomy** (`config/intents.json`): 10 intents including `other`, induced from six 150-thread slices
  by independent readers, merged, stress-tested by three annotators on fresh slices (0.97 intent
  agreement), then revised. Every intent is defined by what it changes downstream.
- **Grounding corpus**: 26,623 English first-contact threads. Every t.co link the brand ever posted was
  resolved to its real target, yielding 37 citable support.spotify.com pages with titles. The drafter may
  cite only those or links present in the retrieved evidence.
- **Escalation policy**: 17 ordered rules written from the data (`config/intents.json`) plus regex hard
  rules for PII, safety, legal, GDPR, breach, money-moved and chasing (`config/triage_rules.json`) that
  the LLM cannot override.
- **Backends**: Anthropic API (primary), Ollama (offline), batch queue; every call cached by content hash
  so reported numbers replay offline.


## 3. How it was evaluated

**Golden set** (`eval/golden/`): 260 first-contact messages, 256 scoreable. Sampled as 150 uniform
(month-stratified) + 70 keyword-targeted for rare intents + 40 hard cases (very short, multi-question,
angry, media-only). Every candidate id is excluded from the retrieval index. Each message was labelled
independently three times from a written annotator guide, adjudicated against the guide, and the
disagreements queued for human review (`review_queue.csv`). Agreement before adjudication: Fleiss'
kappa 0.95 (intent), 0.96 (disposition). The disposition label follows the written policy, not what the
2017 agent happened to do.

**Baselines.** For every task, one trivial and at least one simple baseline:

| task | trivial | simple | data-driven |
|---|---|---|---|
| intent | majority class | hand-written keyword rules; TF-IDF + logistic regression (5-fold out-of-fold on the golden set) | — |
| triage | always escalate | rule policy on the gold intent; rule policy on the predicted intent | k-NN vote on whether the brand moved the 5 nearest past cases to DM |
| reply | canned template per intent | copy the brand's reply to the nearest past message | — |

**Metrics.** Accuracy and macro-F1 with percentile-bootstrap 95% CIs over examples; for triage, precision/
recall on `escalate` plus the **unsafe auto-handle rate** (share of should-escalate cases the system
auto-handled). Replies are scored by an LLM judge (Claude Sonnet 5, a different and stronger model than
the Haiku drafter) on a 1-5 rubric — grounded, helpful, tone, safe, overall — plus a binary
"would a supervisor let this go out unedited" pass. The judge sees the same evidence pack the drafter
could use and never sees which system wrote the reply. Judge validity is measured against blind human
ratings on the same rubric (Section 4.4).

## 4. Results

All numbers below are generated from `eval/results/*.json` by `scripts/render_results.py`
(`docs/RESULTS.md` has the full tables). Model calls: Claude Haiku 4.5 for the agent, Claude Sonnet 5
as judge, answered through the batch harness described in the README; `qwen3:1.7b` on Ollama as a
fully-local comparison.

### 4.1 Intent classification (n = 256, 10 classes)

| system | accuracy [95% CI] | macro-F1 [95% CI] |
|---|---|---|
| majority class | 0.172 [0.13, 0.22] | 0.029 |
| keyword rules | 0.445 [0.39, 0.51] | 0.464 |
| TF-IDF + LR (5-fold CV) | 0.539 [0.48, 0.60] | 0.489 |
| **LLM + taxonomy + 4 retrieved neighbours** | **0.789 [0.74, 0.84]** | **0.758 [0.69, 0.81]** |

Per class, the LLM is strong where the action is distinctive (billing 0.88 F1, security 0.89, content
0.86, feature 0.82, app 0.81) and weak on the two "residual" classes: `how_to_and_product_question`
(F1 0.56, recall 0.50) and `other` (F1 0.43, precision 0.32). The confusion matrix shows why: the model
sends unclear how-to, content and app messages to `other` (12 of 54 errors), and confuses how-to with
billing in both directions (6 errors) — the "policy question vs. failure with a charge" seam that the
annotators themselves needed a tie-break rule for.

### 4.2 Auto-handle vs escalate (n = 256, gold escalation rate 0.38)

| system | accuracy | escalate recall | **unsafe auto-handle** | escalation rate |
|---|---|---|---|---|
| always escalate | 0.379 | 1.00 | **0.00** | 1.00 |
| rule policy on *gold* intent | 0.906 | 0.80 | 0.20 | 0.32 |
| k-NN on historical DM (k=5) | 0.816 | 0.82 | 0.18 | 0.43 |
| rule policy on *predicted* intent | 0.848 | 0.72 | 0.28 | 0.32 |
| LLM triage alone (ablation) | 0.840 | 0.84 | 0.16 | 0.41 |
| **agent: hard rules ▸ LLM ▸ rule veto** | **0.867** | **0.93** | **0.07** | 0.46 |

Three things stand out. (1) The intent carries most of the signal: rules on the gold intent reach 0.906
accuracy, so the classifier's errors are the triage's errors. (2) The LLM layer earns its place by
recovering from those errors: with predicted intents, rules alone auto-handle 28% of cases that needed
a human; the LLM alone 16%. (3) Letting either layer veto auto-handling brings that to 7% for a 5-point
rise in escalations. The veto was adopted after seeing these numbers; on 200 random held-out halves the
reduction holds (mean 0.095, 5th-95th percentile 0.044-0.146), so it is not a fit to noise, but the
0.07 is still an in-sample figure. On the random stratum alone — the closest to live traffic — the unsafe
rate is 0.05 (2 of 44 should-escalate cases); on the 40 hard cases it is 0.25 (2 of 8).

Where the 7 unsafe cases come from (all listed in `eval/results/failures_claude-haiku-4-5.md`): a repeat
account hijack where the customer had already reset the password (the "steps tried" regex requires
"my/the" before "password" and the LLM read it as a first contact); "why are all my songs being taken
off??" (account-scoped loss vs. device bug — ambiguous even for annotators); a promo-charged-at-full-price
message classified as feedback; an artist chasing a week-old submission; and a Hulu-bundle eligibility
question whose gold label follows a tie-break that sends any partner-bundle mention to billing.

Of the 27 over-escalations, 23 come from the LLM layer invoking "account-scoped" or "steps tried" rules
liberally on app issues and policy questions, 3 from the veto (predicted billing on a how-to question),
and 1 from a hard rule: "Still waiting for Reputation to drop 😭" matched the *chasing support* regex.

### 4.3 Reply quality

_(see Section 4.3 in the final version — filled from `judged_*.json` once the judge passes complete)_

### 4.4 Does the judge agree with a human?

_(filled from `agreement_*.json` once the blind rating sheet is completed)_

## 7. What I would do with one more week

1. **Fix the two label seams instead of the model.** Merge `other`'s "vague plea" bucket with a real
   *clarify* action, and split how-to from billing by a single question ("has money moved or an attempt
   failed?") in the prompt — the confusion matrix says these two seams cost more accuracy than any model
   change would.
2. **Silver-label 3,000 messages** with the LLM classifier, train the TF-IDF/LR (or a small encoder) on
   them, and use it as a cheap first pass with the LLM only on low-margin cases. The 5-fold baseline here
   is starved of data (200 examples); the interesting question is whether a $0 model gets to 0.75.
3. **Human ratings at scale.** 60 blind ratings bound the judge; 300 with two raters would let me report
   inter-human agreement next to judge-human agreement, which is the number that actually validates the
   rubric.
4. **Evaluate the conversation, not the first turn.** The brand's playbook is a troubleshooting ladder;
   a simulated-customer harness (the thread's later customer turns as the script) would measure whether
   the agent reaches resolution in ≤3 turns and escalates at the right rung.
5. **Retire time-bound facts.** Build the "registry" the policy assumes (current help-article URLs, live
   incident feed, active promos) so replies stop depending on what was true in November 2017, and add a
   check that flags any reply citing a date-bound template (Reputation, NUS, 10k library limit).
6. **Regex hygiene from the failure dump**: "still waiting for <album>" is not a support chase;
   "changed password" without "my" is a steps-tried signal.
