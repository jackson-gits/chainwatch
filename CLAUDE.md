# CLAUDE.md: ChainWatch, real-time mule-ring detection for UPI-style payment streams

Build specification for our IBM Z Datathon 2026 entry (24-hour event, Oct 17-18, 2026; track: **Real-Time AI for Critical Decisions**).
Read this file before writing code, then the spec files in `docs/spec/` for the sections you work on. Together they are long on purpose: they are meant to let you build the project end to end without asking.
Project codename `ChainWatch` (Python package `chainwatch`) is a placeholder; rename freely, but keep names consistent.

Status markers used below: **[VERIFIED]** = checked against a source on 2026-10-14 to 2026-10-16, **[UNVERIFIED]** = could not be confirmed, check it yourself before relying on it, **[DECISION]** = a design choice already made (do not re-litigate without telling the user why).

## Contents

0. Read this first
1. Mission, context and commitments we already made
2. Hard rules
3. Verified facts and open unknowns
4. Architecture
5. Tech stack and dependency policy
6. Repository layout
7. Configuration
8. Data
9. Feature engine
10. Models
11. Rings
12. Decisions and reason codes
13. Serving API and dashboard
14. Evaluation protocol
15. Benchmarks
16. Deployment
17. Testing plan
18. Make targets and CLI
19. Build plan and timeline
20. Deliverables
21. Definition of done
22. Troubleshooting playbook
23. Appendix

---

## Where the rest of the specification lives

This file holds sections 0 to 3: context, commitments, hard rules and verified facts. Everything else is split by topic into `docs/spec/`. Section numbers (§) are global, so a reference such as §10.3 means section 10.3 in whichever file contains it. Read this file first, then open the spec file for the part you are working on (do not load them all at once).

| Sections | File | What it contains |
|---|---|---|
| §4 to §7 | `docs/spec/04-07_architecture_stack_layout_config.md` | Architecture, tech stack and dependency policy, repository layout, full `default.toml`. Read before creating the repo skeleton, or when touching config, dependencies or layout. |
| §8 | `docs/spec/08_data.md` | Canonical schema, chainworld simulator, paysim and organiser adapters, splits and leakage-related data rules. Read when building or using any data code. |
| §9 to §11 | `docs/spec/09-11_features_models_rings.md` | Feature engine (all 59 features), models and portable json format, thresholds, ring grouping and escalation. Read when working on features, training, scoring or rings. |
| §12 to §13 | `docs/spec/12-13_decisions_api_dashboard.md` | Tiers, reason codes, rest/websocket api, replay, injection, dashboard. Read when working on decisions, the API or the UI. |
| §14 to §16 | `docs/spec/14-16_evaluation_benchmarks_deployment.md` | Evaluation protocol, metrics, ablations, leakage checks, benchmarks, docker and the linuxone runbook. Read when evaluating, benchmarking or deploying. |
| §17 to §19 | `docs/spec/17-19_testing_make_plan.md` | Test plan, makefile and cli, build plan, checkpoints and cut list, status.md template. Read when writing tests, wiring commands or planning the work. |
| §20 to §23 | `docs/spec/20-23_deliverables_done_troubleshooting_appendix.md` | Readme and docs, templating and claims check, deck and demo, definition of done, troubleshooting, sources, glossary, judge q&a. Read when writing docs, preparing the demo or debugging. |

---

## 0. Read this first

**What we are building.** A streaming system that watches payments as they happen, keeps a short-term picture of who is paying whom, and links "mule" accounts into **rings**: accounts that receive scam money from many people and pass it on within minutes. Every alert comes with plain-language reasons and a recommended action (allow, verify, hold) for a human analyst. A small API runs in a container on IBM LinuxONE (s390x) and we measure its latency and throughput.

**The one claim we want to be able to defend** (only if the measured results support it): at the same false-alarm rate, a ring-aware detector finds more mule rings, and finds them earlier, than a one-payment-at-a-time model, on the same data and the same time-based split. If the data says otherwise, we report that instead (see §2, rule H1).

**Decisions already made [DECISION]:**

