<!-- ChainWatch specification, §17 to §19. Part of CLAUDE.md; section numbers are global. -->

## 17. Testing plan

Rule E6: every module has tests. Fast tests run on every commit (`make test`, target under about 90 seconds on a laptop); slow tests (`@pytest.mark.slow`) run before checkpoints and before submission (`make test-all`).

### 17.1 Configuration of the test suite

- `pyproject.toml` sets `[tool.pytest.ini_options]` with `markers = ["slow: slower than about 20 seconds"]`, `addopts = "-q -ra"`, `testpaths = ["tests"]`; and `[tool.ruff]` with line length 110, target `py310`, rules `E,F,I,B,UP` (ignore `UP` rules that would force 3.11+ syntax).
- `tests/conftest.py` builds, once per session, a **tiny world** by overriding config in code: `n_customers=1500, n_merchants=60, n_traders=10, n_salary_payers=4, n_landlords=30, n_agents=12, n_campaigns=12, days=8`, `eval.burn_in_days=1`, `eval.split_days=[4, 6, 8]`, seed 7. Fixtures: `tiny_cfg`, `tiny_world` (events and accounts in a temp dir), `tiny_features` (feature matrices), `tiny_models` (LightGBM with `n_estimators=40`, exported JSON), `engine` (a `ScoringEngine` over the tiny models), `client` (FastAPI `TestClient` over that engine).
- Serve-only tests live in `tests/serving/` and must pass in an environment that has **only** `requirements-serve.txt` plus `pytest` and `httpx` (CI job `serve-py310`, §17.3).
- Fixtures contain no real data (rule D1): hand-made PaySim-format rows and generated worlds only.

### 17.2 Test catalogue

