# Decision log

Non-obvious decisions, in the order they were made. Each one: what, why, what it cost.

1. **Brand = SpotifyCares, chosen on measured evidence, not familiarity.** Profiled every brand on
   reply volume, share of first replies that are DM redirects, reply uniqueness and length
   (`scripts/profile_brands.py`). Spotify: 43k replies, 31% DM-redirects, 83% unique replies, and
   replies that carry actual resolutions (help-article links, troubleshooting steps). Apple/T-Mobile/
   Comcast redirect to DM 50-80% of the time (nothing to ground on); Amazon is multilingual and generic;
   airlines' replies are mostly apologies. Hulu was the runner-up (99% English, very substantive replies)
   but half the volume.
2. **Unit of work = the customer's first-contact tweet, with the thread kept as context.** The brand's
   *first* reply is what an AI agent would have to produce, so that is the grounding target and the
   drafting target. Mid-thread customer turns ("it's an iPhone 7 on iOS 11") are not classified.
3. **Thread linearisation prefers the brand's reply when a tweet forks.** TWCS threads are trees
   (other customers pile on). We follow root -> brand reply -> customer -> brand ... and drop side
   branches, so every thread reads as one conversation. Cost: ~5% of threads lose a sibling turn.
4. **English-only via py3langid; drop exact-duplicate customer texts.** 4.4% of Spotify threads are
   non-English; retweet storms create hundreds of identical messages that would dominate retrieval.
5. **Resolved every t.co link the brand ever posted (1,065 unique) to its final URL + page title.**
   t.co links are unique per tweet, so the raw text has no stable notion of "the Downloads article".
   After resolution: 37 citable support.spotify.com pages, 917 links to track/album pages. The agent may
   only cite URLs from this catalog or from retrieved evidence; anything else is stripped and counted
   as a grounding failure. Locale is `in-en` because resolution ran from India; the article content is
   locale-independent.
6. **Hybrid retrieval (BM25 + MiniLM embeddings, reciprocal-rank fusion) over customer messages,
   not over replies.** We want "customers who said something like this", then read how the brand
   answered them. Reply text is the payload, not the key.
7. **Golden-set threads are excluded from the retrieval index.** Otherwise an example would retrieve
   its own historical reply and the "grounded" drafter would just copy the answer key.
8. **Every model call is cached by content hash (model + system + prompt + params).** Reviewers can
   replay the exact recorded outputs offline (`LLM_BACKEND=cache-only`) in minutes; a live re-run with
   any backend regenerates them. The cache also makes the eval deterministic across runs.
9. **Three LLM backends, one interface: Anthropic API (primary), Ollama local model (offline
   fallback), and a batch queue.** No API key was available in the build environment. The local path
   was first tried with `qwen3:4b`, which on an 8 GB M2 took 50–260 s per call (the classify prompt is
   ~3.5k tokens) — 20+ hours for the golden set — so the default local model is `qwen3:1.7b`, used only
   for smoke tests and a small local-reproduction subset. The batch queue lets a stronger model answer
   the recorded prompts out-of-band and be ingested into the same cache; the headline numbers come from
   Claude Haiku 4.5 (agent) and Claude Sonnet 5 (judge) answered that way, and the README says so.
10. **Ollama calls use constrained JSON mode.** Qwen3 with thinking disabled still "thinks out
    loud" in plain text and runs out of tokens before answering; `format=json` fixes it. The Anthropic
    path uses an assistant prefill of `{` for the same effect.
11. **The JSON extractor prefers the last balanced object over the first bracket.** Small models echo
    the option list `["billing", ...]` before answering; the first parse attempt returned the list.