1. **Serving path is pure Python.** No numpy, pandas, scikit-learn or LightGBM at serve time. Reason [VERIFIED]: PyPI publishes no s390x wheels for the latest numpy, scipy, pandas, scikit-learn, pyarrow, lightgbm, xgboost, matplotlib, shap, numba, uvloop, httptools or orjson (see §5). Installing them on LinuxONE would mean compiling from source.
2. **Train on x86 (laptop or Colab), serve anywhere.** The model is trained with LightGBM, exported to a portable JSON format, and evaluated by our own pure-Python tree evaluator (`portable_gbdt.py`). This also removes the big-endian byte-order trap that bit two earlier IBM Z Datathon winners (their blog posts describe models giving nonsense output on LinuxONE until they fixed byte order).
3. **Primary benchmark is a controlled synthetic world ("ChainWorld")**, calibrated to PaySim's marginals where available. PaySim is run as a secondary public benchmark. Reason: PaySim's native fraud is shallow (a takeover, a transfer, a cash-out) and its balance fields are widely reported to leak the label, so it cannot test whether ring structure adds anything. We say this openly in the write-up.
4. **Honest evaluation.** Strong baselines (not strawmen), time-based split, thresholds fixed on validation, 5 world seeds, bootstrap intervals, ablations, robustness grid, leakage checks. Negative results are reported.
5. **Recommendation only.** The system suggests allow, verify or hold with reasons. It never freezes anything automatically and never accuses a person. Mule account holders are often victims themselves.

**Reading order for you:** §2 (rules) then §8 to §12 (what to build) then §13 (API and UI) then §14 (how we judge ourselves) then §19 (order of work). Everything else is reference.

**First 10 minutes checklist:**

1. Read this file fully.
2. Check `docs/STATUS.md` exists; if not, create it from the template in §19.4.
3. Ask the user the questions in §0.1 (in one message, with the defaults shown).
4. Run `make check-platform` once the skeleton exists (§16.4).
5. Follow the phase order in §19.

### 0.1 Questions to ask the user at the start (offer defaults, do not block on them)

| # | Question | Default if no answer |
|---|---|---|
| 1 | Solo or team? Names to put in README and LICENSE? | Owner: Jackson Raj A (GitHub: jackson-gits); ask before listing anyone else |
| 2 | Do we have LinuxONE (L1CC) access working? Which OS image, Python version, is Docker installed? | Assume unknown; run `scripts/check_platform.py` on the box and read the result |
| 3 | Did the organisers publish problem statements, a dataset, a submission format and a deadline at kickoff? | Assume none; keep the project as specified |
| 4 | Does the Official Rules page allow starting work before the window opens? | Assume NO: only environment, access and data-download setup before kickoff (see §19.1) |
| 5 | License for the public repo? | MIT |
| 6 | Public GitHub repo name and visibility? | `chainwatch`, public at submission time |
| 7 | Online or in-person participation (affects demo logistics)? | Online |

---

## 1. Mission, context and commitments we already made

### 1.1 Event context

- **[VERIFIED]** IBM Z Datathon 2026 is run by the Shooting Stars Foundation with IBM. Dates: Oct 17-18, 2026. It is a 24-hour global student challenge. The official page lists 16,000+ registrants (2026), 45+ countries, $50,000+ in prizes.
- **[VERIFIED]** 2026 themes: (1) *Real-Time AI for Critical Decisions* ("Detect. Decide. Respond."), (2) *AI Secured: Innovation Without Exposure*, (3) *Wildcard: Beyond the Obvious*. We are in theme 1.
- **[VERIFIED]** Schedule on the official page: LinuxONE setup and tech-prep session Oct 16 at 7:30 PM IST; global virtual kickoff Oct 17, 9:00-10:00 AM IST. An IBM community newsletter mentions IBM Z Day on Nov 18, 2026, and a third-party listing calls it the project showcase.
- **[VERIFIED]** The official page lists regional awards, including an **APAC Regional Champion** (top university team in Asia-Pacific), plus university-level awards.
- **[UNVERIFIED]** Official judging criteria, the exact submission format and the exact deadline. The Official Rules page could not be read by our tooling. Use as a proxy only the rubric of an earlier IBM Z internal hackathon: challenge and feasibility, innovation and originality, value and usability, technical features. Confirm everything at kickoff and record it in `docs/STATUS.md`.
- **[VERIFIED]** Past winners (blog posts and news about earlier editions) built: a BERT hate-speech detector, a symptom-based disease classifier, a chest X-ray healthy/unhealthy classifier, a CNN lake-encroachment tracker, a farm-production ML tool. Common pattern: a real local problem, standard tools, one clear output, deployed on LinuxONE, mentors afterwards. Our edge: streaming, graph-aware, explainable, with measured latency.

