# ChainWatch make targets (spec section 18.1). Every target echoes the command it runs.
# `make` is not installed on the Windows build machine; the same recipes run under Git Bash,
# WSL, Linux and macOS.

PY ?= python
PRESET ?= medium
SEEDS ?= 0 1 2 3 4
HOST_TAG ?= laptop
MODEL_DIR ?= models/default
WORLD ?= data/processed/world_$(PRESET)_seed0
FEATS ?= data/processed/features_world_$(PRESET)_seed0

.DEFAULT_GOAL := help

.PHONY: help setup-train setup-serve lint fmt test test-all test-serve world world-demo \
        world-freeze features train thresholds models verify-models calibrate evaluate \
        eval-all ablate robustness leakage paysim report check-claims serve demo \
        docker-build docker-run docker-stop docker-s390x wheelhouse-s390x bundle \
        bench-engine bench-load platform-info check-platform parity clean

help: ## list the targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

setup-train: ## train environment in .venv
	$(PY) -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

setup-serve: ## pure-Python serve environment in .venv-serve (rehearsal for LinuxONE)
	$(PY) -m venv .venv-serve && .venv-serve/bin/pip install -r requirements-serve.txt pytest httpx

lint: ## ruff over the repository
	$(PY) -m ruff check .

fmt: ## format with ruff
	$(PY) -m ruff format .

test: ## fast tests only (under about 90s)
	$(PY) -m pytest -m "not slow"

test-all: ## every test including slow
	$(PY) -m pytest

test-serve: ## serving tests inside the pure-Python environment
	.venv-serve/bin/python -m pytest tests/serving -m "not slow"

world: ## generate one world per seed in SEEDS
	@for s in $(SEEDS); do echo ">> chainwatch world --preset $(PRESET) --seed $$s"; \
		$(PY) -m chainwatch world --preset $(PRESET) --seed $$s || exit 1; done

world-demo: ## demo replay world into data/replay
	$(PY) -m chainwatch world-demo

world-freeze: ## results/world_manifest.json
	$(PY) -m chainwatch world-freeze

features: ## replay the world through the feature engine
	$(PY) -m chainwatch features --world $(WORLD)

train: ## train B0, B1, G2 for the seed-0 world
	$(PY) -m chainwatch train --features $(FEATS) --out $(MODEL_DIR) --detectors B0,B1,G2

thresholds: ## validation thresholds, benign profile, manifest
	$(PY) -m chainwatch thresholds --features $(FEATS) --models $(MODEL_DIR)

models: features train thresholds verify-models ## full model package for the seed-0 world of PRESET

verify-models: ## parity and package checks
	$(PY) scripts/verify_models.py $(MODEL_DIR)

calibrate: ## world difficulty check with B0 and B1 only (never G2)
	$(PY) -m chainwatch calibrate --preset $(PRESET) --seed 1000

evaluate: ## per-seed evaluation, then results/summary.json
	$(PY) -m chainwatch eval-all --preset $(PRESET) --seeds $(SEEDS)

ablate: ## ablations A1 to A10
	$(PY) -m chainwatch ablate --seeds $(SEEDS)

robustness: ## robustness grid R1 to R6
	$(PY) -m chainwatch robustness --models models/eval/medium_seed0

leakage: ## leakage checks L1 to L9
	$(PY) -m chainwatch leakage --seeds $(SEEDS)

paysim: ## profile and convert PaySim, then transaction-level evaluation
	$(PY) scripts/profile_paysim.py
	$(PY) -m chainwatch paysim-convert --src data/raw/paysim --out data/processed/paysim

eval-all: evaluate ablate leakage robustness paysim report check-claims ## the whole pipeline

report: ## render README, report, cards, demo script and deck outline from results
	$(PY) -m chainwatch report

check-claims: ## fail on numbers without a results source or forbidden phrases
	$(PY) scripts/check_claims.py

serve: ## API and dashboard on :8000 (one uvicorn worker, rule E8)
	$(PY) -m chainwatch serve

demo: ## serve with compare mode on and print the URL and shortcut keys
	$(PY) -m chainwatch serve --compare

docker-build: ## build chainwatch:latest
	docker build -t chainwatch:latest .

docker-run: ## run the container on :8000
	docker run -d --name chainwatch -p 8000:8000 --restart unless-stopped chainwatch:latest

docker-stop: ## stop and remove the container
	-docker rm -f chainwatch

docker-s390x: ## cross-build for linux/s390x (needs the QEMU setup of spec 16.5)
	docker buildx build --platform linux/s390x -t chainwatch:s390x --load .

wheelhouse-s390x: ## download s390x wheels for requirements-serve.txt
	bash scripts/check_platform.sh wheelhouse

bundle: ## dist/chainwatch-bundle.tar.gz of tracked files, models/default and data/replay
	mkdir -p dist && git archive --format=tar.gz --output=dist/chainwatch-bundle.tar.gz HEAD

bench-engine: ## in-process engine latency and throughput
	$(PY) bench/engine_bench.py --host-tag $(HOST_TAG)

bench-load: ## HTTP load test against a running server
	$(PY) bench/load_test.py --host-tag $(HOST_TAG)

platform-info: ## CPU, RAM, Python and OS record for the host
	$(PY) bench/platform_info.py --host-tag $(HOST_TAG)

check-platform: ## probe a machine for the serve environment (run it on LinuxONE too)
	bash scripts/check_platform.sh

parity: ## cross-platform decision parity file
	$(PY) -m chainwatch parity --out results/parity_$(HOST_TAG).jsonl

clean: ## remove venvs, caches, dist; never touches data/raw, models or results
	rm -rf .venv .venv-serve dist wheelhouse-* .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
