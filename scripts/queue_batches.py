"""Split pending LLM prompts (queue backend) into batch files for out-of-band answering, and ingest answers.

  uv run python scripts/queue_batches.py split <out_dir> [batch_size]   -> writes batch_i.jsonl + batches.json
  uv run python scripts/queue_batches.py ingest <answers_dir> <backend_label>
"""
import json, sys
from pathlib import Path
sys.path.insert(0, "src")
from support_agent.llm import ingest_answers, pending_requests

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
    d, label = Path(sys.argv[2]), sys.argv[3]
    n = sum(ingest_answers(p, label) for p in sorted(d.glob("answers_*.jsonl")))
    print(f"ingested {n} answers from {d}")