### 1.2 Our submitted form answers (keep the project consistent with these)

**Problem statement:**

> When a UPI scam happens, the money rarely stays put. It hops through borrowed "mule" accounts within minutes, and checking payments one by one misses the pattern. Banks need to see the whole chain.

**Proposed solution:**

> We want to catch the whole chain, not just one suspicious payment. Our system watches payments as they happen and keeps a short-term picture of who is paying whom. When an account suddenly receives money from many people and passes almost all of it on within minutes, that's a classic mule pattern, so we link such accounts together into a ring.
>
> Every alert comes with plain reasons, for example "passed on most of the money within minutes to several accounts", so a fraud analyst can understand and trust it. Alongside that, it suggests an action: allow, ask for extra verification, or hold.
>
> We'll compare it with a normal one-payment-at-a-time model on the same data, to show how many more cases the network view catches and how much earlier. It runs as a small API in a container on IBM LinuxONE, and we'll measure how quickly it responds. Everything we test on is synthetic, so no real person's data is touched.

**Audience:** (1) fraud and risk teams at banks and UPI apps; (2) everyday customers, including people whose accounts get quietly used as mule accounts; (3) cybercrime investigators; (4) compliance analysts and regulators who need an auditable record of why an account was flagged.

**Datasets:** PaySim (public synthetic mobile-money data) plus our own synthetic mule-ring scenarios with different ring sizes and background noise; the organisers' dataset instead, if one is provided at kickoff; no real customer or personal data.

### 1.3 Commitments made in the supporting PDF we uploaded (the build MUST honour each)

The PDF is `UPI_Mule_Ring_Detection_Supporting_Document.pdf` (3 pages). It makes no performance claims. It promises:

1. **Stream replay** of transactions in time order, one at a time.
2. A **rolling time-window** picture of who pays whom (bounded state).
3. **Signals per account**, limited to what the dataset contains: how much received money is passed on, how soon after it arrives, how many distinct senders and receivers appear in the window, how deep the chain is, how many neighbouring accounts already look suspicious. **No device or IP signals.**
4. A **standard gradient-boosted model** scores; accounts that are suspicious and connected are **grouped into one ring**.
5. **Suggested action**: allow, verify or hold for human review. A recommendation, never an automatic punishment.
6. **Plain-language reasons** per alert (how quickly money was passed on, to how many accounts).
7. A **small API in a container on IBM LinuxONE**; response time measured as median and 95th percentile, plus throughput, on LinuxONE and on a laptop.
8. **Evaluation**: same data, same time-based train/test split; a one-payment-at-a-time model versus our ring-aware version; same false-alarm rate; ring-level and early-warning measures (minutes or hops before money leaves the system); a **leakage check shown in the write-up**.
9. **Data**: PaySim; our own synthetic ring scenarios with the write-up saying which results come from which data; the organisers' dataset if provided; PaySim's row counts and columns checked before use.
10. **Limits** stated: synthetic data is not real life; false alarms hurt real people including tricked account holders; detection support only; no personal data.
11. We **do not claim to beat RBI's MuleHunter.AI** and have not compared against it.

### 1.4 Claims policy (applies to README, deck, video, report, commit messages)

Allowed: "On our synthetic benchmark (N worlds, time-based split), at the same false-alarm rate, the ring-aware detector found X% of rings versus Y% for [baseline]" with X and Y read from `results/*.json`.

