<!-- ChainWatch specification, §4 to §7. Part of CLAUDE.md; section numbers are global. -->

## 4. Architecture

```text
 OFFLINE (laptop / Colab, x86_64, full Python stack)            ONLINE (laptop AND IBM LinuxONE s390x, pure Python)
 +------------+   +---------------+   +--------------+  JSON   +-------------------------------------------------+
 | ChainWorld |-->| FeatureEngine |-->| LightGBM     |-------->| models/*.json  (portable trees, thresholds,     |
 | PaySim     |   | (stream replay|   | B0 / B1 / G2 |         |                 benign profile, feature spec)   |
 | Organizer  |   |  = training   |   +--------------+         +-------------------------+-----------------------+
 +------------+   |  data)        |                                                      |
                  +---------------+                                                      v
   Event (no labels) --> FeatureEngine --> PortableGBDT --> Tiers --> Reasons --> Decision (allow | verify | hold)
                          | windows, FIFO     score         |                          |
                          | trail, flow comps               v                          v
                          +--------------------------> RingGrouper --> RingAlert --> escalation back into Tiers
                                                              |
                                  FastAPI: /v1/score, /v1/rings, WebSocket /v1/ws, replay, metrics
                                                              |
                                                       Dashboard (static HTML + JS, cytoscape)
```

**Per-event flow in the serving engine** (`chainwatch/serving/engine.py`, one object shared by `/v1/score`, replay and benchmarks):

1. Validate the `Event` (pydantic; no label fields allowed; `extra="forbid"`).
2. Idempotency: if `event_id` is among the last `[serving].request_id_lru` ids, return the cached decision without touching state.
3. Lateness: if `ts < engine_clock - max_lateness_s`, reject with `late_event`. Otherwise advance `engine_clock = max(engine_clock, ts)`.
4. `FeatureEngine.step(event)` returns a feature dict and updates all account state (strictly causal: features use only events with earlier `(ts, event_id)`).
5. `PortableGBDT.predict(features)` for the production detector `G2`; when `[serving].compare_mode` is on, also for `B0` and `B1` (shadow results for the dashboard).
6. Ring escalation (§11.5): if the sender is a member of a ring in `alert` status, the tier is raised to at least `hold`; else if the receiver is, to at least `verify`. This uses ring state from before the current event.
7. `tiers.decide(base_score, thresholds, floor)` returns `allow`, `verify` or `hold` and the effective score, using the `G2+R` entry of `models/default/thresholds.json` (§10.4).
8. `reasons.explain(features, score)` returns up to 3 reason codes with plain text.
9. If the **base** score (before escalation) is at or above the edge threshold, report the edge to `RingGrouper`; it may create, grow or merge rings and may emit ring events and `RingAlert`s.
10. Record stage timings (`features`, `model`, `decision`, `total`) with `time.perf_counter_ns` into the metrics histogram. Return the decision.

---

## 5. Tech stack and dependency policy

### 5.1 Two environments (do not mix them)

| | Training and evaluation ("train env") | Serving ("serve env") |
|---|---|---|
| Where | Laptop or Colab, x86_64 or arm64 | Laptop **and** LinuxONE s390x, inside Docker or a venv |
| Python | 3.11 or 3.12 | 3.10 to 3.13 (must work on 3.10) |
| Packages | numpy, pandas, scikit-learn (metrics only), LightGBM, matplotlib, pytest, ruff, httpx, plus everything in serve env | fastapi, pydantic, uvicorn (plain), websockets, tomli (only on Python < 3.11) |
| Never needed here | n/a | numpy, pandas, scikit-learn, LightGBM, matplotlib, orjson, uvloop, httptools, pyarrow |

### 5.2 Requirements files

`requirements-serve.txt`:

```text
fastapi>=0.110,<1
pydantic>=2.6,<3
uvicorn>=0.27,<1
websockets>=12
tomli>=2.0; python_version < "3.11"
```

