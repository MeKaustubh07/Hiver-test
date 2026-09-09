"""One-off: compare candidate brands on thread structure, language, and reply substance."""
import sys, random
sys.path.insert(0, "src")
import pandas as pd, py3langid as langid
from support_agent.data import load_twcs, brand_subset, build_threads, clean_text

df = load_twcs()
random.seed(7)
for brand in sys.argv[1:] or ["SpotifyCares", "hulu_support", "Delta"]:
    sub = brand_subset(df, brand)
    threads = build_threads(sub, brand)
    langs = [langid.classify(clean_text(t.customer_message))[0] for t in threads]
    en = sum(1 for l in langs if l == "en")
    lens = pd.Series([len(t.turns) for t in threads])
    print(f"\n===== {brand}: subset={len(sub)} threads={len(threads)} en={en/len(threads):.2%} "
          f"turns: mean={lens.mean():.1f} p50={lens.median():.0f} p90={lens.quantile(.9):.0f}")
    en_threads = [t for t, l in zip(threads, langs) if l == "en"]
    for t in random.sample(en_threads, 10):
        print("-" * 100)
        print(t.transcript(max_turns=4)[:600])