Forbidden: any claim of real-world UPI performance; "beats MuleHunter.AI" or any comparison with it; "production-ready"; rupee savings presented as fact (savings only as clearly labelled assumption-based estimates); "detects mule accounts in real UPI" ; invented or rounded-up numbers; any statistic not traceable to a file in `results/` or to a cited source in §23.1.

---

## 2. Hard rules

**Honesty (H):**

- **H1.** Never edit, round up, cherry-pick or invent results. Every number in README, report, deck and UI comes from a file in `results/` via the templating in §20.3. If the ring-aware detector does not beat the baseline on a metric, say so and explain.
- **H2.** Baselines must be strong: B1 (per-account history features) is a fair "industry-style" baseline, B0 (single-transaction) is the weak reference. Do not weaken B1 to flatter the result.
- **H3.** Always label data provenance: every table and figure states `ChainWorld`, `PaySim` or `Organizer`.
- **H4.** Anything not checked is marked `UNVERIFIED` in docs. Do not state unchecked facts as facts.
- **H5.** Thresholds are chosen on the validation split only. The test split is touched once per experiment run for reporting. No tuning on test.

**Data and safety (D):**

- **D1.** Synthetic or public data only. No real names, phone numbers, UPI IDs, Aadhaar or PAN numbers, account numbers, device IDs or IPs anywhere, including test fixtures and screenshots. Account IDs are opaque strings (e.g. `A00012345`). Account ID prefixes must carry no meaning in ChainWorld; never use an ID as a feature.
- **D2.** The system recommends; humans decide. UI wording is neutral ("account shows a mule-like pattern"), never accusatory. No automatic freezing, blocking or reporting.
- **D3.** Serving makes no outbound network calls. It must run fully offline once installed.
- **D4.** No secrets in git. Use `.env` (ignored) and `.env.example`. API key (optional) via `CHAINWATCH_API_KEY`.
- **D5.** Do not use protected or demographic attributes; none exist in the data and none may be added.

**Engineering (E):**

- **E1.** Python 3.10 to 3.13 compatible. Serving code must run on Python 3.10 (Ubuntu 22.04 default). No `type` statements, no PEP 695 generics, no 3.12-only syntax. Use `tomllib` if present, else `tomli`.
- **E2.** Serving dependencies are limited to those in `requirements-serve.txt` (§5.2). Adding a dependency to the serving path needs a note in `docs/decisions.md` and a check that it installs on s390x without compiling.
- **E3.** Determinism: every random draw uses an explicit seed (`random.Random(seed)` and `numpy.random.default_rng(seed)`). The same config and seed must reproduce the same world, features, model and results.
- **E4.** One feature implementation: the streaming `FeatureEngine` (§9) generates both training data (by replaying the stream) and serving features. No separate pandas feature code that could drift from serving.
- **E5.** Never copy trained pickles or other binary model files across architectures. The model format is JSON only (§10.3).
- **E6.** Type hints on public functions; short docstrings; `ruff` clean; tests for every module (§17).
- **E7.** Commit small and often with clear messages. Keep `docs/STATUS.md` current (§19.4) so a human can resume at any time.
- **E8.** The serving engine is single-threaded and stateful: run one uvicorn worker. Guard `process()` with a lock.
- **E9.** Do not start long-running jobs without logging progress and expected duration. Prefer `--small` flags for quick iteration.

**Process (P):**

- **P1.** Walking skeleton first (§19): get world_small to engine to model to API working end to end, then deepen.
- **P2.** Ask the user only when blocked by a decision that changes deliverables. Otherwise pick the default in this file and record it in `docs/decisions.md`.
- **P3.** Before the kickoff window opens, do only what §19.1 allows.

---

## 3. Verified facts and open unknowns

### 3.1 PaySim (public dataset)

**[VERIFIED]** from the dataset description (mirrored in a GitHub README of the Kaggle text):