| File | What it proves |
|---|---|
| `test_schema.py` | `EventIn` rejects label fields and extra keys, bad types, non-positive amounts; `LabeledEvent` is not accepted by the engine input type |
| `test_canonical_io.py` | csv.gz round trip with nulls; sort check; validation catches unsorted ids, duplicate ids, inconsistent balances |
| `test_world_invariants.py` | All invariants in §8.2.7 on the tiny world; every typology produces a structurally correct ring (hand-checked on one example each); mule reuse works; money conservation |
| `test_world_determinism.py` | Same config and seed gives byte-identical files; different seed differs; ids carry no archetype information (id-only classifier near chance) |
| `test_paysim_adapter.py` | Mapping on a 50-row hand-made fixture: types normalised, `ts` within the hour, merchant destination balances null, burn-in and splits by step |
| `test_registry.py` | Feature counts per group and per detector (13, 22, 12, 3, 9; B0 13, B1 50, G2 59; P0 27), order, defaults, registry hash changes when a name or semantic config changes |
| `test_features_edge_cases.py` | Hand-built streams for: windows at the boundary (`ts - W` excluded), first event of an account, equal timestamps ordered by `event_id`, out-of-order within lateness (`ts_eff`), FIFO (partial consumption, exact consumption, expiry, more debit than credits, many tiny credits), dwell weighting, trail depth over a 4-hop chain, funnel width, suspicion flag and TTL, hub rule, flow components (rebuild tick, drop of old edges), caps (`deque_cap`, `payee_memory_n`), eviction |
| `test_features_reference.py` | `FeatureEngine` equals `reference.py` for all 59 features (tolerance 1e-9) on the first 20,000 tiny-world events and on five hand-built streams, with huge caps. `@slow` for the 20,000-event case |
| `test_features_causality.py` | Features of events `0..i` identical on prefix and on full stream; modifying or deleting later events changes nothing for earlier ones; no NaN or inf anywhere |
| `test_matrix.py` | Matrix builder splits by time, excludes burn-in rows, writes aligned arrays, is deterministic, `names.json` matches the registry |
| `test_portable_gbdt.py` | Hand-built trees: boundary `x == threshold` goes left; negative-child leaf encoding; single-leaf tree; sigmoid numerics at extreme raw scores; rejects malformed JSON and a feature-name mismatch |
| `test_export_parity.py` (`@slow`) | Trains a small LightGBM on the tiny features, exports, then `PortableGBDT` and `vector_predict` equal LightGBM on 10,000 rows: probability difference below 1e-7 and identical leaf indices (`pred_leaf=True`); exporter raises on a categorical split or non-`<=` decision |
| `test_thresholds.py` | Quantile thresholds with ties on a toy score vector; achieved FPR reported; hold not below verify; the `+R` calibration finds `ring_alert` for a toy ring set; bisection converges and respects the FPR target on a toy simulation |
| `test_profile.py` | Quantile grid, `rank()` interpolation and clamping, `typical()` |
| `test_rings_grouping.py` | Cases listed in §11.8: chain forms a ring; star without relay does not; terminal edges do not link; hub does not glue; merge inside the link window and no merge outside; expiry and id stability across a rebuild; determinism under re-sorted input |
| `test_rings_scoring.py` | Noisy-OR and ring-score numbers (for example 0.5, 0.5 with one relay gives 0.375); clamping; top-k |
| `test_rings_escalation.py` | Out to `hold`, in to `verify`; no escalation for non-alerting rings; ring membership identical with escalation on and off (edges use base score) |
| `test_tiers.py` | Tier boundaries, ties to the higher tier, floor logic and effective score |
| `test_reasons.py` | One positive and one negative fixture per reason code; at most 3 reasons; ordering by weight; no unresolved placeholders; deny-list words absent; `allow` has no reasons; fallback `MODEL_SCORE`; rule file errors (unknown feature or filter) fail at start-up |
| `tests/serving/test_engine_flow.py` | Idempotent duplicate `event_id`; `late_event`; stage timings present and non-negative; production detector `G2+R`; compare mode returns shadow results; lock prevents interleaving |
| `tests/serving/test_api.py` | Every endpoint in §13.1: success and error shapes, auth on and off (`X-API-Key`), batch with one bad event, ring endpoints, account endpoint, 404s, label fields rejected, rate limit |
| `tests/serving/test_ws.py` | Snapshot on connect, batch messages, heartbeat, bounded queue drop, auth via `?key=` |
| `tests/serving/test_replay.py` | State machine, speed changes, warm-up silent processing, `next_campaign` fast-forward keeps state correct (decisions equal those of an uninterrupted run), reset |
| `tests/serving/test_inject.py` | Pure-Python generator is deterministic per seed; each typology has the right structure; no numpy import; injection respects the rate cap |
| `tests/serving/test_import_guard.py` | In a **subprocess**, install an import hook that raises on `numpy`, `pandas`, `sklearn`, `lightgbm`, `matplotlib`, `scipy`, `orjson`, `uvloop`, `httptools`; import `chainwatch.serving.app`, load the tiny models, score ten events, read `/v1/metrics`. Proves the serve env really is pure Python |
| `tests/serving/test_py310_syntax.py` | Every file under `chainwatch/serving`, `chainwatch/features`, `chainwatch/model/portable_gbdt.py`, `chainwatch/rings`, `chainwatch/decision`, `chainwatch/data/canonical.py`, `chainwatch/data/io.py`, `chainwatch/config.py`, `chainwatch/schema.py` parses with `ast.parse(source, feature_version=(3, 10))` |
| `test_eval_metrics.py` | Toy cases with known answers for PR-AUC, recall at FPR with ties, catch time and `tau`, hops remaining, before-exit logic, prevented bounds, false rings, archetype FPR, Wilson intervals, bootstrap reproducibility with a fixed seed |
| `test_eval_protocol.py` (`@slow`) | End-to-end on the tiny world: all variants produce results, test-touch log blocks a second run without `--allow-rerun`, headline sentences render from results |
| `test_leakage.py` | L1 to L9 pass on the tiny world; **canary**: a deliberately leaky feature is detected by the L3/L4 screens |
| `test_report_templates.py` | Every `{{placeholder}}` resolves from results; missing keys fail the build; filters format correctly |
| `test_claims.py` | `check_claims.py` fails on an unreferenced percentage, on forbidden phrases, and passes on rendered text with allowed numbers |
| `test_cli.py` | Each sub-command parses `--help`; `chainwatch world --preset small` runs (tiny override); exit codes |
| `test_e2e_determinism.py` (`@slow`) | Run world, features, train, thresholds twice on the tiny config: identical hashes for events, matrices, model JSON and thresholds |

