"""One-off: dump random corpus slices + reply patterns as text for taxonomy induction."""
import re, sys, json
from pathlib import Path
import pandas as pd
sys.path.insert(0, "src")
from support_agent.corpus import load_corpus

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
c = load_corpus("SpotifyCares")
rng = c.sample(frac=1.0, random_state=42).reset_index(drop=True)
N, K = 150, 6
for i in range(K):
    sl = rng.iloc[i*N:(i+1)*N]
    lines = []
    for r in sl.itertuples():
        lines.append(f"### thread {r.root_id} ({r.created_at.date()})\n{r.transcript[:900]}\n")
    (out / f"slice_{i}.txt").write_text("\n".join(lines))
# reply patterns: normalise greeting names and count
norm = (c.first_reply_clean.str.lower()
        .str.replace(r"^(hey|hi|hello|heya|howdy)[^!,.:]*[!,.:]\s*", "", regex=True)
        .str.replace(r"https?://\S+", "<url>", regex=True)
        .str.replace(r"\s+", " ", regex=True).str.strip())
top = norm.value_counts().head(150)
lines = [f"# Top repeated first replies (after removing names/URLs); corpus={len(c)} threads; {c.reply_asks_dm.mean():.1%} of first replies ask for a DM\n"]
for txt, n in top.items():
    lines.append(f"[{n:>4}] {txt}")
# phrases around URLs: what articles do they link to?
ctx = c.first_reply_clean[c.first_reply_clean.str.contains("http")].str.lower()
ctx = ctx.str.extract(r"(.{0,90})https?://\S+")[0].dropna().str.strip()
lines.append("\n\n# Most common text immediately preceding a link (what the link is for)\n")
for txt, n in ctx.value_counts().head(80).items():
    lines.append(f"[{n:>4}] ...{txt}")
lines.append("\n\n# 60 random full first replies (raw, with sign-offs)\n")
for r in c.sample(60, random_state=1).itertuples():
    lines.append(f"- {r.first_reply}")
(out / "reply_patterns.txt").write_text("\n".join(lines))
print("wrote", K, "slices and reply_patterns.txt;", "top reply covers", top.iloc[0], "threads")