| Column | Meaning |
|---|---|
| `step` | One step is one hour of simulated time; 744 steps (30 days) |
| `type` | `CASH-IN`, `CASH-OUT`, `DEBIT`, `PAYMENT`, `TRANSFER` as documented (the CSV is believed to write `CASH_IN` and `CASH_OUT` with underscores; accept both spellings and normalise on load) |
| `amount` | Amount in local currency |
| `nameOrig` | Customer who started the transaction |
| `oldbalanceOrg`, `newbalanceOrig` | Sender balance before and after |
| `nameDest` | Recipient customer |
| `oldbalanceDest`, `newbalanceDest` | Recipient balance before and after; **no data for merchants** (names starting with `M`) |
| `isFraud` | Transactions by fraudulent agents who take control of customer accounts, move funds to another account, then cash out |
| `isFlaggedFraud` | The simulated business rule flags an illegal attempt: a single transfer above 200,000 |

Source for the PaySim paper: Lopez-Rojas, Elmir, Axelsson, "PaySim: A financial mobile money simulator for fraud detection", EMSS 2016, pp. 249-255. The abstract says it simulates mobile money transactions "based on an original dataset".

**[UNVERIFIED], check at load time with `scripts/profile_paysim.py` and write results to `results/paysim_profile.json`:** row count (we believe about 6.36 million), number of fraud rows (we believe about 8 thousand, roughly 0.13%), number of unique `nameOrig` and `nameDest` (we believe nearly every sender is unique, which makes the account graph extremely sparse), the exact Kaggle file name, the licence on the Kaggle page, whether fraud only occurs in `TRANSFER` and `CASH_OUT`, and how predictive the balance fields are. The download needs a free Kaggle account; if you cannot fetch it, ask the user to download `paysim1` manually into `data/raw/paysim/` (never commit it; it is about 470 MB).

### 3.2 Platform facts

- **[VERIFIED] PyPI wheels for s390x** (checked 2026-10-16, latest release of each package only): none for numpy, scipy, pandas, scikit-learn, pyarrow, lightgbm, xgboost, matplotlib, shap, numba, psutil, pillow, uvloop, httptools, orjson. Present: pydantic-core, websockets (cp311 and newer, pure fallback also exists), PyYAML, kiwisolver, contourpy. Pure Python: pydantic, fastapi, uvicorn, networkx, httpx, joblib, threadpoolctl, pytest, anyio, h11, click. Older releases or other indexes may differ.
- **[VERIFIED]** IBM's public Python wheel repository documented in an IBM Community post covers **ppc64le (Power) only**, not s390x.
- **[VERIFIED]** An IBM Developer tutorial for LinuxONE virtual servers says the instance image has no container runtime; Docker CE is installed from Docker's Ubuntu APT repository, plus the Compose plugin; example image `ibm-ubuntu-22-04-3-s390x-1` (Ubuntu 22.04, s390x); external access needs a security group and a floating IP. **That tutorial is for IBM Cloud virtual servers and may not match the Community Cloud (L1CC) exactly.**
- **[VERIFIED, secondary]** Earlier winners' blog posts say their L1CC instance came pre-configured with Docker and a pre-built image with TensorFlow, PyTorch and scikit-learn; the same code ran on the big-endian s390x architecture after they fixed byte order or upgraded PyTorch.
- **[UNVERIFIED]** For 2026: whether Docker is preinstalled on our L1CC instance; the Python version; whether outbound internet (PyPI, GitHub) works from the instance; whether inbound ports are open; CPU, RAM and disk limits; instance lifetime; whether `apt` can install `python3-numpy`. `scripts/check_platform.py` (§16.4) checks all of these.
- **[UNVERIFIED]** That the `python:3.12-slim` official image has an s390x variant. Docker Hub could not be reached from our sandbox. If it does not, use `Dockerfile.ubuntu` (§16.1).

### 3.3 Things the organisers will tell us at kickoff (record in `docs/STATUS.md`)

Problem statements for the track; any provided dataset and its licence; submission format (repo link, video, deck, form); deadline in IST; judging criteria and weights; rules on allowed libraries and platforms; whether LinuxONE use is mandatory (the 2026 page says challenges are "powered by IBM Z", so assume we should demonstrate it).
