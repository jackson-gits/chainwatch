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

Recorded 2026-10-16 ~19:00 IST. To be resolved as confirmed / partly confirmed / refuted with the numbers.

1. `B1` is much better than `B0` on transaction-level recall at matched FPR (account history matters).
2. `G2+R` catches more rings than `B1+R` at matched FPR, and catches them earlier (hops remaining and minutes before
   exit). This is the central hypothesis and may fail.
3. `slow_roll` is the hardest typology for every variant.
4. Agents and traders carry the highest account-level false-alert rates.
5. On PaySim, per-transaction models score very high mostly through sender balance fields, and graph features add
   little because accounts rarely repeat.
6. The ring layer alone (`B0+R`) cannot rescue a weak scorer.