### 17.3 Continuous integration (`.github/workflows/ci.yml`)

Two jobs on push and pull request: `serve-py310` (Python 3.10, installs `requirements-serve.txt`, `pytest`, `httpx`; runs `ruff check` and `pytest tests/serving -m "not slow"`), and `full-py312` (installs dev requirements; runs `ruff check` and `pytest -m "not slow"`). The slow suite is run by hand (`make test-all`) before checkpoints C3 and C6 (§19.5) and before submission. CI must be green on the submission commit.

### 17.4 Manual checks (write the outcome into `docs/STATUS.md`)

Dashboard in a real browser (Chromium and one other if available) at 1280x720 and on a projector resolution; WebSocket reconnect after killing and restarting the server; run for 30 minutes at 1200x speed and watch memory; container restart; SSH tunnel access to the LinuxONE instance.

---

## 18. Make targets and CLI

### 18.1 Makefile

Variables (override on the command line): `PY ?= python`, `PRESET ?= medium`, `SEEDS ?= 0 1 2 3 4`, `HOST_TAG ?= laptop`, `MODEL_DIR ?= models/default`, `WORLD ?= data/processed/world_$(PRESET)_seed0`.

| Target | Does |
|---|---|
| `setup-train` | `python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt` |
| `setup-serve` | `python -m venv .venv-serve && .venv-serve/bin/pip install -r requirements-serve.txt pytest httpx` (the pure-Python environment, also used to rehearse LinuxONE) |
| `lint`, `fmt` | `ruff check .`; `ruff format .` |
| `test` | `pytest -m "not slow"` |
| `test-all` | `pytest` (includes slow) |
| `test-serve` | Run `pytest tests/serving` inside `.venv-serve` (proves the serving path runs without the train packages) |
| `world` | For each seed in `SEEDS`: `python -m chainwatch world --preset $(PRESET) --seed $$s` |
| `world-demo` | `python -m chainwatch world-demo` (writes `data/replay/*`) |
| `world-freeze` | `python -m chainwatch world-freeze` (writes `results/world_manifest.json`) |
| `features` | `python -m chainwatch features --world $(WORLD)` |
| `train` | `python -m chainwatch train --features data/processed/features_world_$(PRESET)_seed0 --out $(MODEL_DIR) --detectors B0,B1,G2` |
| `thresholds` | `python -m chainwatch thresholds --features ... --models $(MODEL_DIR)` |
| `models` | `features train thresholds verify-models` for the seed-0 world of `PRESET` (`thresholds` also writes the benign profile and the manifest) |
| `verify-models` | `python scripts/verify_models.py $(MODEL_DIR)` |
| `evaluate` | `python -m chainwatch eval-all --preset $(PRESET) --seeds $(SEEDS)` (per seed: world, features, train into `models/eval/seed<N>`, thresholds, evaluate) then aggregate into `results/summary.json` |
| `ablate`, `robustness`, `leakage` | The corresponding sub-commands over the same seeds |
| `paysim` | `python scripts/profile_paysim.py` then `chainwatch paysim-convert` then features, train, thresholds, evaluate on PaySim (transaction-level only) |
| `eval-all` | `evaluate ablate leakage robustness paysim report check-claims` |
| `report` | `python -m chainwatch report` (renders templates into `README.md`, `reports/REPORT.md`, `docs/demo_script.md`, `docs/deck_outline.md`, figures) |
| `check-claims` | `python scripts/check_claims.py` |
| `serve` | `python -m chainwatch serve` (native) |
| `docker-build`, `docker-run`, `docker-stop` | Build `chainwatch:latest`; run with `-p 8000:8000 --restart unless-stopped`; stop and remove |
| `docker-s390x` | `docker buildx build --platform linux/s390x -t chainwatch:s390x --load .` (needs the QEMU setup of §16.5) |
| `wheelhouse-s390x` | Download s390x wheels for `requirements-serve.txt` into `wheelhouse-s390x/` (§16.3 step 3) |
| `bundle` | Tarball `dist/chainwatch-bundle.tar.gz` of tracked files plus `models/default` and `data/replay` |
| `bench-engine`, `bench-load` | `python bench/engine_bench.py --host-tag $(HOST_TAG)`; `python bench/load_test.py --host-tag $(HOST_TAG)` |
| `platform-info` | `python bench/platform_info.py --host-tag $(HOST_TAG)` |
| `check-platform` | `bash scripts/check_platform.sh` |
| `parity` | `python -m chainwatch parity --out results/parity_$(HOST_TAG).jsonl` |
| `demo` | Start the server (native) with compare mode on and print the URL and the shortcut keys |
| `clean` | Remove `.venv*`, `__pycache__`, `dist`, `wheelhouse-*`; never touches `data/raw`, `models`, `results` |