Use plain `uvicorn`, **not** `uvicorn[standard]` (it pulls `uvloop` and `httptools`, which have no s390x wheels and would compile from source).

`requirements-train.txt`:

```text
-r requirements-serve.txt
numpy>=1.26
pandas>=2.2
scikit-learn>=1.4
lightgbm>=4.3
matplotlib>=3.8
```

`requirements-dev.txt`:

```text
-r requirements-train.txt
pytest>=8
ruff>=0.4
httpx>=0.27
networkx>=3.2
```

After the first clean install on the laptop, run `pip freeze > requirements-serve.lock` inside a venv that contains **only** serve requirements, on Python 3.10 if available, and commit the lock. On macOS with Apple Silicon, LightGBM needs `brew install libomp`.

### 5.3 Dashboard assets

Vendor `cytoscape.min.js` (MIT licence; keep its LICENSE file) into `chainwatch/serving/static/vendor/` using `scripts/build_vendor_assets.sh` (runs `npm pack cytoscape`, extracts `dist/cytoscape.min.js`). No CDN links at runtime (rule D3). No charting library: draw sparklines and the latency histogram with hand-written canvas code (about 80 lines).

### 5.4 Why not the usual stack (for the README "design decisions" section)

- No numpy at serve time: no s390x wheel on PyPI for the latest release; building from source needs a compiler toolchain and takes long.
- No pickle or joblib models: pickles of NumPy arrays and scikit-learn trees are architecture-sensitive; two earlier winning teams hit byte-order bugs on LinuxONE.
- Pure Python per-event work is affordable: our features are dictionary and deque operations, and each tree evaluation is a handful of comparisons.

---

## 6. Repository layout

