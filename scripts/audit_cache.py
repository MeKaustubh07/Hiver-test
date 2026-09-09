"""Audit the LLM cache for batch-harness contamination: answers reused across different prompts,
stock rationales, boilerplate replies. Writes eval/results/cache_audit.json and prints a summary."""
import json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

CACHE = Path("data/cache/llm")
BOILER = re.compile(r"(thanks for (the|your) feedback!?$|what can we help you with|help us with more details|glad you'?re loving it|^hey there, we hear you|keep us posted\.$)", re.I)
recs = []
for p in CACHE.glob("*.json"):
    d = json.loads(p.read_text()); d["_key"] = p.stem; recs.append(d)
by_tag = defaultdict(list)
for d in recs:
    by_tag[(d.get("tag", ""), d["model"])].append(d)

def msg_of(prompt):
    m = re.search(r'CUSTOMER MESSAGE:\n"""(.*?)"""', prompt, re.S)
    return m.group(1).strip() if m else prompt[-300:]

report = {}
for (tag, model), rs in sorted(by_tag.items()):
    texts = defaultdict(set)   # normalised answer text -> set of distinct customer messages
    for d in rs:
        texts[d["text"].strip()].add(msg_of(d["prompt"]))
    shared = {t: ms for t, ms in texts.items() if len(ms) > 1}
    n_shared_records = sum(1 for d in rs if len(texts[d["text"].strip()]) > 1)
    stock_rat = 0; boiler = 0
    rats = Counter()
    for d in rs:
        try: j = json.loads(re.sub(r"^[^{]*", "", d["text"], count=1))
        except Exception: j = {}
        r = str(j.get("rationale") or j.get("reason") or "")
        if r: rats[r] += 1
        rep = str(j.get("reply", ""))
        if rep and BOILER.search(rep) and len(rep) < 120: boiler += 1
    stock = {r: n for r, n in rats.items() if n >= 3}
    report[f"{tag}|{model}"] = {"records": len(rs), "answers_shared_across_different_messages": n_shared_records,
                                "distinct_shared_texts": len(shared), "stock_rationales(>=3 uses)": stock, "boilerplate_replies": boiler,
                                "sample_shared": [(t[:90], len(ms)) for t, ms in sorted(shared.items(), key=lambda kv: -len(kv[1]))[:5]]}
    print(f"\n[{tag} | {model}] records={len(rs)} shared-across-messages={n_shared_records} distinct-shared-texts={len(shared)} boilerplate={boiler} stock-rationales={sum(stock.values())}")
    for t, n in report[f"{tag}|{model}"]["sample_shared"]: print(f"   {n:3d}x {t!r}")
Path("eval/results").mkdir(exist_ok=True)
json.dump(report, open("eval/results/cache_audit.json", "w"), indent=1, ensure_ascii=False)