Every target prints the command it runs. Long targets log progress and expected duration (rule E9).

### 18.2 CLI (`python -m chainwatch <command>`, `argparse`, stdlib only for the serving-related commands)

Global options: `--config PATH` (default `configs/default.toml`), `--set section.key=value` (repeatable; same effect as `CHAINWATCH__SECTION__KEY`), `--log-level`, `--small` (shorthand for preset small and two seeds where applicable).

| Command | Arguments | Output |
|---|---|---|
| `world` | `--preset small|medium|large`, `--seed N`, `--days N`, `--out DIR` | `data/processed/world_<preset>_seed<N>/` (§8.2.6); train env |
| `world-demo` | none | `data/replay/demo.csv.gz`, `demo_accounts.csv.gz`, `demo_rings.json` |
| `world-freeze` | `--worlds DIR...` | `results/world_manifest.json` with SHA-256 per events file |
| `paysim-convert` | `--src data/raw/paysim --out data/processed/paysim` | Canonical stream plus `meta.json` with the step-based split (§8.3) |
| `features` | `--world DIR`, `--limit N`, `--priority P0|P1` | `data/processed/features_<world_tag>/` (§9.9) |
| `train` | `--features DIR`, `--out DIR`, `--detectors B0,B1,G2`, `--grid` (run the tuning grid of §10.2) | Model JSONs and train reports |
| `thresholds` | `--features DIR`, `--models DIR` | `thresholds.json`, `benign_profile.json`, `manifest.json` |
| `eval-all` | `--preset`, `--seeds`, `--skip-existing` | Per-seed results and `results/summary.json` |
| `evaluate` | `--features DIR`, `--models DIR`, `--allow-rerun` | `results/eval_<world_tag>.json` |
| `ablate` | `--seeds`, `--ids A1,A2,...` | `results/ablation_<world_tag>.json` |
| `robustness` | `--seeds` | `results/robustness.json` |
| `leakage` | `--features DIR` | `results/leakage_<world_tag>.json` |
| `parity` | `--out FILE` or `--compare A B` | Decision and score parity file or comparison report |
| `report` | none | Rendered docs, figures, `reports/REPORT.md` |
| `check-claims` | none | Same as `scripts/check_claims.py` |
| `serve` | `--host`, `--port`, `--model-dir`, `--no-replay`, `--compare` | Runs uvicorn (§13.1) |

Exit codes: 0 success, 1 failure, 2 usage error, 3 refused (for example `evaluate` without `--allow-rerun`, or a model/registry hash mismatch).

---

## 19. Build plan and timeline

### 19.1 Before the kickoff window opens (default: assume the rules do NOT allow pre-built code)

Allowed (environment, access, data and tools only):
1. Attend the LinuxONE setup and tech-prep session (Oct 16, 7:30 PM IST) and get access working; run the shell commands of §16.3 step 1 by hand on the instance (the repository scripts do not exist yet) and save the outputs for `docs/linuxone_runbook.md`.
2. Install and test tools on the laptop: Python 3.11 or 3.12, Python 3.10 if available (for the serve venv), git, Docker, `buildx` and the QEMU s390x rehearsal using **a throwaway one-line container** (§16.5 first two commands), Node (only for `npm pack cytoscape`).
3. Create a Kaggle account and download PaySim (`paysim1`) into `data/raw/paysim/` (git-ignored).
4. Create an **empty** GitHub repository.
5. Read the Official Rules page yourself and tell Claude Code whether pre-work is allowed. If the rules explicitly allow it, the user may say so and the build can start earlier; until then do not write project code, generators, features, models or UI, and do not pre-generate worlds or pre-train models.