```text
chainwatch/                          # repo root
|-- CLAUDE.md                        # this file
|-- README.md                        # public landing page, rendered from README.template.md (section 20.1)
|-- README.template.md               # template with {{placeholders}}; edit this, not README.md
|-- .github/workflows/ci.yml         # ruff + fast tests on Python 3.10 (serve only) and 3.12 (full)
|-- LICENSE                          # MIT unless the user says otherwise
|-- Makefile                         # targets in section 18
|-- pyproject.toml                   # package metadata, ruff and pytest config
|-- requirements-serve.txt
|-- requirements-train.txt
|-- requirements-dev.txt
|-- requirements-serve.lock          # generated, committed
|-- .env.example                     # CHAINWATCH_API_KEY= (blank)
|-- .gitignore                       # data/raw, data/interim, data/processed, models/eval, logs, dist, wheelhouse-*, .venv, .venv-serve, __pycache__, .env
|-- .dockerignore
|-- Dockerfile                       # python:3.12-slim based serving image
|-- Dockerfile.ubuntu                # fallback: ubuntu:22.04 + apt python3 + venv
|-- docker-compose.yml
|-- configs/
|   |-- default.toml                 # section 7
|   |-- world_small.toml             # overrides: small preset
|   |-- world_medium.toml
|   |-- world_large.toml
|   |-- features.toml                # feature registry flags: priority P0/P1 per feature, disabled list for ablations
|   |-- robustness.toml              # world overrides for the robustness grid (section 14.6)
|   `-- reasons.toml                 # reason-code texts and conditions (section 12.2)
|-- chainwatch/
|   |-- __init__.py                  # __version__
|   |-- __main__.py                  # python -m chainwatch -> cli.main()
|   |-- cli.py                       # sub-commands (section 18.2)
|   |-- config.py                    # load TOML + env overrides + validation, config hash
|   |-- schema.py                    # Event, Decision, Reason, RingAlert (pydantic) + slim dataclasses
|   |-- data/
|   |   |-- canonical.py             # canonical columns, validators, sort order
|   |   |-- io.py                    # read/write csv.gz event streams (stdlib csv + gzip)
|   |   |-- paysim.py                # PaySim adapter (section 8.3)
|   |   |-- organizer.py             # organiser dataset adapter, filled in at kickoff (section 8.4)
|   |   `-- world/
|   |       |-- population.py        # accounts and archetypes
|   |       |-- background.py        # legitimate activity
|   |       |-- typologies.py        # mule ring campaigns
|   |       |-- balances.py          # balance simulation and consistency
|   |       `-- generate.py          # orchestration + world_report.json
|   |-- features/
|   |   |-- registry.py              # feature names, order, groups, defaults, priority
|   |   |-- state.py                 # AccountState, window counters, FIFO credits
|   |   |-- engine.py                # FeatureEngine.step(event) -> dict
|   |   |-- matrix.py                # replay a stream through FeatureEngine and write feature matrices (section 9.9)
|   |   `-- reference.py            # slow brute-force implementation (tests only)
|   |-- model/
|   |   |-- train.py                 # LightGBM training for B0, B1, G2 (train env)
|   |   |-- export.py                # LightGBM dump -> portable JSON
|   |   |-- portable_gbdt.py         # pure-Python evaluator (serve env)
|   |   |-- vector_predict.py        # numpy evaluator of the same JSON for evaluation at scale (train env only)
|   |   |-- thresholds.py            # FPR-based thresholds from validation
|   |   `-- profile.py               # benign percentiles for reasons
|   |-- rings/
|   |   |-- grouping.py              # incremental ring grouping
|   |   `-- scoring.py               # ring score
|   |-- decision/
|   |   |-- tiers.py                 # allow / verify / hold
|   |   `-- reasons.py               # reason codes
|   |-- serving/
|   |   |-- engine.py                # ScoringEngine (section 4 per-event flow)
|   |   |-- inject.py                # pure-Python mini campaign generator for live injection (section 13.2)
|   |   |-- app.py                   # FastAPI app factory
|   |   |-- routes.py                # REST endpoints
|   |   |-- ws.py                    # WebSocket hub
|   |   |-- replay.py                # replay controller and live injection
|   |   |-- metrics.py               # latency histogram, throughput counters
|   |   |-- security.py              # optional API key, token-bucket rate limit
|   |   `-- static/                  # index.html, app.js, style.css, vendor/cytoscape.min.js (+ LICENSE)
|   `-- eval/
|       |-- splits.py                # time-based splits
|       |-- metrics.py               # PR-AUC, recall@FPR, ring metrics, time-to-catch
|       |-- protocol.py              # runs detectors over worlds and seeds
|       |-- ablation.py              # B0, B1, G2 with and without ring logic
|       |-- robustness.py            # stress grid
|       |-- leakage.py               # leakage checks (section 14.7)
|       |-- parity.py                # cross-platform decision parity
|       `-- report.py                # figures, tables, template rendering
|-- bench/
|   |-- engine_bench.py              # in-process throughput and stage timings
|   |-- load_test.py                 # HTTP load test (asyncio + httpx)
|   `-- platform_info.py             # writes results/platform_<host>.json
|-- scripts/
|   |-- check_platform.py            # run on laptop and on LinuxONE (section 16.4)
|   |-- check_platform.sh            # bash wrapper: uname, lscpu, docker, ports
|   |-- profile_paysim.py            # section 3.1 checks
|   |-- verify_models.py             # recompute model-package hashes and registry hash (make verify-models)
|   |-- run_native.sh                # venv + uvicorn, no Docker
|   |-- build_vendor_assets.sh
|   `-- check_claims.py              # fails if a number in docs lacks a results source
|-- tests/                           # section 17
|-- data/                            # git-ignored except data/replay and data/sample
|   |-- raw/                         # paysim/, organizer/
|   |-- interim/
|   |-- processed/                   # features_*.npz, events_*.csv.gz
|   |-- replay/                      # demo.csv.gz, demo_accounts.csv.gz, demo_rings.json (committed, under 10 MB)
|   `-- sample/                      # tiny fixtures for tests
|-- models/                          # committed model package (section 10.6)
|-- results/                         # committed JSON + figures
|-- reports/                         # generated markdown and figures
`-- docs/
    |-- STATUS.md                    # live status, decisions, known issues (section 19.4)
    |-- decisions.md                 # decision log (date, decision, reason)
    |-- architecture.md              # diagram + module notes
    |-- report_template.md           # report with {{placeholders}} (section 20.3)
    |-- allowed_numbers.txt          # numbers allowed in prose, each with its source (section 20.3)
    |-- model_card.md
    |-- data_card.md
    |-- linuxone_runbook.md          # exact commands used on the LinuxONE box
    |-- demo_script.md               # 3-minute demo (section 20.5)
    `-- deck_outline.md              # slide outline with {{placeholders}} (section 20.4)