12. **Escalation ground truth is a policy decision, not a data artefact.** The brand moved 37.7% of
    first contacts to DM, and that signal is used as a *baseline* (k-NN over neighbours' `reply_asks_dm`)
    and as evidence in the triage prompt, but the golden labels follow a written policy so that
    "should escalate" is defined independently of what a busy 2017 agent happened to do.
13. **Three-annotator labelling with adjudication, then human review — instead of one labeller.**
    Each of the 260 candidates was labelled independently three times from the written guide and
    adjudicated against the guide (Fleiss' kappa 0.95 intent / 0.96 disposition before adjudication).
    Disagreements are listed first in `eval/golden/review_queue.csv` so human review time goes where the
    labels are actually uncertain. The kappa is between three runs of the same model family, so it
    measures guide clarity and label stability, not human-level agreement; the review pass is what makes
    the labels "hand-checked".
14. **Golden set is deliberately not corpus-distributed.** 150 random (month-stratified) + 70 keyword-
    targeted for rare intents + 40 hard cases. Per-class recall is measurable for every intent; the
    headline accuracy is not the accuracy on live traffic and the report says so.
15. **Escalation = "either layer can veto auto-handling".** After hard regex rules, the LLM decides; if it
    says auto but the rule policy on the *predicted* intent says escalate (or confidence is low), the case
    escalates with the rule's reason. On the golden set this cut the unsafe auto-handle rate from 0.16 to
    0.07 at the cost of escalating 46% instead of 41% of messages (gold rate 38%). The policy was chosen
    after seeing those numbers, so its reported unsafe rate is optimistically biased — flagged in the
    "misleading" section.
16. **Intent accuracy is scored against baselines trained by cross-validation on the golden set itself.**
    TF-IDF + logistic regression gets 5-fold out-of-fold predictions on the same 256 examples. That is the
    fair comparison for a "simple" baseline with no other labelled data, but 200 training examples
    understate what the same model would do with a few thousand silver labels.
17. **Links that no longer resolve are shown to the model as bracketed placeholders, not URLs.** 917 of
    the brand's 1,009 resolvable 2017 links now redirect to the bare web-player root, and the DM
    "compose" deep-link is account-specific. In the first full run the drafter copied
    `https://open.spotify.com` into 22 of 246 replies as if it were the Community idea or the singles
    link — a grounding failure the URL check could not catch because the URL genuinely appeared in the
    evidence. Evidence now renders such links as `[link to a page that no longer resolves]` / `[DM link]`
    and they are never citable. The pre-fix run is kept as `agent_claude-haiku-4-5-v1` for the
    before/after numbers in the failure analysis.
18. **Judge is a different, stronger model than the drafter, and sees the evidence, not the system
    name.** Claude Sonnet 5 judges replies drafted by Claude Haiku 4.5, with the same retrieved
    evidence pack the drafter could use plus the hard policies and the citable-page list. Same-model
    judging inflates scores (self-preference); a judge without the evidence cannot score groundedness
    and just rewards fluency. The nearest-reply baseline drops from 3.32 to 3.05 overall once the judge
    sees dead 2017 links as placeholders — the judge is doing what it should.
19. **Two judged runs are kept on purpose: v1 (before the unusable-link fix) and final.** Overall
    3.67 → 3.72, unsupported-claim rate 0.18 → 0.14 for the grounded drafter. The delta is small because
    the judge had already been penalising the copied root URLs; the fix mostly removed a class of
    failure rather than moving the mean.
20. **Model calls went through a batch queue answered by Claude Code subagents, and the README says so
    up front.** No API key was available. The pipeline's prompts, parsing and post-processing are
    identical to the API path; only the transport differs, and every cache record is labelled
    `claude-code-batch`. A reviewer can replay everything offline or re-run live with a key.
21. **Human ratings are the user's job, not mine.** The rating sheet is blind and shuffled with the
    key file separate, and the agreement command refuses to run on an empty sheet. Filling it with
    model-generated "human" ratings would make the judge-validation number meaningless.
22. **The customer is whoever the brand replied to, not the thread root.** An external review of the
    golden labels found three candidates whose "customer message" was a promotional tweet from another
    Spotify account (the marketing and Spotify-for-Artists handles, anonymised as numeric ids); the real
    customer had replied to the promo and the brand answered *them*. 491 threads (1.7%) have that shape.
    Reconstruction now takes the consecutive turns by the author the brand first replied to as the message
    and keeps earlier turns as context. Sibling conversations under the same promo (other customers who
    replied to it) are still dropped — one thread per root. The corpus, embeddings, the three candidates'
    text and labels, and every downstream number were regenerated after the fix.
23. **The golden set was reviewed once more by an AI pass, and that is stated, not hidden.** A separate
    review went through all 107 queued rows and changed 10 labels (5 intents, 7 dispositions), each
    with a written rationale in `ai_review_reason`; `reviewed_by_human` stays blank. The same pass rated
    the 60 blind replies with the judge's rubric, giving a *judge-vs-AI-reviewer* agreement (weighted
    kappa 0.58, pass kappa 0.40) that is reported under that name and is not evidence of human agreement.
    The `agreement` command refuses to treat AI-tagged ratings as human ones.
