# Reproduce the headline numbers offline (cached model outputs) in a few minutes:  make reproduce
# Re-run live against a model:  ANTHROPIC_API_KEY=... make live   (or LLM_BACKEND=ollama make live)
TAG ?= claude-haiku-4-5
JUDGE_TAG ?= claude-sonnet-5

.PHONY: setup data test reproduce live report clean

setup:            ## install deps (uv) and download the Kaggle dataset (~170MB, no account needed)
	uv sync
	mkdir -p data/raw && cd data/raw && [ -f twcs/twcs.csv ] || (curl -L -o twcs.zip "https://www.kaggle.com/api/v1/datasets/download/thoughtvector/customer-support-on-twitter" && unzip -o -q twcs.zip)

data: setup       ## build the SpotifyCares corpus (thread reconstruction, language filter, dedup)
	uv run support-agent build-corpus

test:
	uv run pytest -q

reproduce:        ## replay cached model outputs -> all metrics in eval/results (no API key needed)
	LLM_BACKEND=cache-only uv run support-agent run-agent --tag $(TAG) --model claude-haiku-4-5-20251001
	uv run support-agent eval-intents --tag $(TAG)
	uv run support-agent eval-triage --tag $(TAG)
	LLM_BACKEND=cache-only uv run support-agent judge-replies $(TAG) --judge-model claude-sonnet-5
	uv run support-agent agreement $(TAG) $(JUDGE_TAG)
	uv run support-agent report

live:             ## same pipeline, live model calls (writes to the cache)
	uv run support-agent run-agent --tag $(TAG)
	uv run support-agent eval-intents --tag $(TAG)
	uv run support-agent eval-triage --tag $(TAG)
	uv run support-agent judge-replies $(TAG)
	uv run support-agent report

report:
	uv run support-agent report

clean:
	rm -rf data/processed/*.parquet data/cache/emb_* eval/results/*.json