### 19.2 Priorities

| Priority | Meaning | Contents |
|---|---|---|
| **P0** | Walking skeleton; must work by checkpoint C2 | Config, schema, canonical IO; ChainWorld v0 (customers, merchants, agents; `funnel` and `layered_chain`); FeatureEngine with the 27 P0 features; matrix builder; LightGBM training for B1 and G2; exporter and `PortableGBDT` with parity test; `ScoringEngine`; `POST /v1/score`, `/healthz`, `/v1/info`; simple quantile thresholds; minimal ring grouper; Dockerfile; running on LinuxONE (or a documented blocker) |
| **P1** | The submission | Full ChainWorld (all archetypes and typologies, balances, calibration, freeze); all 59 features with reference tests; B0; full ring layer with calibration and escalation; reasons; replay, WebSocket, dashboard, injection, comparison panel; evaluation protocol on 5 seeds with ablations A1 to A5, leakage checks, benchmarks on both machines, parity; README, report, cards, deck, demo script, video |
| **P2** | Nice to have | Robustness grid R1 to R6; ablations A6 to A10; PaySim end-to-end evaluation (profile is P1); `calibrate_with_rings` bisection (P1 if time, else report pre/post escalation FPR separately); contributions-based reason ordering; compiled-tree evaluator; large preset; organiser-dataset adapter beyond the check in §8.4 |

### 19.3 Phase plan (T0 = kickoff, Oct 17, 9:00 AM IST; the real deadline is UNVERIFIED, so shift everything once it is known)

| Window | Work | Exit criterion |
|---|---|---|
| T+0:00 to 0:45 | Attend kickoff; record problem statements, dataset, rules, deadline, submission format in `docs/STATUS.md`; confirm track and the organiser-data decision (§8.4); answer §0.1; repo skeleton, `pyproject.toml`, `Makefile`, config loader, schema, canonical IO, CI file | `make test` runs; STATUS.md filled |
| 0:45 to 2:30 | ChainWorld v0 and its invariants; `chainwatch world --preset small` | Tiny and small worlds generate; invariants pass |
| 2:30 to 4:30 | FeatureEngine P0, matrix builder, training of B1 and G2 (P0 features), exporter, `PortableGBDT`, parity test | Parity test green; first PR-AUC numbers printed (not claims) |
| 4:30 to 6:00 | `ScoringEngine`, API, tiers, simple thresholds, minimal rings, Dockerfile; **deploy the skeleton to LinuxONE** | **C2:** `curl /v1/score` works locally and on the LinuxONE instance (or the blocker is documented and the QEMU rehearsal is done) |
| 6:00 to 9:00 | Full ChainWorld, B0/B1 difficulty calibration (B0 and B1 only), freeze, generate 5 medium worlds in the background; finish all 59 features with reference and causality tests | **C3:** worlds frozen; equivalence test green |
| 9:00 to 12:00 | Full ring layer, calibration, escalation, reasons; replay, WebSocket, dashboard v1; per-seed features and training running in the background | **C4:** dashboard shows a replayed ring with reasons |
| 12:00 to 14:00 | `evaluate` on all seeds; fix issues; ablations A1 to A5; leakage checks; PaySim profile | **C5:** `results/summary.json` exists with real numbers (whatever they say) |
| 14:00 to 15:30 | **Rest block** (60 to 90 minutes; background jobs keep running). Tired people ship bugs | n/a |
| 15:30 to 18:00 | Dashboard polish, injection, comparison panel; benchmarks and parity on laptop and LinuxONE; robustness and PaySim if time | Bench and parity files for both hosts |
| 18:00 to 21:00 | Report, README, model and data cards, deck, demo script; `make eval-all` templating; `check-claims` clean | **C6:** all docs rendered; claims check passes |
| 21:00 to 23:00 | Video, final container on LinuxONE, tag and freeze the repo, submit | **C7:** submission made; STATUS.md final |
| 23:00 to 24:00 | Buffer for the unexpected | Do not start new features |

### 19.4 `docs/STATUS.md` template (create it in the first 10 minutes; update at least hourly)

