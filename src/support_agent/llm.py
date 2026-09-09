"""Minimal LLM client with pluggable backends and a content-addressed disk cache.

Backends
  anthropic  : Claude via the Anthropic API (needs ANTHROPIC_API_KEY). Default when the key is set.
  ollama     : a local model served by Ollama at http://localhost:11434 (offline fallback).
  queue      : never calls a model. Cache misses are appended to data/cache/queue/pending.jsonl so
               they can be answered in a batch (see `ingest_answers`); the call returns None.
  cache-only : replay recorded responses; raise on a miss. Used to reproduce reported numbers
               without any model access.

Every call is cached under data/cache/llm/<sha256>.json keyed by (model, system, prompt, temperature,
max_tokens, json_mode). The model name identifies the generator, so replaying with the same
LLM_MODEL reproduces a run exactly regardless of which backend produced it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

CACHE_DIR = Path("data/cache/llm")
QUEUE_DIR = Path("data/cache/queue")
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_OLLAMA_MODEL = "qwen3:1.7b"
BACKENDS = {"anthropic", "ollama", "queue", "cache-only"}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    text: str
    backend: str
    model: str
    cached: bool
    latency_s: float


def cache_key(model: str, system: str, prompt: str, temperature: float, max_tokens: int, json_mode: bool) -> str:
    blob = json.dumps({"model": model, "system": system, "prompt": prompt, "temperature": temperature,
                       "max_tokens": max_tokens, "json_mode": json_mode}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def extract_json(text: str) -> Any:
    """Parse the JSON answer in a model response (tolerates fences, preambles, echoed lists)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [m.start() for m in re.finditer(r"[{\[]", text)]
    if not starts:
        raise LLMError(f"no JSON in response: {text[:200]!r}")
    # prefer objects over arrays: models often echo option lists before answering with an object
    for start in [i for i in starts if text[i] == "{"] + [i for i in starts if text[i] == "["]:
        try:
            return _parse_balanced(text, start)
        except (LLMError, json.JSONDecodeError):
            continue
    raise LLMError(f"unbalanced JSON in response: {text[:200]!r}")


def _parse_balanced(text: str, start: int) -> Any:
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise LLMError("unbalanced")


