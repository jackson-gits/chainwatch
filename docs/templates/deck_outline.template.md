# ChainWatch — deck outline (speaker notes + slide content)

Eleven slides, ~12 minutes plus questions. Every measured number is a placeholder rendered
by `make report` from `results/`; slide copy with no placeholder is framing, not evidence.
Hand-typed background figures on slide 2 carry their source on the slide.

## Slide 1 — Title

**ChainWatch: offline evaluation harness for UPI mule-network detection**

- Scores payment events for layering-ring evidence; alerts on rings, not single accounts.
- One-line honesty tag on the title slide: simulator evaluation on ChainWorld plus an
  unlabelled PaySim sanity profile — not a production system, not a real-bank accuracy
  claim.

## Slide 2 — Problem (sourced background figures)

- Scale of the problem (sourced): UPI processed about 24.07 billion transactions worth about
  Rs 29.37 lakh crore in September 2026 — roughly 802 million a day, volume up about 23%
  year on year [1]; January to June 2026 saw more than 12.71 lakh cyber-fraud complaints
  reporting about Rs 10,178.97 crore lost, with only about Rs 2,968.85 crore held or frozen
  — roughly 29% of the reported loss [2] [3]. Money hops through borrowed mule accounts
  within minutes, so payment-by-payment checks miss the chain.
- Mule-account reporting (sourced): accounts are used as mules within minutes of a scam and
  are hard to distinguish from ordinary high-turnover accounts [4] [5].
- Public reporting on RBI's MuleHunter.AI [6] [7] describes a graph-based detection
  approach; we cite it for context only. We do not claim parity with MuleHunter and have
  not compared implementations, datasets or thresholds.

## Slide 3 — Data and scale

- ChainWorld: {{ summary.n_worlds | int }} deterministic synthetic payment worlds of
  {{ cfg.world.days | int }} simulated days each — {{ cfg.world.n_customers | int }}
  customers, {{ cfg.world.n_merchants | int }} merchants, {{ cfg.world.n_agents | int }}
  cash-out agents, {{ cfg.world.n_campaigns | int }} planted mule campaigns with known
  ground truth (generated from `configs/default.toml` presets).
- Detector: 59 features prioritised as {{ cfg.features.priority | raw }}, 13 hard rules,
  exported trees capped at {{ cfg.model.ship_max_trees | int }} with depth
  {{ cfg.model.max_depth | int }}.
- PaySim: optional external sanity profile (`scripts/profile_paysim.py`); unlabelled, so
  score-distribution checks only — never a recall number.

## Slide 4 — Protocol

- Detector runs blind; the ring matcher needs {{ cfg.rings.min_accounts | int }} accounts
  and {{ cfg.rings.min_flagged_edges | int }} flagged edges per ring (flagged edges linked
  within {{ cfg.rings.link_window_s | int }} s), and a ring is caught when
  {{ cfg.eval.catch_min_accounts | int }} of its accounts carry at least a
  {{ cfg.eval.catch_min_action | raw }} tier action.
- Target {{ summary.fpr_target | pct1 }} of legitimate test transactions at the verify tier
  ({{ cfg.thresholds.fpr_hold | pct1 }} at hold), at most
  {{ cfg.thresholds.max_false_rings_per_day | int }} false rings per day on validation;
  every rate carries a {{ cfg.eval.bootstrap | int }}-replicate bootstrap CI.
- Pre-registered expectations E1-E6 (spec §14) are scored automatically into
  `results/expectations.json`; the report shows pass/fail, not just numbers.

## Slide 5 — Headline result

- Evaluated on {{ summary.n_worlds | int }} ChainWorld worlds at the
  {{ summary.fpr_target | pct1 }} verify-tier target false-alarm rate.
- {{ summary.claim.headline | raw }}

## Slide 6 — Results detail

{{ table.main }}

- Strongest single-rule baseline comparison: {{ summary.claim.vs_strong_baseline | raw }}
- Time to catch: median {{ summary.variants.G2+R.pooled.ttc_median_min | min1 }} min from
  first illicit deposit {{ summary.variants.G2+R.pooled.ttc_median_ci | ci_min }}.
- {{ summary.variants.G2+R.pooled.hops_remaining_median | num0 }} hops median remaining
  before cash-out; {{ summary.variants.G2+R.pooled.before_exit | pct0 }} of rings alerted
  before any exit hop.

## Slide 7 — Where recall comes from (per typology)

{{ table.typology }}

- Speaker note: read the weakest row of this table aloud — thin hop evidence by alert time
  is the hard case; say it before the audience finds it.

## Slide 8 — Ablations, robustness, leakage

{{ table.ablation }}

{{ table.robustness }}

{{ table.leakage }}

- Speaker note: one sentence each — ablations show the model earns its complexity;
  robustness shows the grid it survives; leakage shows the numbers are not an artefact of
  the removed columns.

## Slide 9 — Limitations

- Simulated worlds, simplified typologies; no real-bank ground truth in the repo.
- Confidence intervals describe simulator sampling variability, not real-world transfer.
- Alerts are ring-level; investigator tooling is out of scope.
- Benchmarks/parity/PaySim numbers live in their own artifacts (next slide), not here.

## Slide 10 — Benchmarks, parity, PaySim (and the comparison question)

- Engine benchmarks: `make bench-engine HOST_TAG=<tag>` → `results/bench_engine_<tag>.json`;
  HTTP load test (server running): `make bench-load HOST_TAG=<tag>` →
  `results/bench_load_<tag>.json`; machine record: `make platform-info HOST_TAG=<tag>` →
  `results/platform_<tag>.json`.
- Cross-machine parity: `make parity HOST_TAG=<tag>` → `results/parity_<tag>.jsonl` (one line
  per event; compare two machines with `chainwatch parity --compare a.jsonl b.jsonl`).
- PaySim profile: `make paysim` → `results/paysim_profile.json` (or `status: missing`).
- If asked about MuleHunter: we do not claim parity with MuleHunter; we have not compared
  implementations or datasets, and the baseline variant in our tables is our own rule set.

## Slide 11 — Reproduce and roadmap

- Reproduce: `make setup world data train calibrate eval-all`; verify with
  `make check-claims` (re-checks every rendered claim against `results/`).
- Roadmap: real-ledger feature mapping (out of repo), investigator workflow, deeper
  cash-out typologies in the simulator — framed as planned work, not measured results.
