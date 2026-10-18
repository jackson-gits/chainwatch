# Decision log

Format: date (IST), decision, reason. Newest entries at the bottom of each section.

## Process

- **2026-10-16 18:05.** The build started before the kickoff window (Oct 17, 09:00 IST). CLAUDE.md §19.1 / rule P3
  says to wait unless the Official Rules allow pre-work; that was flagged to the owner, who answered "i want you do
  it in one go". The rules page was **not** confirmed to allow pre-work (UNVERIFIED). Risk accepted by the owner.
- **2026-10-16 18:05.** §0.1 answers: team (names to be added; owner Jackson Raj A, GitHub jackson-gits), LinuxONE
  access not yet available, licence MIT, keep the repo name `upi-mule-ring-detection` (package stays
  `chainwatch`), participation online (default), no kickoff information yet (default).

## Configuration additions (beyond the spec's `default.toml`)

- `world.campaign_start_min_day` (= `eval.burn_in_days`) and `world.campaign_end_margin_days = 1.0`: campaigns start in
  `[burn_in, days - margin)`, `slow_roll` 1.5 days earlier. Reason: rings that start in the last hours would be cut
  off by the end of the simulation and violate the invariant "every ring has at least 2 hops".
- `world.delay_scale` (robustness R5), `world.mule_mix` (R4), `family_hub_share`, `new_customer_share`,
  `dormant_customer_share`, `split_bill_rate`, `n_charity`, `force_typologies` (demo world): spec values made explicit.
- `model.min_train_positives = 200` (spec value) so tests can lower it for the tiny world.
- `rings.escalate_linking_only = true`: see "Interpretations".
- `eval.bootstrap_seed = 2026`: the spec asks for a fixed bootstrap seed in config.

## Interpretations of the spec

- **Escalation only for linking members.** §11.5 escalates transactions touching "a member" of an alerting ring.
  Exit accounts reached only by terminal edges (cash-out agents, merchants) are members too; escalating them would
  hold every customer cash-out at that agent. We escalate only accounts that *link* edges (senders of flagged edges
  and receivers of non-terminal flagged edges, never hubs). Config switch `rings.escalate_linking_only`.
- **Ring fragments after a rebuild** inherit the visible/alert state of the rings they came from (no duplicate alert
  events when an old edge expires and a ring splits).
- **Ring money fields:** `inflow` = flagged edges sent by entry accounts; `outflow` = flagged edges received by exit
  accounts; `exit_amount` = flagged edges of a terminal type.
- **`d_first_credit`** uses a has-credit flag in the never-evicted light registry (a 4th field next to first_seen,
  last_seen, susp_until), so it means "never received a credit" over the whole stream.
- **Registry hash** covers the ordered feature names plus every `[features]` value except `max_lateness_s` and
  `priority` (priority already changes the names), plus `project.tz_offset_s` (used by `t_hour`). This lets the HTTP
  load test raise `max_lateness_s` without a model/registry mismatch.
- **Hub test in the feature engine** uses the read-phase counts: `s_out_uniq_1h (+cur) + s_in_uniq_1h` for the sender,
  `d_in_uniq_1h (+cur) + d_out_uniq_1h` for the receiver.
- **Neighbour scans** skip the excluded account (D for `g_nbr_out_susp_cnt`, S for `g_dst_nbr_in_susp_cnt`) without
  counting it towards `nbr_scan_cap`.
- **Window scans for the receiver** stop at 1 h (all receiver features use windows of at most 1 h); same results as a
  24 h scan, cheaper.
- **Heavy-state eviction** also resets the last-20 debit amounts (`s_amt_vs_median`), a memory longer than the TTL.
- **Feature matrices** also store `{split}_src_role.npy` / `{split}_dst_role.npy` (int8) for ring-level evaluation.
- **Archetype false-alert rates** count an account when it is sender *or* receiver of a legitimate transaction whose
  final tier is verify or hold; mule accounts are excluded.

## ChainWorld design (before any world was frozen)

- **Money pools with "drain".** Traders, merchants and family hubs forward a fraction of the money received since
  their previous forward; the rest becomes their own money. Without it traders passed on about 99% (spec: 0.6 to 0.95).
- **New-mule "first seen" event** is a small CASH_IN from an agent 0 to 6 h before the first mule credit (label 0).
- **Sink cash-outs** are rounded down to a multiple of 100 with probability 0.6 (cash at an agent), part of the
  typology design.
