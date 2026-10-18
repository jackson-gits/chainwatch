# ChainWatch status

Last updated: 2026-10-18
Current phase: ChainWorld evaluation complete; external validation and platform evidence remain.

## Evaluation complete

- Five hash-frozen `medium` ChainWorld worlds (seeds 0-4) were evaluated on their held-out time splits.
- Medium A1-A5 ablations completed across all five worlds. `results/ablation_summary.json` is the medium A1-A5 aggregate; per-world files are in `results/`.
- The full R1-R6 robustness grid completed on small synthetic worlds, seeds 100 and 101, using the medium seed-0 model package and validation-fixed thresholds; R6 retrained on a medium world without `round_trip`.
- All 28 robustness cell/variant groups had 100% ring recall in these two-seed samples. Across cell means, G2+R deployed transaction recall ranged from 93.2% to 98.7% (lowest at 30% `slow_roll` mix, R3); the seed-level range was 92.2% to 99.1%. Seed-level FPR ranged from 0.395% to 0.717% across both variants. R6 held-out `round_trip` recall was 100% for both B1+R and G2+R, matching the reference package. See `results/robustness.json` and the generated table in `reports/REPORT.md`; these small synthetic samples do not establish real-world robustness.
- Leakage checks L1-L9 completed across all five medium worlds. Every release-blocking check passed. L2 (ID-only model) failed on seeds 1, 3, and 4 as a non-blocking bug signal; unseen-ID remapping passed on all five.
- The generated evaluation report and claim checker are current: `reports/REPORT.md`, `results/expectations.json`; `chainwatch check-claims` passed after report generation.
- The calibration miss is retained: reserved seed 1003 produced B1 validation recall 0.9545 at 1% FPR, above the preregistered 0.4-0.9 band. The medium preset was not changed after held-out scoring.

## Main medium results

- G2+R caught 250 of 250 test rings across the five synthetic worlds (100%, bootstrap 95% CI 100%-100%). B1+R also caught 250/250; B0 caught 224/250 (89.7%). The preregistered expectation that G2+R would beat B1+R was refuted: paired recall and catch-time differences were zero.
- G2+R caught 249/250 rings before cash-out (99.6%; 95% CI 98.8%-100%), with pooled median catch time 5.25 minutes and median two hops remaining.
- Thresholds were fixed on validation for a 0.5% false-positive target. G2+R's realised test FPR ranged from 0.483% to 1.619% (mean 0.904%), so some worlds were off target. The report marks those operating-point deviations.
- E1 was confirmed; E2 and E3 were refuted; E4 was partly confirmed; E5 was not tested; E6 was confirmed. See `results/expectations.json` for the resolved details.
- These are synthetic ChainWorld results and do not establish effectiveness on production payment traffic.

## Platform and data status

- Local laptop engine and HTTP load measurements are recorded in `results/bench_*_laptop.json` and described in `docs/linuxone_runbook.md`.
- `results/platform_qemu-s390x.json` and the QEMU parity artifacts document an s390x emulation rehearsal. They do not establish a physical IBM LinuxONE platform record or native hardware performance.
- Physical LinuxONE access is still needed to record the platform, run native parity, and measure engine and HTTP performance.
- PaySim remains untested because the CSV dataset is not present; `results/paysim_profile.json` is a profile artifact, not an evaluation result.
- The demo script exists, but no recorded three-minute demo video was found in the project artifacts.

## Other evaluation artifacts

- Prior small-world results are retained as per-world files. The current aggregate `results/ablation_summary.json` reflects the completed five-seed medium A1-A5 run.
- Medium A6-A10 were not repeated; the existing small-world A1-A10 per-world files are retained but are not the current aggregate.
- `results/world_manifest.json` records frozen event hashes for medium seeds 0-4. Do not regenerate these worlds after test scoring.
- `docs/decisions.md` contains the calibration decision, held-out results, leakage interpretation, and resolved preregistered expectations.

## Reproduction

```powershell
python -m chainwatch eval-all --preset medium --seeds 0 1 2 3 4 --skip-existing --jobs 2
python -m chainwatch ablate --preset medium --seeds 0 1 2 3 4 --ids A1,A2,A3,A4,A5 --jobs 2
python -m chainwatch robustness --models models/eval/medium_seed0 --jobs 2
python -m chainwatch report --no-figures
python -m chainwatch check-claims
```
