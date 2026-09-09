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

_(Sections 3–7 — results, baselines, failure analysis, "what is misleading", next week — are written
from `eval/results/*.json` once the evaluation runs complete.)_
