<!-- ChainWatch specification, §14 to §16. Part of CLAUDE.md; section numbers are global. -->

## 14. Evaluation protocol

Everything here runs in the train env (§5.1) on precomputed feature matrices (§9.9). Evaluation uses **the shipped model files**: `model/vector_predict.py` (numpy, train env only) evaluates the exported JSON trees in vectorised form, so the scores that are evaluated come from the same JSON the API loads. A parity test (§17) ties it to `PortableGBDT` and to LightGBM.

### 14.1 What is evaluated on which data

| Data | Transaction-level | Ring-level | Notes |
|---|---|---|---|
| ChainWorld (5 seeds, medium) | yes | yes | Primary. All headline claims come from here, labelled "synthetic" |
| PaySim | yes | no | No ring ground truth; sparse graph. Used for transparency and to test the balance-leakage expectation |
| Organizer data | yes if labels exist | only if rings can be defined | Decide at kickoff (§8.4) |

### 14.2 Protocol (per world seed)

1. `chainwatch world --preset medium --seed s` (frozen, §8.2.5), `chainwatch features`, `chainwatch train` (B0, B1, G2), `chainwatch thresholds` (all six variants, §10.4 and §11.6). Everything fitted on train or validation only.
2. `chainwatch evaluate` scores the **test** split `[20, 30)` days with each detector, applies tiers (and the ring layer for `+R`), then computes the metrics in §14.3. The first `burn_in_days` days never enter any metric.
3. **Test-touch log.** `evaluate` appends `{world_hash, model_hash, code_commit, utc}` to `results/test_touch_log.jsonl` and refuses to run again for the same `(world_hash, model_hash)` unless `--allow-rerun` is passed (the rerun is logged and flagged in the report). This keeps "the test set is touched once per run" (rule H5) visible in git.
4. Aggregate over seeds into `results/summary.json` (§14.4).

