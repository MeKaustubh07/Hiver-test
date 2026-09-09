"""Split pending LLM prompts (queue backend) into batch files for out-of-band answering, and ingest answers.

  uv run python scripts/queue_batches.py split <out_dir> [batch_size]   -> writes batch_i.jsonl + batches.json
  uv run python scripts/queue_batches.py ingest <answers_dir> <backend_label>
"""
import json, re, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, "src")
from support_agent.llm import ingest_answers, pending_requests, requests_from_batches

BOILER = re.compile(r"(thanks for (the|your) feedback!?$|what can we help you with|help us with more details|glad you'?re loving it|^hey there, we hear you|keep us posted\.$)", re.I)


def customer_message(prompt: str) -> str:
    m = re.search(r'CUSTOMER MESSAGE:\n"""(.*?)"""', prompt, re.S)
    return m.group(1).strip() if m else ""


def words(s: str, n: int = 8) -> list[str]:
    return re.findall(r"[a-z0-9']+", s.lower())[:n]


def guard(answers_path: Path, reqs: dict[str, dict]) -> tuple[Path, dict]:
    """Drop answers that are not demonstrably specific to their own prompt: msg_check must repeat the first
    words of that prompt's customer message; identical texts across different messages in the same batch
    are rejected (except DM/diagnostic templates in drafts); boilerplate replies are rejected.
    Writes <name>.guarded.jsonl and returns it plus counts."""
    rows = []
    for line in answers_path.read_text().splitlines():
        if line.strip():
            try: rows.append(json.loads(line))
            except json.JSONDecodeError: pass
    kept, stats = [], Counter()
    seen_text: dict[str, str] = {}
    for a in rows:
        req = reqs.get(a.get("key"))
        if not req or not a.get("text"):
            stats["no_request_or_empty"] += 1; continue
        msg = customer_message(req["prompt"])
        exp, got = words(msg), words(str(a.get("msg_check", "")))
        if msg and (len(got) < min(4, len(exp)) or sum(1 for x, y in zip(exp, got) if x == y) < min(6, len(exp))):
            stats["msg_check_mismatch"] += 1; continue
        t = a["text"].strip()
        tag = req.get("tag", "")
        if tag in ("classify", "triage", "judge") and t in seen_text and seen_text[t] != msg:
            stats["duplicate_answer_other_message"] += 1; continue
        if tag == "draft":
            try: reply = json.loads(re.sub(r"^[^{]*", "", t, count=1)).get("reply", "")
            except Exception: reply = ""
            if BOILER.search(reply) and len(reply) < 120:
                stats["boilerplate_reply"] += 1; continue
            if t in seen_text and seen_text[t] != msg and not re.search(r"\bDM\b|device, operating system", reply):
                stats["duplicate_draft_other_message"] += 1; continue
        seen_text[t] = msg
        kept.append(a); stats["kept"] += 1
    out = answers_path.with_suffix(".guarded.jsonl")
    out.write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in kept))
    return out, dict(stats)

cmd = sys.argv[1]
if cmd == "split":
    out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    pend = pending_requests()
    batches = []
    for i in range(0, len(pend), size):
        p = out / f"batch_{i // size}.jsonl"
        with open(p, "w") as f:
            for r in pend[i:i + size]:
                f.write(json.dumps({k: r[k] for k in ("key", "system", "prompt", "json_mode", "max_tokens")}, ensure_ascii=False) + "\n")
        tags = sorted({r.get("tag", "") for r in pend[i:i + size]})
        batches.append({"path": str(p), "n": min(size, len(pend) - i), "tag": "+".join(tags)})
    json.dump(batches, open(out / "batches.json", "w"))
    print(json.dumps({"pending": len(pend), "batches": len(batches)}))
elif cmd == "ingest":
    # ingest <answers_dir> <backend_label> [<batch_dir> <model> [tag]]  (batch_dir: metadata source when the queue was cleared)
    d, label = Path(sys.argv[2]), sys.argv[3]
    reqs = requests_from_batches(Path(sys.argv[4]), sys.argv[5], sys.argv[6] if len(sys.argv) > 6 else "") if len(sys.argv) > 5 else None
    n = sum(ingest_answers(p, label, requests=reqs) for p in sorted(d.glob("answers_*.jsonl")))
    print(f"ingested {n} answers from {d}")
elif cmd == "ingest-guarded":
    # ingest-guarded <answers_dir> <backend_label> <batch_dir> <model>  — applies the specificity guard first
    d, label, bdir, model = Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), sys.argv[5]
    reqs = requests_from_batches(bdir, model)
    for r in reqs.values():  # recover per-request tags from the batch lines' own json_mode/prompt shape
        r["tag"] = "classify" if "TAXONOMY (choose exactly one" in r["prompt"] else "triage" if "ESCALATION POLICY" in r["prompt"] else "draft" if "Write the reply." in r["prompt"] else "judge" if "CANDIDATE REPLY" in r["prompt"] else "shorten" if "Shorten this support reply" in r["prompt"] else ""
    total = Counter(); n = 0
    for p in sorted(d.glob("answers_*.jsonl")):
        if p.name.endswith(".guarded.jsonl"): continue
        g, st = guard(p, reqs); total.update(st)
        n += ingest_answers(g, label, requests=reqs)
    print(f"ingested {n} answers from {d}; guard stats {dict(total)}")