```

---

## 7. Configuration

All tunables live in `configs/default.toml`; preset files override only what differs. `config.py` loads TOML (`tomllib` or `tomli`), applies env overrides of the form `CHAINWATCH__SECTION__KEY=value`, validates ranges, and computes `config_hash` (SHA-256 of the canonical JSON of the merged config). The hash is written into every output file's metadata.

```toml
[project]
name = "chainwatch"
seed = 2026
tz_offset_s = 19800            # IST; used only for t_hour
currency = "INR"

[world]                        # ChainWorld, section 8.2
preset = "medium"              # small | medium | large (preset files override counts below)
days = 30
t0_weekday = 0                 # 0 = Monday at sim time 0
n_customers = 60000
n_merchants = 3000
n_traders = 400
n_salary_payers = 150
n_landlords = 1500
n_agents = 500                 # cash-out agents (legitimate, high fan-in and fan-out)
n_campaigns = 140              # mule-ring campaigns over the whole period
fraud_target_share = [0.0008, 0.004]   # sanity band: ring txns / all txns
upi_txn_cap_inr = 100000       # illustrative per-transaction cap, UNVERIFIED against NPCI
structuring_cap_inr = 49999    # illustrative cap used by the structuring typology
hard_negative_boost = 1.0      # multiplies hard-negative archetype activity
noise_level = 1.0              # 0..2, scales background randomness

[world.mix]                    # typology weights, must sum to 1
funnel = 0.35
layered_chain = 0.25
split_merge = 0.15
round_trip = 0.05
structuring = 0.12
slow_roll = 0.08

[features]
windows_s = [600, 3600, 86400]
fifo_lookback_s = 86400
state_ttl_s = 172800           # evict accounts idle this long
max_lateness_s = 5
amount_history_n = 20
dwell_ewma_alpha = 0.3
dwell_default_s = 86400
trail_depth_cap = 8
trail_min_share = 0.10
funnel_cap = 50
susp_matched_share_min = 0.7
susp_dwell_max_s = 900
susp_min_in_uniq_1h = 2
susp_ttl_s = 3600
flow_edge_min_matched = 0.5
flow_rebuild_every_s = 300
flow_comp_cap = 200
hub_degree_cap = 30            # accounts with more distinct 1h counterparties than this are hubs and never joined into flow components
nbr_scan_cap = 50              # scan at most this many most-recent distinct neighbours for neighbour features
round_min_amount = 1000
near_cap_inr = 49999           # t_near_cap: amount in [near_cap_lo_frac x cap, cap]; illustrative, UNVERIFIED as a real limit
near_cap_lo_frac = 0.85
deque_cap = 500                # max events kept per account per direction; window counters saturate beyond this
payee_memory_n = 200           # counterparties remembered per account (LRU); older ones count as "new" again
flow_window_s = 3600           # flow edges older than this are dropped at each rebuild
priority = "P1"               # P0 = core subset only, P1 = all features

[model]
objective = "binary"
n_estimators = 600
learning_rate = 0.05
num_leaves = 31
max_depth = 8                  # bounds per-event evaluation cost in the pure-Python evaluator
ship_max_trees = 300           # cap for exported models (latency budget); accuracy impact is reported
min_child_samples = 50
subsample = 0.8
subsample_freq = 1
colsample_bytree = 0.8
reg_lambda = 1.0
early_stopping_rounds = 50
neg_downsample = 0.2           # keep 20% of legitimate rows when training; weights compensate
use_missing = false            # features never contain NaN; keeps the portable evaluator simple
zero_as_missing = false
deterministic = true
n_jobs = 4
detectors = ["B0", "B1", "G2"]