Variants evaluated (rows of every results table): `RULE` (the rule-based suspicion flag of §9.7 used as a binary alarm; context only), `B0`, `B0+R`, `B1`, `B1+R`, `G2`, `G2+R`. **Headline comparison:** `B0` versus `G2+R` (the PDF's "one-payment-at-a-time versus ring-aware"), always shown together with `B1` and `B1+R` so the result cannot be read as beating a straw man.

**Same false-alarm rate (the PDF promise).** All non-`RULE` variants are compared at the same final tier false-alarm rate: the share of legitimate test transactions whose final tier is `verify` or `hold`. Thresholds come from validation (§10.4, §11.6); the realised value on test is printed in every table. If a variant's realised FPR differs from the target by more than 10% relative, the table marks it and the claim sentence must mention it.

### 14.3 Metrics

**Transaction level** (positives: `is_fraud = 1`; negatives: `is_fraud = 0`):
- **PR-AUC** (average precision) and **ROC-AUC**, from the raw scores (no thresholds). For `+R` variants use the effective score after escalation.
- **Recall at fixed FPR** for `fpr_points = [1e-4, 1e-3, 1e-2]` in two forms: (a) *curve form*: threshold chosen on the **test** negatives to hit the FPR exactly (an optimistic, threshold-free comparison; label it as such); (b) *deployed form*: validation-fixed thresholds, reporting the realised FPR and the recall at that point (the headline).
- **Alert volume**: verify and hold alerts per 10,000 transactions.

**Ring level** (ChainWorld; only rings whose first transaction lies in the test split, §8.5):
- **Caught.** A ring is caught when at least `catch_min_accounts` (2) distinct **mule or collector** accounts of that ring (ground-truth roles `mule` and `collector`; victims and sink agents do not count) have appeared as `src` or `dst` of a transaction whose **final** tier is at least `catch_min_action` (`verify`). The catch time `tau` is the timestamp of the transaction at which the second such account appears. **Ring recall** = caught rings / rings.
- **Time to catch** = `tau - first transaction ts of the ring`, in minutes. Report median and interquartile range over caught rings plus the share caught within 15 minutes, 1 hour and ever. Rings never caught are shown as such, not dropped silently.
- **Caught before exit.** `exit_ts` = timestamp of the ring's first transaction with `dst_role = sink`. Report the share caught with `tau < exit_ts`, and `minutes_before_exit = exit_ts - tau` (negative means after exit). Rings without a sink transaction are excluded from this metric and counted.
- **Hops before exit** (the PDF's early-warning measure): `exit_hop` = hop index of the first sink transaction; `max_done(tau)` = highest hop index among ring transactions with `ts <= tau`; `hops_remaining = exit_hop - max_done(tau)`. Larger is earlier; zero or less means the exit had already happened.
- **Cash-out prevented share, as bounds, not as fact.** For each ring, `sink_amount` = sum of amounts of ring transactions with `dst_role = sink`. `prevented_upper` = share of `sink_amount` carried by transactions after `tau` (assumes any action after the catch could stop later exits). `prevented_lower` = share carried by sink transactions whose own final tier is `hold` (assumes only holds stop money). Present both with the sentence "assumption-based estimates on synthetic data"; never convert into rupee savings claims.
- **False rings per day.** Ring alerts whose purity (share of flagged edges with `is_fraud = 1`) is below `false_ring_purity`, per simulated test day; and the number of legitimate accounts touched by alerting rings per day.
- **Breakdowns:** by typology (the six in §8.2.3) and by ring size (accounts: up to 4, 5 to 8, more than 8).

**Impact on legitimate accounts (always reported):** account-level false-alert rate by archetype: for each archetype (customer, merchant, trader, agent, salary payer, landlord, family hub, new, dormant), the share of accounts with at least one final `verify` or `hold` in the test period, and per account-day. This shows who bears the false alarms (expected: agents and traders), which matters for the "false alarms hurt real people" limit in the PDF.

### 14.4 Statistics

- Per seed: exact counts, Wilson 95% intervals for proportions.
- Across seeds: mean, minimum and maximum of the per-seed values (five seeds).
- Pooled: bootstrap (`eval.bootstrap = 2000`) resampling **rings** with replacement for ring-level metrics; for transaction-level metrics resample contiguous 6-hour blocks of the test period (preserves temporal correlation). Fix the bootstrap seed in config.
- Differences between variants (for example `G2+R` minus `B1+R`) use the **paired** version of the same resampling, plus for binary catch outcomes the two discordant counts (caught by one and not the other). Language rule: say "the interval excludes zero" or "includes zero"; do not report p-values.
- Always print `n` (rings, positives, negatives) next to every proportion.

### 14.5 Ablations (`eval/ablation.py`, results in `results/ablation_<world_tag>.json`)

| Id | Change | Question |
|---|---|---|
| A1 | Detector ladder B0, B1, G2 (without rings) | What does account history add? What do graph features add? |
| A2 | Ladder with rings (`+R`) | What does the ring layer add on top of each scorer? |
| A3 | G2 without the neighbour features (`g_nbr_*`, `g_dst_nbr_*`, `g_src_susp`, `g_dst_susp`) | Is neighbour suspicion doing the work? (retrain) |
| A4 | G2 without trail and funnel (`g_trail_depth`, `g_funnel_in`) | Do trail features matter? (retrain) |
| A5 | G2 without flow components (`g_flow_*`) | Are components needed? (retrain) |
| A6 | `G2+R` with `require_pass_node = false` | Is the relay requirement helping? |
| A7 | `G2+R` with `escalation = false` | Escalation versus grouping alone |
| A8 | `G2+R` with `link_window_s` times 0.5 and times 2 | Sensitivity to the link window |
| A9 | `G2+R` with `top_k_noisy_or` in {1, 3, 5} | Sensitivity to the ring score aggregation |
| A10 | P0 feature set versus P1 | Cost of the minimal feature set |

A3 to A5 retrain (same hyperparameters, same seeds). A6 to A9 reuse the scores and only change the ring layer; for each of these, recalibrate `ring_alert` and thresholds on validation (§11.6) so the comparison stays at matched FPR.

### 14.6 Robustness grid (`eval/robustness.py`, `configs/robustness.toml`)

Models trained on the default medium world are **not retrained**; new small-preset worlds (2 seeds each) are generated with one change at a time and scored. For each cell report ring recall, deployed recall and realised FPR, median time to catch, for `B1+R` and `G2+R`.

| Id | Change | Values |
|---|---|---|
| R1 | `noise_level` | 0.5, 1.0, 1.5, 2.0 |
| R2 | `hard_negative_boost` | 1, 2, 4 |
| R3 | `slow_roll` share of the typology mix (others rescaled) | 0.08, 0.30 |
| R4 | Mule profile mix | default; all mules `active` (unwitting) |
| R5 | Typology delays | times 0.5 (faster rings), times 2 (slower rings) |
| R6 | Held-out typology | train without `round_trip`, test with it (needs an extra training run) |

Expected, to be tested rather than assumed: performance degrades as noise and hard negatives rise; `slow_roll` is the hardest; windows that are shorter than the ring's delays cause misses. Report all cells, including the bad ones.

### 14.7 Leakage checks (`eval/leakage.py`; shown in the write-up, PDF commitment 8)

| Id | Check | Pass criterion |
|---|---|---|
| L1 | Temporal integrity: max train `ts` is below min validation `ts`, which is below min test `ts`; causality test of the feature engine (§9.10) passes | exact |
| L2 | **ID-only model:** train LightGBM on features derived only from the account id string (numeric part, length, first characters) | PR-AUC within 2x of the positive rate (about chance); also report temporal ID overlap and a validation sensitivity with IDs remapped to unseen random values |
| L3 | **Label-shuffle control:** shuffle training labels, retrain G2, evaluate on validation | PR-AUC within 2x of the positive rate; use a fixed training-round budget so true validation labels cannot select the iteration |
| L4 | **Single-feature screen:** AUC of each feature alone on validation | List the top 10; any feature above 0.98 gets a manual note in `docs/decisions.md` |
| L5 | **Importance concentration:** gain share per feature group; on PaySim, share of the balance-derived features | Report only (the PaySim expectation in §8.3) |
| L6 | **Account reuse:** share of test-ring mule accounts that were mules in train rings; metrics on the subset of test rings with entirely unseen accounts | Report; the subset must not be much worse than the whole |
| L7 | **Threshold hygiene:** `thresholds.json` says `split = validation`, the world hash matches the training world | exact |
| L8 | **Burn-in:** no metric row has `ts` below the burn-in end | exact |
| L9 | **No label fields in the serving path:** `EventIn` rejects label fields; the engine input type has none (test in §17) | exact |

Results are written to `results/leakage_<world_tag>.json` and printed as a table in the report. A failure of L1, L7, L8 or L9 blocks the release. A failure of L2 or L3 means a bug: stop and investigate before any further results are produced. Add a **canary test** (in `tests/`, not in the report): inject a deliberately leaky feature (for example the next event's label) into a copy of the pipeline and assert that L3-style or L4-style screens flag it. This proves the checks can detect a leak.

### 14.8 Cross-platform parity (`eval/parity.py`, `chainwatch parity`)

On each platform run the same fixed slice (20,000 events of the demo world after warm-up) through `ScoringEngine` and write `results/parity_<host_tag>.jsonl` with one line per event: `event_id`, `decision`, `score` (Python `repr`), `base_score`, `ring_id`. A comparison command (`chainwatch parity --compare A.jsonl B.jsonl`) reports the number of lines compared, the number with differing `decision`, the maximum absolute score difference, and the first differing line. **Expected** (to be confirmed, not claimed in advance): identical decisions and scores equal to within 1e-9 because all arithmetic is IEEE-754 double in the same order; differences, if any, come from the C maths library (`exp`, `log1p`) and must be reported as measured. QEMU-emulated runs count as a byte-order test, not as a performance or LinuxONE result.

### 14.9 Outputs and figures

- `results/eval_<world_tag>.json` per world with keys `meta` (world, provenance, config and model hashes, counts), `transaction_level.<variant>`, `ring_level.<variant>` (including `by_typology`, `by_size`), `archetype_fpr.<variant>`, `operating_points.<variant>`.
- `results/summary.json`: per-variant aggregates over seeds, bootstrap intervals, paired differences, and the exact sentences allowed by the claims policy (filled by the report step).
- Figures in `reports/figures/` (PNG, 150 dpi, every title includes the data provenance): precision-recall curves; recall versus FPR (log x-axis); ring recall by typology with intervals; ECDF of time to catch; histogram of hops remaining at catch; false rings per day versus ring-alert threshold; account-level false-alert rate by archetype; robustness heatmap; engine and HTTP latency distributions (from §15).
- `reports/REPORT.md` is generated by `chainwatch report` from `docs/report_template.md` (numbers via templating, §20.3) and must contain: setup, data cards, results tables, ablations, robustness, leakage table, benchmarks, limits, "where ChainWatch does not help".

### 14.10 Pre-registered expectations (write to `docs/decisions.md` **before** the first test-split run)

1. `B1` is much better than `B0` on transaction-level recall at matched FPR (account history matters).
2. `G2+R` catches more rings than `B1+R` at matched FPR, and catches them earlier (hops remaining and minutes before exit). This is the central hypothesis and may fail.
3. `slow_roll` is the hardest typology for every variant.
4. Agents and traders carry the highest account-level false-alert rates.
5. On PaySim, per-transaction models score very high mostly through sender balance fields (§8.3), and graph features add little because accounts rarely repeat.
6. The ring layer alone (`B0+R`) cannot rescue a weak scorer.

Report each as confirmed, partly confirmed or refuted, with the numbers. A refuted central hypothesis is reported plainly in the README and the deck (rule H1) and the story shifts to what was learned (for example "ring logic helps earliness but not recall").

---

## 15. Benchmarks

The PDF promised response time as median and 95th percentile plus throughput, **on LinuxONE and on a laptop**. This section produces those numbers; nothing here is a claim until a results file exists.

### 15.1 Platform record (`bench/platform_info.py`)

`python bench/platform_info.py --host-tag laptop|linuxone` writes `results/platform_<host_tag>.json`: `platform.machine()`, `platform.system()`, `platform.release()`, `sys.byteorder`, `sys.version`, Python implementation, CPU count, `lscpu` text (if available), `/proc/meminfo` total, whether running in a container (`/.dockerenv`), `docker --version` if present, the installed serving packages with versions (`pip list --format=json`), git commit, config hash, and a UTC timestamp. It never stores hostnames, user names or IP addresses (rule D1); `host_tag` is chosen by the person running it.

### 15.2 In-process engine benchmark (`bench/engine_bench.py`)

```bash
python bench/engine_bench.py --host-tag laptop --events 200000 --reps 3 [--compare] [--priority P0|P1]
```

- Loads the model package, builds `ScoringEngine`, and streams `bench.engine_events` events from a fixed slice (test split of `world_medium_seed0`, converted to `Event`s in memory). The first `warmup_requests` events are not measured.
- Per event, records `features`, `model`, `decision` and `total` in nanoseconds (`perf_counter_ns`); also records throughput = measured events divided by measured wall time, and process resident memory at the end and the engine's account counts.
- Runs `--reps` repetitions with a fresh engine each time; reports the **median of repetitions** for p50, p95, p99, max and events per second, and the min and max across repetitions.
- Modes: default (G2+R only), `--compare` (B0, B1 and G2 scored per event), and `--priority P0` for the minimal feature set.
- Output `results/bench_engine_<host_tag>.json`; also prints a compact table.
- Measurement hygiene: no other heavy process; record the Python version; fix `PYTHONHASHSEED=0`; do not trim outliers; garbage collection is left at defaults and this is stated in the report.

### 15.3 HTTP load test (`bench/load_test.py`)

```bash
python bench/load_test.py --host-tag laptop --base-url http://127.0.0.1:8000 --requests 20000 --concurrency 1 8 32 64
```

(The defaults of `--requests` and `--concurrency` come from `[bench].load_requests` and `[bench].load_concurrency`.)

- Uses `httpx.AsyncClient` (pure Python). Sends `POST /v1/score` with events from the fixed slice; after a warm-up of `warmup_requests`. Also measures `POST /v1/score/batch` at batch sizes 1, 10 and 100 at one concurrency level.
- Concurrent requests arrive slightly out of order, so start the server with `CHAINWATCH__FEATURES__MAX_LATENESS_S=3600` for this test (documented in the output). Detection quality is not measured in this test; ordering only affects the exact feature values, not the cost.
- Records client-side latency per request (p50, p95, p99, max), requests per second, error count, and the server-reported `/v1/metrics` engine latency before and after, so engine versus end-to-end latency can be shown side by side.
- Runs on the same machine as the server (localhost) because the load generator and the service share a box in our setup; say so in the report, since network latency is excluded.
- Output `results/bench_load_<host_tag>.json`.

### 15.4 Comparison rules (laptop versus LinuxONE)

- Same git commit, same model package, same event slice, same Python minor version if possible (record if not).
- Record vCPU count and memory of both machines. Do not conclude that one platform is "faster"; state what was measured, with the hardware description. Single-thread pure-Python speed depends on the core, and virtual-server sizes differ.
- Measure both **inside Docker** and **native venv** on each machine when both are available, so container overhead is separated from platform differences. Label each result file accordingly (`--host-tag laptop-docker`, `linuxone-native`, and so on).
- QEMU-emulated s390x results are never reported as performance.
- Present results as a table: rows = platform and mode; columns = engine p50, p95, p99 (microseconds), events per second, end-to-end p50, p95 at concurrency 1 and 32, memory.

### 15.5 Targets (expectations to aim for; not claims, UNVERIFIED until measured)

Engine p95 below about 5 ms per transaction in G2+R mode and sustained throughput of at least several hundred events per second per process on a laptop. If these are missed: follow the optimisation order in §9.10 and §10.3 (single-pass window scans, compile trees to `if/else`, `ship_max_trees`), re-measure, and report the final numbers whatever they are. Do not remove features to hit a target without recording the accuracy cost in `docs/decisions.md`.

### 15.6 Scaling notes (for the README "limits" section)

The engine is a stateful single process. It scales by making it faster, not by adding workers. Sharding by account is not straightforward because each transaction touches two accounts that may live on different shards; a real deployment would need a partitioning scheme with cross-shard state exchange or a graph store. We do not implement or claim this.

---

## 16. Deployment

Targets: (a) laptop, native venv or Docker; (b) IBM LinuxONE (s390x) via the hackathon environment. How we get access (a provisioned virtual server over SSH, an IBM Cloud account, or something else) is **UNVERIFIED until kickoff**; this section covers each case.

### 16.1 Images and files

`Dockerfile` (serve env only; no numpy, no LightGBM):

```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONHASHSEED=0 PIP_NO_CACHE_DIR=1
WORKDIR /app
ARG REQS=requirements-serve.txt
COPY ${REQS} ./requirements.txt
RUN pip install --only-binary=:all: -r requirements.txt
COPY pyproject.toml ./
COPY chainwatch ./chainwatch
COPY configs ./configs
COPY models ./models
COPY data/replay ./data/replay
RUN useradd --uid 10001 --no-create-home chainwatch
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"
CMD ["python", "-m", "chainwatch", "serve", "--host", "0.0.0.0", "--port", "8000"]
```

- `--only-binary=:all:` makes pip fail early, with a clear message, if any dependency lacks a wheel for the platform instead of silently compiling for ages. The default `REQS` is the unpinned `requirements-serve.txt`; the lock file is used on the laptop (`--build-arg REQS=requirements-serve.lock`) because a pinned version might lack an s390x wheel.
- `Dockerfile.ubuntu` is the fallback if `python:3.12-slim` has no s390x variant (UNVERIFIED, §3.2): `FROM ubuntu:22.04`, `apt-get install -y python3 python3-venv python3-pip`, create `/opt/venv`, install requirements there, same copy steps, same `CMD`. This uses Python 3.10, which is why serving code must support 3.10 (rule E1).
- `docker-compose.yml`: one service `chainwatch`, `build: .`, `ports: ["8000:8000"]`, `restart: unless-stopped`, `environment: [CHAINWATCH_API_KEY]` (value passed through from the host env, blank by default), `read_only: true`, `tmpfs: [/tmp]`, same healthcheck.
- `.dockerignore`: `.git`, `data/raw`, `data/interim`, `data/processed`, `results`, `reports`, `tests`, `bench`, `docs`, `.venv`, `__pycache__`. The build context stays small (replay data under 10 MB plus models).

### 16.2 Make targets for deployment (details in §18)

`make docker-build`, `make docker-run`, `make docker-stop`, `make docker-s390x` (cross-build with buildx and QEMU, §16.5), `make wheelhouse-s390x`, `make bundle`.

### 16.3 LinuxONE runbook (also written to `docs/linuxone_runbook.md` with the real outputs pasted in as it is executed)

1. **Connect and look around** (`bash scripts/check_platform.sh`): `uname -m` (expect `s390x`), `lscpu`, `cat /etc/os-release`, `python3 --version`, `docker --version` and `docker info`, `free -m`, `df -h`, listening ports (`ss -ltn`), outbound reachability of PyPI and GitHub (HEAD request with a 5 s timeout), then `python3 scripts/check_platform.py` for the structured verdict (§16.4). Paste the output into the runbook.
2. **Get the code onto the machine.** If GitHub is reachable: `git clone`. Otherwise build the bundle on the laptop (`make bundle` creates `dist/chainwatch-bundle.tar.gz` containing the repo without raw data) and `scp` it. Never copy trained pickles (there are none) or raw datasets.
3. **Choose the run path.**
   - *Docker present (or installable):* `docker build -t chainwatch:latest .` (needs outbound access for `pip` and the base image); if the box has no outbound access, build the s390x image on the laptop (§16.5) and `docker save chainwatch:s390x | gzip > chainwatch-s390x.tar.gz`, `scp`, then `docker load`. Run `docker run -d --name chainwatch -p 8000:8000 --restart unless-stopped chainwatch:latest`. If Docker is not installed and the person has sudo, follow the IBM tutorial route (Docker CE from Docker's Ubuntu APT repository plus the Compose plugin); do not improvise a different method without noting it.
   - *No Docker:* native path `bash scripts/run_native.sh` (creates `.venv`, installs serve requirements, starts uvicorn on port 8000). On Ubuntu 22.04 you may need `sudo apt-get install -y python3-venv`. If `pip` has no outbound access, use the wheelhouse: build it on the laptop with `make wheelhouse-s390x` (runs `pip download --only-binary=:all: --platform manylinux2014_s390x --platform manylinux_2_17_s390x --platform manylinux_2_28_s390x --python-version <box python> --implementation cp --abi cp<ver> -d wheelhouse-s390x -r requirements-serve.txt`; UNVERIFIED that every platform tag resolves; try the tags that work), copy it over, then `pip install --no-index --find-links wheelhouse-s390x -r requirements-serve.txt`.
4. **Check it is alive:** `curl -s localhost:8000/healthz`; `curl -s localhost:8000/v1/info` should show `"machine": "s390x"` and `"byteorder": "big"`.
5. **Reach the dashboard.** Preferred: SSH tunnel from the laptop, `ssh -L 8000:localhost:8000 <user>@<host>` then open `http://localhost:8000` (works with no open ports). Alternative: open port 8000 in the instance's security group and use the floating IP (as in the IBM VPC tutorial); in that case set `CHAINWATCH_API_KEY` first.
6. **Parity, then benchmarks, then copy results back:**
   `python -m chainwatch parity --out results/parity_linuxone.jsonl`, `python bench/platform_info.py --host-tag linuxone`, `python bench/engine_bench.py --host-tag linuxone`, `python bench/load_test.py --host-tag linuxone ...` (needs `httpx`, pure Python: `pip install httpx` if reachable), `scp` the `results/*linuxone*` files to the laptop, compare with `chainwatch parity --compare`, commit.
7. **Record everything** in `docs/linuxone_runbook.md` and `docs/STATUS.md`: exact commands, versions, outputs, and any deviation from this plan.

### 16.4 `scripts/check_platform.py` (stdlib only)

Prints one line per check with `OK`, `WARN` or `FAIL`, and writes `results/platform_check_<host_tag>.json`. Checks: machine and byte order; Python version (WARN below 3.10); `pip` present; serve packages importable (and versions); `docker` present and daemon reachable; user can run docker; port 8000 free (bind test); CPU count and memory; free disk; outbound HTTPS to `pypi.org` and `github.com` (5 s timeout, never fatal); wheel availability test `pip download --only-binary=:all: --no-deps -d <tmp> pydantic-core` (skipped when offline); clock sanity. Ends with a **NEXT STEP** line, for example "Docker works: run `make docker-run`" or "No Docker: run `bash scripts/run_native.sh`" or "No outbound access: use the wheelhouse".

### 16.5 Cross-building and the QEMU rehearsal (works before kickoff, on the laptop)

```bash
docker run --privileged --rm tonistiigi/binfmt --install s390x          # one-time: register QEMU handlers
docker run --rm --platform linux/s390x python:3.12-slim python -c "import platform,sys;print(platform.machine(), sys.byteorder)"   # expect: s390x big
docker buildx build --platform linux/s390x -t chainwatch:s390x --load .
docker run --rm --platform linux/s390x -v "$PWD/results:/app/results" chainwatch:s390x python -m chainwatch parity --out results/parity_qemu-s390x.jsonl
```

If the first or second command fails because the image has no s390x variant, switch to `Dockerfile.ubuntu` (`docker buildx build --platform linux/s390x -f Dockerfile.ubuntu ...`). The QEMU run proves byte-order safety and dependency availability; its timings are meaningless and must never appear as performance numbers. Mark the resulting file `qemu`.

### 16.6 Running without LinuxONE

If access is delayed or missing, complete everything on the laptop, include the QEMU parity result, and say in the README, deck and video exactly what was and was not run on LinuxONE. Do not fabricate or imply LinuxONE measurements.

### 16.7 Operational notes

Run as a non-root user; logs go to stdout; the process is single-worker (rule E8); the container restarts on failure; the API key (optional) comes from the environment; `docker logs chainwatch` is the first place to look when something fails. Keep the demo container running through judging and pin it with `--restart unless-stopped`. Record the container image digest and git commit in `docs/STATUS.md` at submission time so the demo can be reproduced.