- **Payroll schedule (2026-10-16 18:55).** A smoke test on a small scratch world (B0, B1 and G2 trained on it; not an
  evaluation world) showed the test false-alarm rate of every history-based detector at 3 to 4 times the validation
  target (B1: 2.1% versus 0.5%). 1294 of 1552 false alarms were salary payments on day 29: in a 30-day world the
  monthly bursts fell only on day 0 (burn-in, never trained on) and day 29 (test). Fix: every payer pays allowances
  on day-of-month 1 and salary on the last day (literal reading of §8.2.1), and half of the payers also run weekly
  payouts to 30 to 70% of their payees (gig-platform style), so payroll fan-out exists in the training window. After
  the fix the realised test FPRs of the scratch models were 0.42% to 0.68%. Motivation: an artifact that affected all
  detectors; not G2's performance. **Transparency note:** these smoke tests printed G2 numbers on the small scratch
  world. No world parameter has been or will be tuned on G2 numbers; difficulty calibration uses B0 and B1 only.
- **Fraud-share band.** `fraud_target_share` is checked (and reported in `world_report.json`) for every world, but the
  spec's small and tiny presets are denser by construction (40 campaigns for 6,000 customers), so the band is enforced
  for the medium and large presets; tests of the tiny world check `0 < share < 0.05`.

## Test suite and build infrastructure

- **2026-10-18 00:10 IST. `tests/fixtures/mini_models/` is committed.** Spec §17.1 requires serving tests to run with
  only `requirements-serve.txt` + pytest + httpx, but the tiny models cannot be trained there (no LightGBM). The tiny
  pipeline (world seed 7, 40 trees) was run once on the laptop and its JSON model package (~60 KB) is committed;
  serve tests load it, and `tests/conftest.py` still trains fresh models in the train env so the training path is
  exercised. The package was built with the exact `tests/tiny.py` config, so the registry-hash check in
  `ScoringEngine` passes unchanged in both environments.
- **2026-10-18 00:10 IST. `tests/conftest.py` keeps heavy imports inside fixture bodies.** pytest imports the root
  conftest for `tests/serving/` too; a module-level numpy/LightGBM import would break the serve-only environment.
- **2026-10-18 00:10 IST. `tests/_reference_features.py` scopes the engine-vs-reference equivalence test to the P0
  feature set** (the catalogue in §17.2 assumed a reference module that was never built). P1 features are covered by
  the causality and edge-case tests; the write-up must say so.
- **2026-10-18 00:10 IST. Lint fixes: `zip(..., strict=False)` everywhere B905 fired** rather than `strict=True`,
  to preserve behaviour exactly; the 79 long lines were re-flowed by hand with identical text.

## Evaluation audit follow-up

- **2026-10-18. L3 control correction.** The shuffled-label model previously selected its iteration using real validation labels.
  It now trains for a fixed 200 rounds and evaluates validation once. On small seeds 0 and 1, shuffled-label PR-AUC is
  0.0092 and 0.0058 against respective 2x-prevalence limits of 0.0109; both pass. Medium evaluation remains pending.
- **2026-10-18. L2 remains a failure.** The ID-only model PR-AUC is 0.1755 / 0.0301 (limit 0.0109) on small seeds 0 / 1.
  Numeric ID versus mule-status AUC is 0.518 / 0.539; 99.6% / 99.5% of validation events have both endpoint IDs
  present in training. Remapping validation IDs to unseen random values drops PR-AUC to 0.0092 / 0.0083, within the
  respective 0.0109 limits. This supports temporal identity memorization rather than a simple ID-range encoding. Keep
  preregistered temporal L2 marked failed; raw IDs remain excluded from detector features, and report remapped-ID
  sensitivity separately rather than relabeling L2 as passed.

## Pre-registered expectations (spec 14.10), written before the first test-split run on an evaluation world

Recorded 2026-10-16 ~19:00 IST. Resolved against the five-seed medium summary after held-out scoring; the machine-readable outcomes are in `results/expectations.json`.

1. `B1` is much better than `B0` on transaction-level recall at matched FPR (account history matters).
2. `G2+R` catches more rings than `B1+R` at matched FPR, and catches them earlier (hops remaining and minutes before
   exit). This is the central hypothesis and may fail.
3. `slow_roll` is the hardest typology for every variant.
4. Agents and traders carry the highest account-level false-alert rates.
5. On PaySim, per-transaction models score very high mostly through sender balance fields, and graph features add
   little because accounts rarely repeat.