[thresholds]
fpr_verify = 0.005             # share of legitimate test transactions allowed to be "verify" or above
fpr_hold = 0.0005              # share allowed to be "hold"
fpr_edge = 0.005               # suspicious-edge threshold for ring grouping
max_false_rings_per_day = 5    # ring alert threshold is chosen to respect this on validation
false_ring_purity = 0.5        # a ring is "false" when fewer than this share of its flagged edges are fraud transactions
calibrate_with_rings = true    # P1: bisect base thresholds so FPR after ring escalation matches the target
bisect_iters = 12

[rings]
min_accounts = 3
min_flagged_edges = 2
link_window_s = 3600
ttl_s = 21600
rebuild_every_s = 300
top_k_noisy_or = 5
require_pass_node = true       # a ring needs at least one relay account (flagged money in and flagged money out)
hub_degree_cap = 30            # an account with more distinct flagged counterparties than this never links edges
terminal_types = ["CASH_OUT", "PAYMENT", "DEBIT"]   # the receiver of such an edge is an exit and does not link other edges
escalation = true
escalate_out_to = "hold"       # a transaction DEBITING a member of an alerting ring is raised to at least this tier
escalate_in_to = "verify"      # a transaction CREDITING such a member is raised to at least this tier
max_edges_in_alert = 200
update_delta = 0.1             # emit a ring "update" event when ring_score grows by this much or membership changes

[serving]
host = "0.0.0.0"
port = 8000
workers = 1                    # stateful engine, keep 1 (rule E8)
compare_mode = false           # if true, also evaluate B0 and B1 per event (about 3x model cost); the demo toggles it
ws_batch_ms = 250
max_batch = 500
request_id_lru = 100000
api_key_env = "CHAINWATCH_API_KEY"   # if this env var is set, X-API-Key is required on /v1
cors_origins = []
rate_limit_rps = 0             # 0 = off
latency_hist_max_ms = 2000

[replay]
dataset = "data/replay/demo.csv.gz"
accounts = "data/replay/demo_accounts.csv.gz"
rings = "data/replay/demo_rings.json"   # ground-truth rings of the demo world (UI overlay and "next campaign" seek only)
warmup_until_ts = 259200       # 3 days: events before this are processed silently at start-up
default_speed = 300.0          # simulated seconds per wall-clock second
inject_max_per_minute = 6      # cap on live-injected campaigns
inject_time_scale = 0.5        # multiplies typology delays for injected campaigns so they unfold in minutes

[eval]
seeds = [0, 1, 2, 3, 4]
split_days = [15, 20, 30]      # train [0,15), validation [15,20), test [20,30)
burn_in_days = 3               # events in the first days build state but are excluded from training and scoring metrics
bootstrap = 2000
fpr_points = [0.0001, 0.001, 0.01]
catch_min_accounts = 2         # a ring is "caught" when this many distinct ring accounts have an action
catch_min_action = "verify"    # ... of at least this tier

[bench]
engine_events = 200000
load_requests = 20000
load_concurrency = [1, 8, 32, 64]
warmup_requests = 1000
```

World presets (override `[world]`):

| Preset | customers | merchants | traders | salary payers | landlords | agents | campaigns | expected events |
|---|---|---|---|---|---|---|---|---|
| small | 6,000 | 300 | 40 | 15 | 150 | 50 | 40 | about 0.15 to 0.25 million (for tests and fast iteration) |
| medium | 60,000 | 3,000 | 400 | 150 | 1,500 | 500 | 140 | about 1 to 2 million (main experiments) |
| large | 300,000 | 12,000 | 1,500 | 600 | 7,000 | 2,000 | 600 | about 5 to 8 million (PaySim-scale stress; optional) |

The generator prints the real event count; the table is only an expectation.
