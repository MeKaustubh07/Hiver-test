"""Unit tests for the deterministic parts of the pipeline (no model calls)."""
import json
from pathlib import Path

import pandas as pd
import pytest

from support_agent.data import Thread, Turn, build_threads, clean_text
from support_agent.draft import GroundedDrafter, LinkCatalog, normalise_greeting
from support_agent.intents import EscalationRule, Intent, Taxonomy
from support_agent.llm import LLMClient, cache_key, extract_json, pending_requests
from support_agent.metrics import agreement, classification_metrics, triage_metrics
from support_agent.retrieval import Hit


# ---- data --------------------------------------------------------------------------------------------
def test_clean_text_strips_mentions_and_urls():
    assert clean_text("@SpotifyCares hey @115712 see https://t.co/x  now") == "hey see now"
    assert clean_text("@x see https://t.co/x", keep_urls=True) == "see https://t.co/x"


def test_build_threads_follows_brand_reply_and_skips_unanswered():
    df = pd.DataFrame([
        {"tweet_id": 1, "author_id": "c1", "inbound": True, "created_at": "a", "text": "@Brand broken", "response_tweet_id": "2,3", "in_response_to_tweet_id": None},
        {"tweet_id": 2, "author_id": "c9", "inbound": True, "created_at": "b", "text": "same here", "response_tweet_id": None, "in_response_to_tweet_id": 1.0},
        {"tweet_id": 3, "author_id": "Brand", "inbound": False, "created_at": "c", "text": "@c1 sorry, DM us", "response_tweet_id": "4", "in_response_to_tweet_id": 1.0},
        {"tweet_id": 4, "author_id": "c1", "inbound": True, "created_at": "d", "text": "done", "response_tweet_id": None, "in_response_to_tweet_id": 3.0},
        {"tweet_id": 5, "author_id": "c2", "inbound": True, "created_at": "e", "text": "@Brand nobody answers", "response_tweet_id": None, "in_response_to_tweet_id": None},
    ])
    threads = build_threads(df, "Brand")
    assert [t.root_id for t in threads] == [1]
    assert [t.tweet_id for t in threads[0].turns] == [1, 3, 4]  # brand reply preferred over the other customer
    assert threads[0].first_brand_reply == "@c1 sorry, DM us"
    assert "Agent: sorry, DM us" in threads[0].transcript()


def test_build_threads_customer_is_who_the_brand_replied_to():
    df = pd.DataFrame([
        {"tweet_id": 10, "author_id": "promo", "inbound": True, "created_at": "Sat Nov 18 18:21:18 +0000 2017", "text": "Premium is 99p for 3 months", "response_tweet_id": "11", "in_response_to_tweet_id": None},
        {"tweet_id": 11, "author_id": "cust", "inbound": True, "created_at": "Sat Dec 02 08:07:12 +0000 2017", "text": "@promo does this count for students?", "response_tweet_id": "12", "in_response_to_tweet_id": 10.0},
        {"tweet_id": 12, "author_id": "Brand", "inbound": False, "created_at": "Sat Dec 02 09:00:00 +0000 2017", "text": "@cust yes, verify via UNiDAYS", "response_tweet_id": None, "in_response_to_tweet_id": 11.0},
    ])
    t = build_threads(df, "Brand")[0]
    assert t.customer_message == "@promo does this count for students?"
    assert [x.tweet_id for x in t.context_before] == [10]
    assert t.first_brand_reply == "@cust yes, verify via UNiDAYS"


# ---- llm ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": [1, 2]}\n```', {"a": [1, 2]}),
    ('<think>reasoning</think>{"ok": true}', {"ok": True}),
    ('Options: ["x", "y"]. Answer: {"intent": "x"}', {"intent": "x"}),
    ('{"s": "brace } inside"} trailing', {"s": "brace } inside"}),
])
def test_extract_json(raw, expected):
    assert extract_json(raw) == expected


def test_cache_key_is_stable_and_model_sensitive():
    a = cache_key("m", "s", "p", 0.0, 10, True)
    assert a == cache_key("m", "s", "p", 0.0, 10, True)
    assert a != cache_key("m2", "s", "p", 0.0, 10, True)