6. The ring layer alone (`B0+R`) cannot rescue a weak scorer.

**Resolution:** E1 confirmed (B1 deployed transaction recall 96% vs B0 33%); E2 refuted (G2+R tied B1+R on ring recall and catch time); E3 refuted (slow_roll was not the weakest typology for each variant); E4 partly confirmed (traders and salary payers had the largest G2+R account-level false-alert shares; agents were not in the top two); E5 not tested (PaySim data is absent); E6 confirmed (B0+R had the same ring recall as B0).

## Medium evaluation calibration and freeze

- **2026-10-18. Reserved-seed calibration completed before medium test evaluation.** Using the unchanged
  `medium` preset (noise 2.0, hard-negative boost 3.0, default mule mix) on calibration seed 1003,
  fraud share was 0.001268 (inside the 0.0008–0.004 band), while B1 validation recall at 1% FPR was
  0.9545 (above the 0.4–0.9 target). Across the 20 recorded calibration runs, none met both target
  conditions. We retain the already-declared medium preset and proceed to measure seeds 0–4, recording
  the difficulty-calibration miss as a limitation. No test result informed this decision. Calibration
  output: `results/calibration_medium_final_seed1003.json`.
- **Before scoring any medium test split:** generate seeds 1–4 with the unchanged preset, retain the
  existing seed-0 world whose config hash matches that preset, and freeze event-file hashes for all five
  worlds in `results/world_manifest.json`. Do not regenerate or retune these worlds after test scoring.

## Medium evaluation results

- **2026-10-18. Five-seed held-out evaluation completed.** Seeds 0–4 were scored with the unchanged,
  hash-frozen `medium` preset. Across 250 test rings, G2+R caught 250 (100%; bootstrap 95% CI 100–100%),
  matching B1+R at 250/250 and exceeding B0 at 224/250 (89.7%). G2+R caught 249/250 rings before cash-out
  (99.6%; 95% CI 98.8–100%), with pooled median catch time 5.25 minutes and median two hops remaining.
  The central expectation that G2+R would beat B1+R was refuted: paired ring-recall and catch-time
  differences were both zero. Results are synthetic and do not establish performance on bank traffic.
- **Operating-point caveat:** thresholds were fixed from validation at 0.5% FPR. G2+R's realised test FPR
  ranged from 0.483% to 1.619% across seeds (mean 0.904%); the generated claim marks the off-target
  variants/worlds explicitly. Do not describe the test results as consistently meeting the target.
- **Leakage controls:** L1 and release-blocking L7–L9 passed on all five worlds; L3 also passed all five.
  L2 (ID-only model) failed on seeds 1, 3, and 4, which remains a non-blocking bug signal. The unseen-ID
  remapping control passed on all seeds, and numeric account-ID assignment was near chance. The L2 failures
  are reported rather than relabelled as passes.
- **Calibration limitation retained:** reserved seed 1003 had B1 validation recall 0.9545 at 1% FPR,
  above the preregistered 0.4–0.9 band. The preset was not retuned after test scoring.
- **Ablation follow-up:** A1–A5 completed on the same five frozen worlds. The retrained G2 feature-drop variants
  retained 100% ring recall in this sample, but some ring-aware variants had higher test FPR than the 0.5%
  validation target. These are ceiling-limited synthetic results; see `results/ablation_summary.json` for
  each ablation's transaction, ring, and operating-point metrics.

## Robustness grid

- **2026-10-18. R1–R6 completed.** The 28 cell/variant summaries use small synthetic worlds with seeds 100 and 101,
  scored by the medium seed-0 package at its validation-fixed thresholds. R6 retrained on a medium world with
  `round_trip` removed from training, then tested on two small worlds containing that typology. All groups had
  100% ring recall; G2+R cell-mean deployed transaction recall ranged from 93.2% to 98.7%, lowest with 30%
  `slow_roll`. Across individual seed rows, deployed recall ranged from 92.2% to 99.1% and FPR from 0.395% to
  0.717% across both variants. R6 `round_trip` recall was 100% for both B1+R and G2+R, matching the reference
  package. The two-seed, synthetic grid is a sensitivity check, not evidence of real-world robustness. Results:
  `results/robustness.json`; generated table: `reports/REPORT.md`.
- **Scope note:** Medium A6–A10 were not repeated. Existing small-world A1–A10 per-world files remain, while
  `results/ablation_summary.json` is the five-seed medium A1–A5 aggregate.
