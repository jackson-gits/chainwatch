# ChainWatch data card

## ChainWorld (primary evaluation data)

ChainWorld is the deterministic simulator in `chainwatch/data/world/`: synthetic payment
graphs generated from `configs/default.toml` and the world presets
(`configs/world_<preset>.toml`), each world containing archetypal user behaviour, a
flow-regime cycle, and planted layering rings with full ground truth (ring membership, hop
path, cash-out hop, taint). `make eval-all` evaluates
{{ summary.n_worlds | int }} such worlds; the same seed reproduces the same worlds, so the
dataset is auditable line by line in code rather than distributed as a binary.

- Content: accounts, payments, cash-outs, ring annotations. Fully synthetic — no real
  customer, no real identifier, no exported ledger.
- Split discipline: thresholds and calibration are fixed on validation worlds before the
  test worlds are scored (stated in every generated ChainWorld table caption), and the
  leakage battery (`make leakage`) re-runs evaluation with suspicious columns removed or
  shuffled to prove the labels are not leaky.
- Known gaps: typologies are simplified versions of documented layering patterns
  (fan-in/fan-out, chain, fan-in-chain, cross-entity hops, cash-out variants); adversary
  adaptation is probed only along the robustness grid's axes. Distribution shift to a real
  ledger is unmeasured by construction.

## Calibration data

Threshold calibration is validation-only: base thresholds are bisected on validation worlds
until the realised false-alarm rate after ring escalation matches the target
(`thresholds.calibrate_with_rings` and `thresholds.bisect_iters` in the config), and the test
worlds are then scored once at that fixed operating point. `make calibrate` runs the
world-difficulty check with the B0/B1 variants only — never G2, never the test split — so the
operating point is not fitted to the ring results reported in the model card.

## PaySim (optional external sanity profile)

PaySim is a public, unlabelled mobile-payment dataset used only for score-distribution and
alert-rate sanity checks (`scripts/profile_paysim.py`, run by `make paysim`, writing
`results/paysim_profile.json`). It carries no mule labels and no ground-truth rings, so it
supports no recall or precision claim anywhere in this repo.

Graceful degradation: the repo works without PaySim. If the profile has not been
generated — CSV not downloaded, pandas not installed, or `make paysim` simply not run —
`results/paysim_profile.json` records `status: missing` and every PaySim section renders
as a one-line "not generated in this run" note instead of numbers (spec §20.4). No
template in this repo dereferences a PaySim key that the profile might not contain.

## Licensing and redistribution

- ChainWorld: generated code, MIT like the rest of the repo; no licence obligation.
- PaySim: distributed by its original publishers under its own terms; this repo never
  bundles the CSV, only the profile script that summarises it locally.