def test_cache_only_raises_and_queue_records(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = LLMClient(backend="cache-only", model="x", cache_dir=tmp_path / "c")
    with pytest.raises(Exception):
        c.complete("hello")
    q = LLMClient(backend="queue", model="x", cache_dir=tmp_path / "c")
    assert q.complete("hello", tag="t") is None
    assert q.complete_json("hello", tag="t") is None
    assert q.stats["queued"] == 2
    assert len(pending_requests(Path("data/cache/queue/pending.jsonl"))) == 2  # different json_mode -> different keys


# ---- taxonomy / drafting -----------------------------------------------------------------------------
def _tax():
    return Taxonomy("B", [Intent("billing", "d", "i", "e", ["x"], "Canned billing reply", "escalate"),
                          Intent("other", "d", "i", "e", [], "Canned other", "auto")],
                    [EscalationRule("r", ["any"], "escalate", "t")], [])


def test_taxonomy_normalise():
    t = _tax()
    assert t.normalise("Billing") == "billing"
    assert t.normalise("billing_issue") == "billing"
    assert t.normalise("nonsense") == "other"
    assert t.normalise(None) == "other"


def test_ground_check_strips_unknown_urls(tmp_path):
    cat_path = tmp_path / "cat.json"
    cat_path.write_text(json.dumps({"citable_pages": [{"url": "https://support.spotify.com/article/a", "title": "A", "n_uses": 1, "example_contexts": []}],
                                    "tco_to_final": {"https://t.co/ok": "https://support.spotify.com/article/b"}}))
    cat = LinkCatalog(cat_path)
    d = GroundedDrafter(llm=None, taxonomy=_tax(), style_guide="", catalog=cat)
    hits = [Hit(1, 1.0, "c", "see https://t.co/ok.", "t", False)]
    allowed = d.allowed_urls(hits)
    reply, stripped = d.ground_check("Try https://support.spotify.com/article/a and https://t.co/ok, not https://evil.example/x.", allowed)
    assert "evil.example" not in reply
    assert stripped == ["https://evil.example/x."]
    assert "https://support.spotify.com/article/b" in reply  # t.co resolved to final
    # root-only and DM links are never citable, even when present in evidence
    cat.tco["https://t.co/root"] = "https://open.spotify.com"
    hits2 = [Hit(2, 1.0, "c", "vote here https://t.co/root and DM https://x.com/messages/compose?id=1", "t", False)]
    assert cat.annotate(hits2[0].first_reply) == "vote here [link to a page that no longer resolves] and DM [DM link]"
    r2, s2 = d.ground_check("Vote at https://open.spotify.com or DM https://x.com/messages/compose?id=1", d.allowed_urls(hits2))
    assert "open.spotify.com" not in r2 and "x.com" not in r2 and len(s2) == 2


def test_normalise_greeting():
    assert normalise_greeting("Hey Jon! Help's here.") == "Hey there! Help's here."
    assert normalise_greeting("Sorry to hear that!") == "Sorry to hear that!"


# ---- metrics -----------------------------------------------------------------------------------------
def test_classification_and_triage_metrics():
    m = classification_metrics(["a", "b", "a"], ["a", "a", "a"], ["a", "b"])
    assert m["accuracy"] == pytest.approx(2 / 3)
    assert m["per_class"]["b"]["recall"] == 0
    t = triage_metrics(["escalate", "escalate", "auto", "auto"], ["escalate", "auto", "auto", "escalate"])
    assert t["unsafe_auto_rate"] == 0.5 and t["escalate_recall"] == 0.5 and t["confusion"]["fp"] == 1


def test_agreement_perfect_and_random():
    h = [{"overall": i, "pass": i >= 4} for i in (1, 2, 3, 4, 5, 5, 2, 4)]
    a = agreement(h, h)
    assert a["overall_weighted_kappa"] == pytest.approx(1.0) and a["pass_agreement"] == 1.0
    j = [{"overall": 3, "pass": True} for _ in h]
    b = agreement(h, j)
    assert b["overall_weighted_kappa"] <= 0.05 and b["n"] == 8


# ---- triage layering --------------------------------------------------------------------------------
def test_layered_triage_rule_veto(tmp_path):
    from support_agent.triage import Decision, LayeredTriage, RulePolicy
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"low_confidence_threshold": 0.5, "rules": [
        {"name": "money", "hard": True, "disposition": "escalate", "pattern": r"\brefund\b", "reason": "money ({match})"},
        {"name": "intent_escalate", "hard": False, "disposition": "escalate", "intents": ["billing"], "reason": "intent {match}"},
    ]}))
    tax = _tax()

    class FakeLLM:
        def __init__(self, disp): self.disp = disp
        def decide(self, text, intent, confidence=1.0, hits=None):
            return Decision(self.disp, "llm says " + self.disp, "", "llm_triage")

    lt = LayeredTriage(RulePolicy(tax, rules, hard_only=True), FakeLLM("auto"), RulePolicy(tax, rules))
    assert lt.decide("I want a refund", "other").source == "hard_rule"             # hard rule wins
    assert lt.decide("hello", "other", 0.9).disposition == "auto"                   # LLM auto, no veto
    v = lt.decide("hello", "billing", 0.9)                                         # rules veto the LLM
    assert v.disposition == "escalate" and v.source == "rule_veto"
    assert lt.decide("hello", "other", 0.2).source == "rule_veto"                   # low confidence veto
    lt2 = LayeredTriage(RulePolicy(tax, rules, hard_only=True), FakeLLM("escalate"), RulePolicy(tax, rules))
    assert lt2.decide("hello", "other", 0.9).source == "llm_triage"