```markdown
# ChainWatch status

Last updated: <UTC and IST time>   Current phase: <name>   Checkpoint reached: <C0..C7>

## Event facts (fill at kickoff)
- Deadline: <IST>        Submission format: <repo / video / deck / form>
- Track and problem statements: <...>
- Organiser dataset: <none | description, licence, path>   Decision (section 8.4): <stay | adapt | pivot>
- Judging criteria and weights: <...>        Rules on libraries/platforms: <...>
- LinuxONE access: <how, OS image, Python, Docker?, outbound internet?, ports, CPU/RAM/disk, expiry>

## Done
- [ ] ...

## In progress
- ...

## Next
- ...

## Results so far (copy from results/*.json, never from memory)
- ...

## Decisions (also in docs/decisions.md)
- <date/time> <decision> because <reason>

## Known issues and risks
- ...

## How to resume
- Commands to run, files to open first.
```

### 19.5 Checkpoints and the cut list

| Checkpoint | When | If missed, cut in this order |
|---|---|---|
| C2 skeleton end to end (local and LinuxONE) | T+6:00 | Drop all P1 features and A3 to A5; drop B0 retraining variants; keep P0 only; skip the large preset and PaySim end-to-end |
| C3 worlds frozen, equivalence test green | T+9:00 | Use 3 seeds instead of 5; drop the robustness grid; reduce ablations to A1 and A2 |
| C4 dashboard shows a ring with reasons | T+12:00 | Drop the comparison panel, then injection, then WebSocket (poll `GET /v1/rings` and `/v1/metrics` every second); keep the feed, ring list and graph |
| C5 real results exist | T+14:00 | Evaluate with 2 seeds; skip PaySim evaluation (keep its profile); skip `calibrate_with_rings` and report pre/post-escalation FPR separately |
| C6 docs rendered, claims clean | T+21:00 | Shorten the deck to 6 slides; skip model card details beyond the template; video can be a screen recording of the dashboard with a spoken script |
| C7 submitted | T+23:00 | Submit the best verified state; never submit unverified claims |

**Minimum viable submission (MVS):** the P0 skeleton running on LinuxONE (or honestly documented), ChainWorld with at least two typologies, B0 versus G2+R at matched FPR on at least 2 seeds, a dashboard with the live feed and ring view, README with real numbers and limits, a demo video. Everything else improves it.

### 19.6 Compute and background jobs

- Check CPU count and RAM at the start (`nproc`, `free -m`) and record them. Run per-seed jobs as separate processes (`make evaluate` may use `xargs -P` or `multiprocessing` with one process per seed; the engine itself stays single-threaded).
- Start long jobs with `nohup ... > logs/<name>.log 2>&1 &`, print the expected duration, and poll the log instead of blocking.
- Expected costs (UNVERIFIED, measure early with the small preset and extrapolate): world generation, feature replay (dominant, pure Python), LightGBM training (minutes), vectorised evaluation (fast). Record the real figures in `docs/STATUS.md` after the first small run and plan the evaluation window from them.
- If the laptop is too slow for the medium preset, reduce `n_customers` and `days` in the preset (record in `docs/decisions.md`), or use Colab for the train-env steps and bring the JSON models back.

### 19.7 Git and process

- Work on `main` with short-lived feature branches if useful; commit small and often; messages in imperative mood. Follow whatever attribution lines the environment prescribes for commits and pull requests.
- Before each commit run `make lint` and `make test`. Never commit `data/raw`, `data/processed`, secrets, or files over 10 MB (`data/replay` is the only large tracked data and stays under 10 MB in total).
- Tag `v0.1-skeleton` at C2 and `v1.0-submission` at C7.
- When something in this file is wrong or unworkable, fix the code path, write the reason in `docs/decisions.md`, and edit this file in the same commit so it stays truthful.

### 19.8 If there is more than one person

Suggested split: **A** data, features, models (sections 8 to 10); **B** rings, serving, dashboard, deployment (sections 11 to 13, 16); **C** evaluation, benchmarks, docs, deck, video (sections 14, 15, 20). Interfaces are the canonical schema (§8.1), the feature registry (§9.8) and the model package (§10.6); agree on them first and test against them.