class LLMClient:
    def __init__(self, backend: str | None = None, model: str | None = None,
                 cache_dir: Path = CACHE_DIR, max_workers: int = 4, timeout: float = 240.0):
        backend = backend or os.getenv("LLM_BACKEND") or ("anthropic" if os.getenv("ANTHROPIC_API_KEY") else "ollama")
        if backend not in BACKENDS:
            raise ValueError(f"unknown backend {backend!r}; choose from {sorted(BACKENDS)}")
        self.backend = backend
        default = DEFAULT_OLLAMA_MODEL if backend == "ollama" else DEFAULT_ANTHROPIC_MODEL
        self.model = model or os.getenv("LLM_MODEL") or default
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_workers = max_workers if backend == "anthropic" else 1  # local models: serialise
        self.timeout = timeout
        self._client = None
        self.stats = {"calls": 0, "cached": 0, "errors": 0, "queued": 0}

    # ---- backends -----------------------------------------------------------------------------
    def _anthropic(self, system: str, prompt: str, temperature: float, max_tokens: int, json_mode: bool) -> str:
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        messages = [{"role": "user", "content": prompt}]
        if json_mode:  # prefill steers the model straight into the object
            messages.append({"role": "assistant", "content": "{"})
        for attempt in range(5):
            try:
                msg = self._client.messages.create(
                    model=self.model, max_tokens=max_tokens, temperature=temperature,
                    system=system or "You are a helpful assistant.", messages=messages,
                )
                text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
                return ("{" + text) if json_mode else text
            except Exception as e:  # rate limits / transient
                if attempt == 4:
                    raise LLMError(str(e)) from e
                time.sleep(2 ** attempt)
        raise LLMError("unreachable")

    def _ollama(self, system: str, prompt: str, temperature: float, max_tokens: int, json_mode: bool) -> str:
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        body: dict[str, Any] = {
            "model": self.model, "stream": False, "think": False,
            "messages": ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}],
            "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 8192},
        }
        if json_mode:
            body["format"] = "json"
        for attempt in range(3):
            try:
                r = httpx.post(f"{host}/api/chat", json=body, timeout=self.timeout)
                r.raise_for_status()
                return r.json()["message"]["content"]
            except Exception as e:
                if attempt == 2:
                    raise LLMError(f"ollama: {e}") from e
                time.sleep(2)
        raise LLMError("unreachable")

    def _queue(self, key: str, system: str, prompt: str, temperature: float, max_tokens: int,
               json_mode: bool, tag: str) -> None:
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"key": key, "model": self.model, "system": system, "prompt": prompt, "temperature": temperature,
               "max_tokens": max_tokens, "json_mode": json_mode, "tag": tag}
        with open(QUEUE_DIR / "pending.jsonl", "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.stats["queued"] += 1

    # ---- public API ---------------------------------------------------------------------------
    def complete(self, prompt: str, system: str = "", temperature: float = 0.0, max_tokens: int = 600,
                 tag: str = "", json_mode: bool = False) -> LLMResponse | None:
        key = cache_key(self.model, system, prompt, temperature, max_tokens, json_mode)
        path = self.cache_dir / f"{key}.json"
        self.stats["calls"] += 1
        if path.exists():
            rec = json.loads(path.read_text())
            self.stats["cached"] += 1
            return LLMResponse(rec["text"], rec["backend"], rec["model"], True, 0.0)
        if self.backend == "cache-only":
            raise LLMError(f"cache miss in cache-only mode (tag={tag!r}, model={self.model}); "
                           "run with a live backend (anthropic/ollama) first")
        if self.backend == "queue":
            self._queue(key, system, prompt, temperature, max_tokens, json_mode, tag)
            return None
        t0 = time.time()
        try:
            if self.backend == "anthropic":
                text = self._anthropic(system, prompt, temperature, max_tokens, json_mode)
            else:
                text = self._ollama(system, prompt, temperature, max_tokens, json_mode)
        except LLMError:
            self.stats["errors"] += 1
            raise
        latency = time.time() - t0
        write_cache_record(self.cache_dir, key, self.backend, self.model, system, prompt, temperature,
                           max_tokens, json_mode, text, tag, latency)
        return LLMResponse(text, self.backend, self.model, False, latency)

    def complete_json(self, prompt: str, system: str = "", temperature: float = 0.0, max_tokens: int = 600,
                      tag: str = "", retries: int = 1) -> Any | None:
        """Call the model in JSON mode and parse; retry once with a repair instruction. None if queued."""
        last_err: Exception | None = None
        p = prompt
        for _ in range(retries + 1):
            resp = self.complete(p, system=system, temperature=temperature, max_tokens=max_tokens,
                                 tag=tag, json_mode=True)
            if resp is None:
                return None
            try:
                return extract_json(resp.text)
            except (LLMError, json.JSONDecodeError) as e:
                last_err = e
                p = prompt + "\n\nYour previous answer was not valid JSON. Respond with ONLY one JSON object."
        raise LLMError(f"JSON parse failed after retries: {last_err}")

    def map(self, fn, items: list, desc: str = "") -> list:
        """Apply fn(item) concurrently (Anthropic) or sequentially (local / queue)."""
        from tqdm import tqdm
        if self.max_workers <= 1:
            return [fn(x) for x in tqdm(items, desc=desc, disable=not desc)]
        with ThreadPoolExecutor(self.max_workers) as ex:
            return list(tqdm(ex.map(fn, items), total=len(items), desc=desc, disable=not desc))


def write_cache_record(cache_dir: Path, key: str, backend: str, model: str, system: str, prompt: str,
                       temperature: float, max_tokens: int, json_mode: bool, text: str, tag: str,
                       latency: float = 0.0) -> Path:
    rec = {"backend": backend, "model": model, "system": system, "prompt": prompt, "temperature": temperature,
           "max_tokens": max_tokens, "json_mode": json_mode, "text": text, "latency_s": latency, "tag": tag}
    path = cache_dir / f"{key}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    return path


def pending_requests(path: Path = QUEUE_DIR / "pending.jsonl") -> list[dict]:
    """Unique pending prompts (deduplicated by key, excluding ones already cached)."""
    if not path.exists():
        return []
    seen: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if not (CACHE_DIR / f"{rec['key']}.json").exists():
                seen[rec["key"]] = rec
    return list(seen.values())


def ingest_answers(answers_path: Path, backend_label: str, cache_dir: Path = CACHE_DIR,
                   requests: dict[str, dict] | None = None) -> int:
    """Write batch answers ({key, text} per line) into the cache using the queued request metadata
    (from pending.jsonl by default, or an explicit {key: request} map, e.g. read from batch files)."""
    pending = requests if requests is not None else {r["key"]: r for r in pending_requests()}
    n = 0
    for line in answers_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            a = json.loads(line)
        except json.JSONDecodeError as e:  # a malformed answer line: skip it, the prompt is simply re-queued later
            print(f"skipping malformed answer line in {answers_path.name}: {str(e)[:80]}")
            continue
        req = pending.get(a["key"])
        if req is None or not a.get("text"):
            continue
        write_cache_record(cache_dir, a["key"], backend_label, req["model"], req["system"], req["prompt"],
                           req.get("temperature", 0.0), req["max_tokens"], req["json_mode"], a["text"], req.get("tag", ""))
        n += 1
    return n


def requests_from_batches(batch_dir: Path, model: str, tag: str = "") -> dict[str, dict]:
    """Rebuild the request map from batch_*.jsonl files (they carry key/system/prompt/json_mode/max_tokens)."""
    out: dict[str, dict] = {}
    for p in sorted(Path(batch_dir).glob("batch_*.jsonl")):
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["key"]] = {**r, "model": model, "temperature": 0.0, "tag": tag}
    return out
