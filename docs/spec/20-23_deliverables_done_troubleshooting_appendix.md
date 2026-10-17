<!-- ChainWatch specification, §20 to §23. Part of CLAUDE.md; section numbers are global. -->

## 20. Deliverables

### 20.1 README (`README.template.md` rendered to `README.md`)

Edit the template, never the rendered file. Sections, in order:
1. One-line description and a screenshot or GIF of the dashboard showing a ring.
2. **The problem** in three sentences, using only facts from §23.1 with their numbers and sources.
3. **What it does:** stream replay, rolling-window picture, per-account signals, ring grouping, allow/verify/hold with reasons.
4. **Results** (all numbers via `{{ }}` placeholders): the generated headline sentences (§20.3), the main table (variants `B0`, `B1`, `B1+R`, `G2`, `G2+R` at matched FPR, with realised FPR shown), a time-to-catch line, a hops-before-exit line, where it does not help (typology breakdown), and the leakage-check summary. Every table says "ChainWorld (synthetic)" or "PaySim".
5. **On IBM LinuxONE and on a laptop:** latency and throughput table (engine and end-to-end), parity sentence, platform badge screenshot. Exactly what was and was not run on LinuxONE (§16.6).
6. **Quick start:** Docker (`make docker-build docker-run`), native (`bash scripts/run_native.sh`), the dashboard URL, the three shortcut keys.
7. **How it works:** architecture figure (§4), feature groups (§9.2), model format (§10.3), ring layer (§11) in short.
8. **Design decisions:** the reasons from §5.4 (no numpy at serve time, JSON models, byte order).
9. **Data and provenance:** ChainWorld, PaySim, organiser data if used; links to the data card.
10. **Limits and responsible use** (from the PDF's limits, §1.3 item 10, plus the simulator caveat and the scaling note in §15.6).
11. **Reproduce everything:** the exact `make` commands and seeds.
12. **Sources** (§23.1) and licence.

### 20.2 Documents in `docs/`

- `model_card.md`: model details (gradient-boosted trees, feature counts, training world, hyperparameters from the train reports), intended use (research prototype for decision support), out-of-scope uses (automatic blocking, use on real customers without validation), data, metrics (templated), ethical considerations (false alarms harm real people, mule account holders are often victims, human review, no demographic or device attributes), limitations, how to retrain.
- `data_card.md`: ChainWorld datasheet (generator, archetypes, parameters, every assumption marked "illustrative"; what is not modelled: devices, IPs, geography, SIM swaps, real UPI semantics, regulation), calibration protocol (B0 and B1 only; frozen before G2 results), PaySim section (citation [8][9], row counts from `results/paysim_profile.json`, the balance-leak caveat and what the profile showed, licence as stated on the Kaggle page, which is UNVERIFIED until read), organiser data section if any.
- `architecture.md`: the diagram of §4, module list with one line each, data flow, the two environments.
- `decisions.md`: dated log (what, why), including pre-registered expectations (§14.10) **before** the first test-split run, calibration choices, any deviation from this file.
- `STATUS.md` (§19.4), `linuxone_runbook.md` (§16.3), `demo_script.md` and `deck_outline.md` (below).

### 20.3 Templating and the claims check (rule H1)

**Placeholders.** Templates use `{{ namespace.path.to.key | filter }}`. `chainwatch report` loads: `summary.*` from `results/summary.json`; `bench.<host_tag>.*` from `results/bench_*_<host_tag>.json`; `platform.<host_tag>.*`; `paysim.*` from `results/paysim_profile.json` and PaySim evaluation files; `leak.*` from `results/leakage_*.json`; `world.*` from `results/world_manifest.json`. Filters: `int` (thousands separators), `num1`, `num2`, `pct0` (0.834 gives `83%`), `pct1` (`83.4%`), `min1` (seconds to minutes with one decimal), `us0` (microseconds, integer), `ms1`, `ci` (`{lo, hi}` to `(95% interval 71% to 88%)`), `pp1` (difference in percentage points, signed). A missing key, an unknown filter or an unresolved placeholder **fails the build** and lists all problems; there are no silent blanks.

**Generated markers.** Rendered values are wrapped in `<!--r:namespace.path-->value<!--/r-->` (invisible in Markdown), so the checker can tell generated numbers from hand-typed ones.

**Generated claim sentences** (`eval/report.py`, keys under `summary.claim.*`) choose their wording from the data, not from hope:
- Compare `G2+R` with each other variant using the paired interval on ring recall at matched FPR: interval above zero gives "found more", interval containing zero gives "found a similar share", interval below zero gives "found fewer". The same logic applies to time to catch (earlier, similar, later) and to deployed transaction-level recall.
- Example output: "On our synthetic benchmark (5 worlds, time-based split), at the same false-alarm rate (0.5% of legitimate transactions), the ring-aware detector found 71% of rings versus 38% for the one-payment-at-a-time model and 58% for the account-history baseline with ring grouping." (numbers here are illustrative of the format only).
- If the central hypothesis fails, the generated sentence says so, and the README's results section is not allowed to override it by hand.

**`scripts/check_claims.py`** scans `README.md`, `reports/REPORT.md`, `docs/model_card.md`, `docs/data_card.md`, `docs/architecture.md`, `docs/demo_script.md`, `docs/deck_outline.md`. It ignores fenced code blocks and generated regions, and fails when:
1. a percentage, a decimal number, a currency amount or an integer of 100 or more appears outside generated regions and is not listed in `docs/allowed_numbers.txt` (format `token | source | note`; for example the §23.1 figures with their reference numbers, event dates, counts of features that the registry test checks);
2. a forbidden phrase appears (case-insensitive): "production-ready", "production ready", "state of the art", "state-of-the-art", "outperforms", "beats MuleHunter", "better than MuleHunter", "better than RBI", "real-world accuracy", "real world accuracy", "detects mule accounts in real", "saves Rs", "saves INR", "saves ₹";
3. a paragraph mentions MuleHunter without also containing "do not claim" or "have not compared" (the PDF's wording);
4. a Markdown table in the README or report lacks a provenance word (ChainWorld, PaySim or Organizer) in its caption or the line above it.

### 20.4 Deck outline (`docs/deck_outline.md`; slides contain placeholders, not numbers)

1. **Title:** ChainWatch, one-line promise, team, "Real-Time AI for Critical Decisions", IBM Z Datathon 2026.
2. **Problem:** UPI scale and fraud figures from §23.1 (with sources on the slide); money hops through mule accounts within minutes; one-payment checks miss it; mule holders are often victims.
3. **Idea:** a ring view of who pays whom over a rolling window; picture of entry, relay, exit.
4. **How it works:** the architecture diagram, stream to features to model to tiers to reasons to rings; "recommendation only".
5. **How we tested it honestly:** synthetic benchmark with realistic confusers, time-based split, strong baseline, same false-alarm rate, 5 worlds, leakage checks; PaySim cross-check and why it cannot test rings.
6. **Results:** `{{claim.*}}` sentences; ring recall, time to catch, hops before exit, with intervals; matched FPR shown.
7. **Where it fails and who pays for false alarms:** typology breakdown (slow roll), archetype false-alert rates, limits.
8. **Live demo:** screenshot of a ring forming with reasons.
9. **IBM LinuxONE:** platform badge, same code and JSON model on big-endian s390x, parity sentence, latency and throughput table for both machines.
10. **Responsible use and limits:** synthetic data, human in the loop, no personal data, what we do not claim (no real-world numbers, no comparison with MuleHunter.AI).
11. **What next:** real feedback loop, partner data under agreements, graph store and sharding, more signals where lawful.

### 20.5 Demo script (`docs/demo_script.md`, 3 minutes; rehearse until it fits)

| Time | Show | Say (short) |
|---|---|---|
| 0:00 | Dashboard, replay running at 300x | "Every payment here is synthetic. Each row is a decision made in the moment." |
| 0:20 | Platform badge (`s390x`, big-endian) and latency strip | "This is the same code running on IBM LinuxONE; median and 95th-percentile times are on screen." |
| 0:40 | Press `i` (inject a `funnel` ring) | "Watch for money arriving from many people into new accounts and being passed on within minutes." |
| 1:10 | Graph forms; ring panel shows alert and three reasons | "These accounts are linked into one ring. The reasons are plain language. It recommends hold; a person decides." |
| 1:40 | Comparison panel | "A one-payment-at-a-time model flagged this ring later, or not at all. Here are the times." (read from screen; only if the panel shows it) |
| 2:10 | Results slide | Read the generated headline sentence exactly as rendered. |
| 2:40 | Limits slide | "Synthetic data, recommendations only, false alarms hurt real people, entry accounts may be tricked." |

Prepare: a list of injection seeds that make a clear demo (found in rehearsal, recorded in the script); a pre-recorded fallback video; the dashboard served over the SSH tunnel; a second browser tab on the laptop-hosted instance. Never improvise a number on stage.

### 20.6 Video (2 to 3 minutes)

Screen recording of the dashboard following the demo script, voice-over from the same text, captions optional. Show the platform badge and the results slide. No claims beyond the rendered sentences. Export at 1080p; keep the file under any size limit the submission form states (check at kickoff).

### 20.7 Repository hygiene at submission

`LICENSE` present; `README.md` rendered; no `data/raw`, `data/processed`, `.env`, secrets or large binaries; `data/replay` under 10 MB; `models/default` committed; `results/` committed (JSON and figures, no raw data); CI green; tag `v1.0-submission`; `make check-claims` and `make verify-models` pass; the container image digest and the git commit written in `docs/STATUS.md`.

### 20.8 Submission checklist (fill the specifics at kickoff)

- [ ] Repository link public and working from a logged-out browser.
- [ ] Deck uploaded in the required format; video uploaded or linked.
- [ ] Form fields consistent with §1.2 (problem, solution, audience, datasets), updated if the project changed.
- [ ] Supporting PDF (optional) consistent with what was built; if something it promised is missing, either build it or leave the PDF out of the submission.
- [ ] Demo instance running on LinuxONE and reachable the way the judges expect; fallback video ready.
- [ ] Every number in every public file traces to `results/` or §23.1.

---

## 21. Definition of done

**Functional**
- [ ] F1. `POST /v1/score` returns a decision with up to three reasons, on the laptop and on LinuxONE (or the blocker is documented).
- [ ] F2. Stream replay runs in the dashboard; a ring appears with role-coloured graph, score, reasons and recommended action.
- [ ] F3. Injection works for all six typologies.
- [ ] F4. Serving image and venv contain no numpy, pandas, scikit-learn or LightGBM; the import-guard test passes.
- [ ] F5. Decisions and scores are identical (or within the measured tolerance) across platforms, shown by the parity report.

**Evaluation**
- [ ] E1. Five frozen medium worlds; detectors B0, B1, G2 with and without ring logic evaluated at matched FPR on the test split.
- [ ] E2. Ablations A1 to A5, leakage checks L1 to L9 (all pass or explained), time-to-catch and hops-before-exit reported with intervals.
- [ ] E3. PaySim profile written; PaySim evaluation done or explicitly skipped with the reason.
- [ ] E4. Pre-registered expectations resolved (confirmed, partly, refuted) in `docs/decisions.md`.
- [ ] E5. Benchmarks recorded for the laptop and LinuxONE (engine and end-to-end), with platform records.

**Documentation and claims**
- [ ] D1. README, model card, data card, architecture, runbook, demo script, deck outline, report all rendered.
- [ ] D2. `make check-claims` passes; no forbidden phrases; PDF commitments in §1.3 all satisfied or the PDF is withdrawn.
- [ ] D3. Limits stated: synthetic data is not real life; false alarms hurt people; detection support only; no personal data; scaling limit; no comparison with MuleHunter.AI.

**Engineering**
- [ ] G1. `make lint` and `make test-all` clean; CI green.
- [ ] G2. Deterministic: rerunning the pipeline on one seed reproduces hashes.
- [ ] G3. `docs/STATUS.md` current; tags set; container digest recorded.

**Demo**
- [ ] M1. Rehearsed three times within three minutes; fallback video exists; the SSH tunnel or public URL has been tested from a second device.

---

## 22. Troubleshooting playbook

| Symptom | Likely cause | What to do |
|---|---|---|
| `pip` tries to compile something on s390x or fails with "no matching distribution" | A dependency without an s390x wheel (numpy, uvloop, httptools, orjson, pydantic-core pinned to an old version) | Use `requirements-serve.txt` (unpinned) with `--only-binary=:all:`; make sure you installed `uvicorn`, not `uvicorn[standard]`; check §3.2; use the wheelhouse route (§16.3); if `pydantic-core` is missing, note it in `docs/decisions.md` and ask the user before changing the stack |
| `docker pull` or `buildx` says no matching manifest for `linux/s390x` | The base image has no s390x variant | Use `Dockerfile.ubuntu` (§16.1) |
| Container starts but the page is unreachable | Port not published, firewall or security group, or server bound to localhost | `-p 8000:8000`, `--host 0.0.0.0`, SSH tunnel (§16.3 step 5) |
| `ModuleNotFoundError: tomllib` | Python 3.10 | `pip install tomli`; the code must try `tomllib` then `tomli` (rule E1) |
| `SyntaxError` on LinuxONE | A 3.11+ construct slipped in | Run `tests/serving/test_py310_syntax.py`; rewrite the construct |
| Parity test fails by a tiny amount | LightGBM stores initial score separately, or threshold printed with low precision | Check the exporter (§10.3): add `base_score` if needed; make sure thresholds are not rounded; confirm `use_missing=False` |
| Parity fails for some rows only | Feature value exactly at a split boundary computed differently (float32 somewhere) | Find the float32 conversion and remove it (§9.1 item 5) |
| Equivalence test (engine vs reference) fails | Off-by-one at a window boundary, wrong ordering of ties, FIFO not committed, or the current event inserted too early | Compare the failing feature on a hand-built stream; check §9.1 items 7 and 9 and the apply order in §9.5 |
| Feature replay is far too slow | Repeated deque scans, `statistics.median` on big lists, string building in loops | Profile with `cProfile`; one-pass window scans; cap histories; see §9.10 |
| Memory grows without bound | TTL sweep not running, deques without `maxlen`, light registry growth | Check the hourly sweep and caps; log `engine.stats()`; document the registry growth rate |
| One giant ring appears | A hub or sink account is linking unrelated edges | Check terminal types, `hub_degree_cap`, `link_window_s`; look at the ring's account degrees |
| No rings alert | `ring_alert` too high, edges threshold too high, or pass-node requirement unmet | Print the validation `peak_score` distribution of true rings; check `edge` threshold; check that relays are detected |
| Ring false-alarm budget cannot be met | Detector too weak or hard negatives too many | Report it; do not loosen the budget silently; look at which archetypes form false rings |
| Training: too few positives | World too small or campaigns too few in the train window | Use a larger preset or raise `n_campaigns`; never duplicate positives to fake it |
| G2 is not better than B1 | The graph features add nothing in this world, or the world has no ring structure beyond account behaviour | Report it (rule H1); inspect feature importance and the typology breakdown; do not retune the world after seeing G2 (§8.2.5) |
| `evaluate` refuses to run | Test already touched for this model and world (§14.2) | Use `--allow-rerun` only for a genuine fix; it is logged and flagged |
| PaySim file missing or too big for memory | Not downloaded; reading all rows at once | Put the CSV in `data/raw/paysim/`; read in 500,000-row chunks |
| PaySim per-transaction score is "too good" | Balance fields leak | This is the expected finding; report it (§8.3) |
| WebSocket drops or the feed stalls | Slow client, queue overflow, proxy buffering | Check the `dropped` counter; reduce speed; connect directly or through the SSH tunnel |
| Cytoscape missing in the page | Vendor file not built | Run `scripts/build_vendor_assets.sh`; the dashboard must work offline |
| HTTP 409 `late_event` during load tests | Concurrent requests reorder events | Start the server with a larger `max_lateness_s` (§15.3) |
| Different results between two runs | Unseeded randomness, set iteration order, multi-thread nondeterminism in LightGBM | Find the unseeded source (rule E3); use `deterministic=True` and fixed threads; `PYTHONHASHSEED=0` |
| `check_claims` fails on a number | Hand-typed figure | Replace with a placeholder or add to `allowed_numbers.txt` with its source (§20.3) |
| LinuxONE session or instance expired | Time-limited access | Keep the runbook current; rebuild from `make bundle`; keep the fallback video |
| Clock or timezone confusion in `t_hour` | `ts` zero is 00:00 UTC (§9.1 item 6) | Check `tz_offset_s`; verify the generator uses the same convention |

---

## 23. Appendix

### 23.1 Sourced facts for README, deck and report (taken from the supporting PDF; accessed 14 October 2026)

Where a figure comes from a news report of official data, the report says so, because the original government releases were not reached. Do not add numbers to public docs that are not in this table, §3 or `results/`.

| What | Figure | Source |
|---|---|---|
| UPI, September 2026 | About 24.07 billion transactions worth about Rs 29.37 lakh crore, roughly 802 million a day; volume up about 23% on a year earlier | [1] |
| Financial cyber-fraud complaints, Jan to Jun 2026 | More than 12.71 lakh complaints; about Rs 10,178.97 crore reported lost | [2] [3] |
| Held or frozen by banks, same period | Rs 2,968.85 crore, about 29% of the reported loss | [2] [3] |
| Tamil Nadu, same period | Rs 897.79 crore reported lost, third-highest among states by value | [2] [3] |
| Longer run, 2021 to May 2026 | About Rs 64,447 crore reported lost; about Rs 10,718 crore frozen; about Rs 323 crore refunded to victims (roughly 0.5% of the loss, by our own arithmetic) | [3] only |

Qualitative statements from the sources: mule accounts are used to receive and move illicit money, often without the holder understanding what is happening, and people are often recruited through fake job offers or quick-income messages [4]; common signals include money passed on quickly, many unrelated senders, dormant accounts suddenly busy [4] [5]; RBI's MuleHunter.AI is described in the press without published performance figures [6]; an RTI reply shows 23 banks had implemented it by 10 December 2025 while the RBI declined to say how many mule accounts it has identified [7]. **We do not claim our prototype beats it and have not compared against it.**

References:
1. DD India, report on NPCI's September 2026 UPI data (published 1 October 2026): https://ddindia.co.in/2026/10/upi-transactions-surge-23-to-24-07-billion-in-september-npci/
2. Outlook Money, cyber-fraud complaints January to June 2026 (as reported from a Ministry of Home Affairs meeting): https://www.outlookmoney.com/news/cyber-fraud-complaints-continue-to-rise-maharashtra-records-highest-losses
3. Anantamias, "Cyber fraud complaints cross 12.7 lakh in six months, with over Rs 10,000 crore lost": https://anantamias.com/current-affairs/cyber-fraud-complaints-12-7-lakh-six-months/
4. Bajaj Finance, "Understanding Mule Accounts: Risks and Prevention Strategies" (undated): https://www.bajajfinserv.in/mule-account-risks-and-prevention
5. Sphinx, "What Is a Mule Account? Detection Signals, Investigation Steps, and Reporting" (updated 4 August 2026): https://sphinxhq.com/blog-posts/what-is-a-mule-account-detection-investigation
6. Business Standard, "What is MuleHunter.AI? RBI's latest tool against financial fraud explained" (6 December 2024): https://www.business-standard.com/amp/finance/news/what-is-mulehunter-ai-rbi-s-latest-tool-against-financial-fraud-explained-124120600865_1.html
7. MediaNama, report on the RBI's reply to an RTI request about MuleHunter.AI (30 December 2025): https://www.medianama.com/2025/12/223-rti-23-banks-mulehunter-mule-accounts/
8. E. A. Lopez-Rojas, A. Elmir and S. Axelsson, "PaySim: A financial mobile money simulator for fraud detection", Proceedings of the European Modeling and Simulation Symposium (EMSS), 2016, pp. 249-255: https://msc-les.org/proceedings/emss/emss2016/emss2016_249.html
9. Kaggle, "Synthetic Financial Datasets For Fraud Detection" (PaySim): https://www.kaggle.com/datasets/ealaxi/paysim1

### 23.2 Glossary

- **Mule account:** an account used to receive and pass on money from scams; the holder may be complicit or tricked.
- **UPI:** India's real-time payment system. We never use real UPI data.
- **Entry / relay / exit:** the roles of accounts in a ring, inferred from flagged transfers: money enters, is passed on, and leaves (cash-out or merchant payment).
- **Pass-through ratio:** money sent on divided by money received in a window.
- **Dwell time:** how long received money stays before being sent on.
- **FIFO matching:** assigning an outgoing payment to the oldest unspent incoming credits to estimate dwell and trail depth.
- **Trail depth:** how many relays the money has already passed through.
- **Funnel width:** how many different origins feed one outgoing payment.
- **FPR (false-alarm rate):** the share of legitimate transactions that raise an alert.
- **PR-AUC:** area under the precision-recall curve (average precision); sensitive to rare positives.
- **Recall at FPR:** share of fraud found at a fixed false-alarm rate.
- **Matched FPR:** variants compared after calibration to the same final false-alarm rate.
- **Time-based split:** train on earlier days, validate on the next, test on the latest; never shuffled.
- **Noisy-OR:** combining independent evidence as `1 - product(1 - p)`.
- **Burn-in:** initial days that build state but are excluded from training and metrics.
- **s390x / big-endian:** IBM Z processor architecture; multi-byte numbers are stored most significant byte first, which breaks naive binary model files.
- **LinuxONE / L1CC:** IBM's Linux platform on IBM Z; L1CC is the community cloud used in earlier datathons (the 2026 arrangements are UNVERIFIED).
- **Ground truth overlay:** labels shown in the demo only to compare with what the system found; never fed to the engine.

### 23.3 Anticipated judge questions and honest answers

1. **Why not a graph neural network?** Heavy numerical libraries have no s390x wheels for current releases, latency and explainability matter, and our handcrafted graph features capture the signals we care about. We did not test a GNN, so we make no claim about it.
2. **Is the data realistic?** No. ChainWorld is a controlled simulator with realistic confusers (merchants, traders, agents, salary payers, family hubs, split bills, charity collections). We calibrated its difficulty with the weak and strong baselines only, froze it before looking at G2, and show sensitivity in the robustness grid. We make no claim about real UPI.
3. **How do you know the gain is not an artifact of your simulator?** We cannot fully rule it out. We use a strong baseline, pre-registered expectations, ablations, a robustness grid, leakage checks (including an ID-only model and a label-shuffle control) and a public-dataset cross-check at transaction level. The residual risk is that the simulator's ring behaviour resembles our features; the data card says so.
4. **Would this work on real UPI traffic?** Unknown. It would need access through a bank or payment provider, different signals, tuning, and regulatory approval. We claim nothing about real traffic.
5. **How does it compare with RBI's MuleHunter.AI?** We do not claim to beat it and have not compared against it; public information about it is limited [6] [7].
6. **What about false alarms?** We report false-alarm rates at matched levels, who bears them (account-level rates by archetype), and the effect of ring escalation. Every action is a recommendation reviewed by a person; entry accounts may be tricked people.
7. **Why LinuxONE?** We deployed the same pure-Python service and JSON model on big-endian s390x and report parity and measured latency and throughput next to a laptop. We make no claim that one platform is better.
8. **Isn't pure Python too slow?** We measured it (see the benchmark table). The work per event is dictionary and deque operations plus a few hundred tree evaluations. The engine is a single stateful process; scaling beyond that needs a partitioning design we did not build (§15.6).
9. **Privacy?** Only synthetic data; opaque ids; no device, IP or location signals; no demographic attributes.
10. **Can criminals evade it?** Yes, to some extent: the `slow_roll` typology is designed to evade short windows and we report how detection falls. Real adversaries adapt; retraining and monitoring would be needed.
11. **Why FIFO matching?** It is a simple, auditable convention for tracing money. Other conventions (proportional allocation) were not tested.
12. **What next?** A feedback loop from analysts, evaluation on real or partner data under agreements, a graph store and sharding, and extra signals where lawful.

### 23.4 Open questions to resolve at kickoff

Track and problem statements; whether an organiser dataset exists and its licence; judging criteria and weights; submission format and deadline; whether LinuxONE use is mandatory and how it is provided; rules on pre-built code, libraries and external data; team size; where the demo is judged (online or in person); size limits for video and deck.

### 23.5 Command cheat sheet

```bash
make setup-train                     # train env
make world PRESET=small SEEDS="0"    # quick world
make test                            # fast tests
make models PRESET=small             # features, train, thresholds, verify
make serve                           # API + dashboard on :8000
make setup-serve && make test-serve  # prove the serving path is pure Python
make docker-build docker-run         # container
make bench-engine HOST_TAG=laptop    # latency and throughput
make evaluate PRESET=medium          # 5 seeds, long job (nohup it)
make eval-all                        # everything, then report and claims check
```

### 23.6 Version of this file

Version 1.0, written 16 October 2026 (IST), before the IBM Z Datathon 2026 kickoff. Facts marked VERIFIED were checked on that date; everything marked UNVERIFIED must be checked before it is relied on. When this file changes during the build, update this line with the date and a one-line reason.
